"""session_graph - the directed graph of one Claude Code session, from the IDs its records carry.

Works on a Session: the main log's records, and when the session has them, each agent's transcript
records and meta, the workflow summaries and the workflow journals, all in their original order. It
reads no files, so the records can come from the session's files or from an archive of them.
Nothing is guessed from text except the completion notifications' task id and status; every edge
comes from an ID in the logs:

  launch    an Agent or Workflow tool call to what it started: an agent's meta toolUseId, or its
            parentAgentId for nesting; a Workflow result's runId and taskId
  contains  run > phase > unit (a logical agent: attempts sharing a journal key) > attempt
  executes  a workflow instance to its run
  ran       a workflow instance to the attempts that started under it
  resume    an instance to the instance that resumed it (the launch's resumeFromRunId)
  result    a completion notification back to the launcher
  phase_order  phase i to phase i+1: grouping, not dependency (workflows are pipelined)

Node kinds: main, agent, workflow (one instance = one launch = one taskId), run (the definition),
phase, unit, attempt, launch_error (a Workflow launch rejected before it ran).

A resume reuses the runId, and so the transcript folder and journal, but gets a new taskId, and the
workflow JSON keeps only the last instance's. An attempt belongs to the last instance launched at
or before it started. Stdlib only.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime


class GraphError(Exception):
    pass


def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S") if ts else None


class Session:
    """One session's records, in their original order.

    main         records                       the main log
    metas        {agent id: meta}              each agent's .meta.json, plus "workflow_dir": the
                                               wf_<id> folder it sat in, or None for a direct agent
    transcripts  {agent id: records}           each agent's transcript

    "records" is anything that can be iterated more than once (a list, or bundle.SourceRecords, which
    parses records from an archive each time), so a large session need not be held in memory.
    workflow_runs {run id: summary}            workflows/wf_<id>.json
    journals     {run id: [row]}               subagents/workflows/wf_<id>/journal.jsonl
    """

    def __init__(self, main, metas=None, transcripts=None, workflow_runs=None, journals=None):
        self.main = main
        self.metas = metas or {}
        self.transcripts = transcripts or {}
        self.workflow_runs = workflow_runs or {}
        self.journals = journals or {}
        missing = sorted(set(self.metas) - set(self.transcripts))
        if missing:
            raise GraphError(f"agents with a meta file but no transcript: {', '.join(missing[:5])}")


def ending_cause(record):
    """Why an agent stopped, when its final record says so: an API error status, a timeout."""
    text = json.dumps(record, ensure_ascii=False) if record else ""
    m = re.search(r"API Error: (\d{3})", text)
    if m:
        return "api-" + m.group(1)
    if re.search(r"timed out", text, re.I):
        return "timeout"
    return None


def transcript_span(records):
    """(first ts, last ts, records, tool calls, errors, ending cause) of an agent transcript. Reads the
    records once, so they can be a stream."""
    first = last = final = None
    count = calls = errors = 0
    for r in records:
        count += 1
        final = r
        t = parse_ts(r.get("timestamp"))
        if t:
            first = t if first is None or t < first else first
            last = t if last is None or t > last else last
        m = r.get("message")
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    calls += b.get("type") == "tool_use"
                    errors += bool(b.get("is_error"))
    return first, last, count, calls, errors, ending_cause(final)


class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.unlaunched = []          # workflow runs with no Workflow launch in any log

    def node(self, nid, kind, **attrs):
        self.nodes[nid] = {"id": nid, "kind": kind, **attrs}
        return self.nodes[nid]

    def edge(self, src, dst, kind, at=None, **attrs):
        self.edges.append({"from": src, "to": dst, "kind": kind, "at": iso(at) if at else None, **attrs})


def _texts(r):
    """The plain text a record carries: a string message, its text blocks, and an attachment's prompt."""
    m = r.get("message")
    texts = []
    if isinstance(m, dict):
        if isinstance(m.get("content"), str):
            texts.append(m["content"])
        elif isinstance(m.get("content"), list):
            texts += [b["text"] for b in m["content"] if isinstance(b, dict) and isinstance(b.get("text"), str)]
    at = r.get("attachment")
    if isinstance(at, dict) and isinstance(at.get("prompt"), str):
        texts.append(at["prompt"])
    return texts


def _scan_main(records, g, launches, notified):
    """Launches, their results and the completion notifications in the main log."""
    first = last = None
    prompts = []
    for r in records:
        t = parse_ts(r.get("timestamp"))
        if t:
            first = first or t
            last = t if last is None or t > last else last
        m = r.get("message")
        if r.get("type") == "assistant" and isinstance(m, dict):
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Workflow"):
                    launches[b["id"]] = {"at": t, "name": b["name"], "input": b.get("input") or {}, "owner": "main"}
        if r.get("type") == "user" and isinstance(m, dict):
            if isinstance(m.get("content"), list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in launches:
                        _record_result(launches[b["tool_use_id"]], r.get("toolUseResult"), b)
            elif isinstance(m.get("content"), str) and not m["content"].lstrip().startswith("<"):
                prompts.append(t)
        for txt in _texts(r):
            _note(txt, t, notified)
    g.node("main", "main", start=iso(first), end=iso(last), prompts=[iso(p) for p in prompts if p])


def _record_result(launch, tool_use_result, block):
    launch["result"] = tool_use_result if isinstance(tool_use_result, dict) else {}
    if not isinstance(tool_use_result, dict):
        launch["error"] = re.sub(r"\s+", " ", str(block.get("content")))[:200]


def _note(text, t, notified):
    if "<task-notification>" not in text:
        return
    tid = re.search(r"<task-id>([^<]+)</task-id>", text)
    st = re.search(r"<status>([^<]+)</status>", text)
    if tid and t and tid.group(1) not in notified:
        notified[tid.group(1)] = (t, st.group(1) if st else None)


def _scan_agent_for_workflows(records, owner, launches, notified):
    """Workflow launches made from inside an agent transcript, with their results and any completion
    notification that lands there."""
    mine = set()
    for r in records:
        m = r.get("message")
        content = m.get("content") if isinstance(m, dict) else None
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Workflow":
                launches[b["id"]] = {"at": parse_ts(r.get("timestamp")), "name": "Workflow",
                                     "input": b.get("input") or {}, "owner": owner}
                mine.add(b["id"])
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in mine:
                _record_result(launches[b["tool_use_id"]], r.get("toolUseResult"), b)
        for txt in _texts(r):
            _note(txt, parse_ts(r.get("timestamp")), notified)


def build_graph(session):
    """The Graph of a Session."""
    g = Graph()
    launches, notified = {}, {}
    _scan_main(session.main, g, launches, notified)

    metas, spans = session.metas, {}
    for aid, m in metas.items():            # each transcript is read once, and held only while it is used
        records = list(session.transcripts[aid])
        spans[aid] = transcript_span(records)
        _scan_agent_for_workflows(records, f"attempt:{aid}" if m["workflow_dir"] else f"agent:{aid}", launches, notified)

    # direct and nested agents
    for aid, m in metas.items():
        if m["workflow_dir"]:
            continue
        first, last, records, calls, errors, cause = spans[aid]
        g.node(f"agent:{aid}", "agent", label=m.get("description"), agent_type=m.get("agentType"),
               depth=m.get("spawnDepth"), start=iso(first), end=iso(last), records=records, tool_calls=calls,
               errors=errors, cause=cause)
        if m.get("parentAgentId"):
            g.edge(f"agent:{m['parentAgentId']}", f"agent:{aid}", "launch", first, via="parentAgentId",
                   tool_use_id=m.get("toolUseId"))
        elif m.get("toolUseId") in launches:
            g.edge("main", f"agent:{aid}", "launch", launches[m["toolUseId"]]["at"], via="toolUseId",
                   tool_use_id=m["toolUseId"])
        if aid in notified:
            g.edge(f"agent:{aid}", "main", "result", notified[aid][0], status=notified[aid][1])

    # workflows: instances, runs, phases, logical agents, attempts
    wf_json = session.workflow_runs
    instances_of_run = defaultdict(list)
    for tid, launch in sorted(launches.items(), key=lambda kv: kv[1]["at"] or datetime.max.replace(tzinfo=None)):
        if launch["name"] != "Workflow":
            continue
        res = launch.get("result", {})
        if res.get("runId"):
            instances_of_run[res["runId"]].append({"tid": tid, "at": launch["at"], "task": res.get("taskId"),
                                                   "owner": launch["owner"],
                                                   "resumed_from": launch["input"].get("resumeFromRunId")})
        else:
            g.node(f"launch-error:{tid}", "launch_error", label="rejected Workflow launch", start=iso(launch["at"]),
                   end=iso(launch["at"]), error=launch.get("error"))
            g.edge(launch["owner"], f"launch-error:{tid}", "launch", launch["at"], via="toolUseId", tool_use_id=tid)

    instance_of_attempt = {}
    for run, d in sorted(wf_json.items()):
        journal = session.journals.get(run, [])
        attempts = [a for a, m in metas.items() if m["workflow_dir"] == run]
        instances = instances_of_run.get(run, [])
        if not instances:
            g.unlaunched.append(run)
            instances = [{"tid": None, "at": None, "task": d.get("taskId"), "owner": "main", "resumed_from": None}]
        ids = [f"wf:{run}" if k == 0 else f"wf:{run}~{k + 1}" for k in range(len(instances))]
        for a in attempts:
            st = spans[a][0]
            k = max((i for i, inst in enumerate(instances) if inst["at"] is None or st is None or inst["at"] <= st),
                    default=0)
            instance_of_attempt[a] = ids[k]
        for k, (nid, inst) in enumerate(zip(ids, instances)):
            mine = [a for a in attempts if instance_of_attempt[a] == nid]
            starts = [spans[a][0] for a in mine if spans[a][0]]
            ends = [spans[a][1] for a in mine if spans[a][1]]
            status = d.get("status") if k == len(instances) - 1 else notified.get(inst["task"], (None, None))[1]
            g.node(nid, "workflow", label=d.get("workflowName"), run_id=run, task_id=inst["task"], instance=k + 1,
                   resumed_from=inst["resumed_from"], status=status, attempts=len(mine),
                   start=iso(min(starts)) if starts else iso(inst["at"]),
                   end=iso(max(ends)) if ends else iso(inst["at"]))
            g.edge(nid, f"run:{run}", "executes")
            if inst["tid"]:
                g.edge(inst["owner"], nid, "launch", inst["at"], via="Workflow result runId", tool_use_id=inst["tid"])
            if inst["task"] in notified:
                g.edge(nid, inst["owner"], "result", notified[inst["task"]][0], status=notified[inst["task"]][1])
            if k:
                g.edge(ids[k - 1], nid, "resume", inst["at"], via="resumeFromRunId")
        logs = d.get("logs") or []
        inst_nodes = [g.nodes[i] for i in ids]
        g.node(f"run:{run}", "run", label=d.get("workflowName"), run_id=run, status=d.get("status"),
               instances=len(instances), agent_count=d.get("agentCount"), attempts=len(attempts),
               run_duration_ms=d.get("durationMs"), tokens=d.get("totalTokens"), tool_calls=d.get("totalToolCalls"),
               stall_lines=sum("[stall]" in l for l in logs), failure_lines=sum("failed" in l for l in logs),
               start=min((n["start"] for n in inst_nodes if n["start"]), default=None),
               end=max((n["end"] for n in inst_nodes if n["end"]), default=None))
        for a in attempts:
            g.edge(instance_of_attempt[a], f"attempt:{a}", "ran")
        phase_of = {}
        for e in d.get("workflowProgress") or []:
            if e.get("type") == "workflow_agent" and e.get("agentId"):
                phase_of[e["agentId"]] = (e.get("phaseIndex"), e.get("phaseTitle"), e.get("label"), e.get("state"))
        for i, p in enumerate(d.get("phases") or [], 1):
            g.node(f"phase:{run}:{i}", "phase", label=p.get("title"), index=i)
            g.edge(f"run:{run}", f"phase:{run}:{i}", "contains")
            if i > 1:
                g.edge(f"phase:{run}:{i-1}", f"phase:{run}:{i}", "phase_order", inferred=True)
        key_of = {j["agentId"]: j["key"] for j in journal if j.get("type") == "started"}
        final = {j["agentId"]: j["type"] for j in journal if j.get("type") in ("result", "failed")}
        units = defaultdict(list)
        for a in attempts:
            units[key_of.get(a, "no-key:" + a)].append(a)
        for key, group in units.items():
            group.sort(key=lambda a: spans[a][0].timestamp() if spans[a][0] else float("inf"))
            known = [phase_of[a] for a in group if a in phase_of]
            pi, pt, label, state = known[0] if known else (None, None, None, None)
            uid = f"unit:{run}:{key[3:11]}"
            first = min((spans[a][0] for a in group if spans[a][0]), default=None)
            last = max((spans[a][1] for a in group if spans[a][1]), default=None)
            stalls = sum(1 for l in logs if "[stall]" in l and label and f'agent "{label}"' in l)
            g.node(uid, "unit", label=label, phase=pt, state=state, attempts=len(group), start=iso(first),
                   end=iso(last), outcomes=[final.get(a) for a in group], stall_retries=stalls)
            g.edge(f"phase:{run}:{pi}" if pi else f"run:{run}", uid, "contains", phase_known=bool(pi))
            for a in group:
                f_, l_, records, calls, errors, cause = spans[a]
                g.node(f"attempt:{a}", "attempt", agent_type=metas[a].get("agentType"), start=iso(f_), end=iso(l_),
                       records=records, tool_calls=calls, errors=errors, outcome=final.get(a), cause=cause,
                       instance=instance_of_attempt[a])
                g.edge(uid, f"attempt:{a}", "contains")
    g.wf_json = wf_json
    return g


def check_graph(g):
    """Consistency facts about the graph as (name, value) pairs, and a list of problems. A problem is
    a broken invariant: a cycle, a node the main thread cannot reach, a child that starts before the
    call that launched it."""
    nodes, edges = g.nodes, g.edges
    problems = []
    fwd = [e for e in edges if e["kind"] != "result"]
    indeg = Counter(e["to"] for e in fwd)
    adj = defaultdict(list)
    for e in fwd:
        adj[e["from"]].append(e["to"])
    queue, ordered = [n for n in nodes if indeg[n] == 0], 0
    while queue:
        n = queue.pop()
        ordered += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if ordered != len(nodes):
        problems.append(f"the graph has a cycle ({ordered} of {len(nodes)} nodes can be ordered)")
    reach, stack = set(), ["main"]
    while stack:
        n = stack.pop()
        if n not in reach:
            reach.add(n)
            stack.extend(adj[n])
    unreachable = Counter(nodes[n]["kind"] for n in nodes if n not in reach)
    if unreachable:
        problems.append(f"nodes the main thread cannot reach: {dict(unreachable)}")
    early = total = 0
    for e in edges:
        if e["kind"] == "launch" and e["at"]:
            total += 1
            child = nodes[e["to"]]
            if child.get("start") and child["start"] < e["at"][:19]:
                early += 1
    if early:
        problems.append(f"{early} of {total} launch edges have a child that starts before its launch")
    overlap = pairs = 0
    for run, d in getattr(g, "wf_json", {}).items():
        by = defaultdict(list)
        for n in nodes.values():
            if n["kind"] == "unit" and n["id"].startswith(f"unit:{run}:") and n.get("phase"):
                by[n["phase"]].append(n)
        titles = [p.get("title") for p in d.get("phases") or []]
        for a, b in zip(titles, titles[1:]):
            if by.get(a) and by.get(b):
                pairs += 1
                if min(u["start"] for u in by[b]) < max(u["end"] for u in by[a]):
                    overlap += 1
    units = [n for n in nodes.values() if n["kind"] == "unit"]
    facts = [
        ("nodes", dict(Counter(n["kind"] for n in nodes.values()))),
        ("edges", dict(Counter(e["kind"] for e in edges))),
        ("launch_edges_checked", total),
        ("workflow_runs_without_a_launch", len(g.unlaunched)),
        ("rejected_launches", sum(1 for n in nodes.values() if n["kind"] == "launch_error")),
        ("resumed_instances", sum(1 for e in edges if e["kind"] == "resume")),
        ("phase_pairs_overlapping", f"{overlap} of {pairs}"),
        ("logical_agents", len(units)),
        ("logical_agents_retried", sum(1 for u in units if u["attempts"] > 1)),
    ]
    return facts, problems
