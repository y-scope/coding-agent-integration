"""bundle_build - turn one Claude Code session's files into a bundle: CLP archives plus a catalog.

  bundle/
    catalog.sqlite   the map: sources, archives, agents, graph nodes and edges, events (derived)
    archives/        one clp-s archives dir, one archive per kind of log, nothing else in it
    files/           what is not a JSON log: tool results, file snapshots, tasks, workflow scripts

Every file that belongs to the session is classified by its path, and a file that matches no rule stops
the build, so nothing is silently left out. The engine is reached only through the functions in
bundle.py. Stdlib only.
"""

import bisect
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time

import bundle as B
import session_graph as G

LAYOUT = 1

# kind, where it goes (an archive, the catalog, or plain files), and a regex on the path relative to
# the session directory (subagents/..., workflows/..., tool-results/...).
SESSION_RULES = [
    ("agent", "archive", r"^subagents/agent-[^/]+\.jsonl$"),
    ("agent-meta", "catalog", r"^subagents/agent-[^/]+\.meta\.json$"),
    ("workflow-agent", "archive", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.jsonl$"),
    ("workflow-agent-meta", "catalog", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.meta\.json$"),
    ("workflow-journal", "archive", r"^subagents/workflows/wf_[^/]+/journal\.jsonl$"),
    ("workflow-run", "archive", r"^workflows/wf_[^/]+\.json$"),
    ("workflow-script", "files", r"^workflows/scripts/[^/]+\.js$"),
    ("tool-result", "files", r"^tool-results/[^/]+$"),
]
ARCHIVED_KINDS = ["main", "agent", "workflow-agent", "workflow-journal", "workflow-run"]
EVENT_KINDS = ("main", "agent", "workflow-agent")

# A copy of the human-prompt rule in session_turns.py (a user record the harness wrote is not a prompt).
INJECTED = ("<task-notification", "<local-command", "<command-", "Caveat:", "This session is being continued",
            "[Request interrupted", "Continue from where you left off")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(main_path, claude_home):
    """[(path relative to the source root, full path, kind, where)] for every file of the session."""
    sid = os.path.basename(main_path)[:-len(".jsonl")]
    project_dir = os.path.dirname(os.path.abspath(main_path))
    root = os.path.join(project_dir, sid)
    source_root = claude_home or project_dir
    found = [(os.path.relpath(main_path, source_root), os.path.abspath(main_path), "main", "archive")]
    walks = []
    if os.path.isdir(root):
        walks.append((root, None))
    if claude_home:
        walks.append((os.path.join(claude_home, "tasks", sid), ("task", "files")))
        walks.append((os.path.join(claude_home, "file-history", sid), ("file-history", "files")))
    for base, fixed in walks:
        for dirpath, _, names in os.walk(base):
            for name in names:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, source_root)
                if fixed:
                    if os.path.dirname(os.path.relpath(full, base)):
                        raise B.BundleError(f"unclassified file (nested under {fixed[0]}): {rel}")
                    found.append((rel, full, *fixed))
                    continue
                inner = os.path.relpath(full, base)
                for kind, where, rx in SESSION_RULES:
                    if re.match(rx, inner):
                        found.append((rel, full, kind, where))
                        break
                else:
                    raise B.BundleError(f"unclassified file: {rel} (add a rule to bundle_build.SESSION_RULES or remove it)")
    return sid, sorted(found)


def _texts(message):
    c = message.get("content") if isinstance(message, dict) else None
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return []


def _event(kind, r):
    """(event row, tool rows) for a record with a uuid; the row is None when the record carries nothing
    to join on (hook and reminder attachments, system rows)."""
    uuid = r["uuid"]
    message = r.get("message") if isinstance(r.get("message"), dict) else {}
    texts = _texts(message)
    tur, attach = r.get("toolUseResult"), r.get("attachment")
    notif = " ".join(texts + ([attach["prompt"]] if isinstance(attach, dict) and isinstance(attach.get("prompt"), str) else []))
    task = re.search(r"<task-id>([^<]+)</task-id>", notif) if "<task-notification>" in notif else None
    tools, is_error = [], 0
    content = message.get("content")
    for blk in content if isinstance(content, list) else []:
        if not isinstance(blk, dict):
            continue
        if blk.get("type") == "tool_use":
            tools.append((blk.get("id"), "use", blk.get("name"), None))
        elif blk.get("type") == "tool_result":
            err = int(bool(blk.get("is_error")))
            is_error |= err
            tools.append((blk.get("tool_use_id"), "result", None, err))
    ts = r.get("timestamp")
    row = dict(uuid=uuid, kind=kind, agent_id=r.get("agentId"), ts=ts[:23] if isinstance(ts, str) else None,
               type=r.get("type"), turn=None,
               human=int(kind == "main" and r.get("type") == "user" and not r.get("isMeta") and not r.get("isCompactSummary")
                         and any(t.strip() and not t.lstrip().startswith(INJECTED) for t in texts)),
               interrupt=int(any(t.lstrip().startswith("[Request interrupted") for t in texts)), is_error=is_error,
               ref_agent_id=tur.get("agentId") if isinstance(tur, dict) and isinstance(tur.get("agentId"), str) else None,
               ref_task_id=task.group(1) if task else None)
    if r.get("type") in ("user", "assistant") or row["ref_agent_id"] or row["ref_task_id"]:
        return row, tools
    return None, tools


def _write_events(db, files_by_kind):
    """Events for the main log and the agent transcripts; returns {kind: (events, unlisted, no uuid)}."""
    counts = {}
    for kind in EVENT_KINDS:
        rows, unlisted, no_uuid = [], 0, 0
        for path in files_by_kind.get(kind, []):
            for r in G.read_jsonl(path):
                if not r.get("uuid"):
                    no_uuid += 1
                    continue
                row, tools = _event(kind, r)
                if row is None:
                    unlisted += 1
                else:
                    rows.append((row, tools))
        if kind == "main":  # a turn runs from one human prompt to the next
            prompts = sorted(r["ts"] for r, _ in rows if r["human"] and r["ts"])
            for r, _ in rows:
                r["turn"] = bisect.bisect_right(prompts, r["ts"]) if r["ts"] else None
        cols = ["uuid", "kind", "agent_id", "ts", "type", "turn", "human", "interrupt", "is_error", "ref_agent_id", "ref_task_id"]
        for r, tools in rows:
            cur = db.execute(f"INSERT INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [r[c] for c in cols])
            db.executemany("INSERT INTO event_tools VALUES(?,?,?,?,?)", [(cur.lastrowid, *t) for t in tools])
        counts[kind] = (len(rows), unlisted, no_uuid)
        db.execute("INSERT INTO bundle VALUES(?,?)", (f"events_unlisted_{kind}", str(unlisted)))
        db.execute("INSERT INTO bundle VALUES(?,?)", (f"events_skipped_{kind}", str(no_uuid)))
    return counts


def _attempt_states(g):
    """Status and cause of each attempt: ok or failed from the journal, else stalled-retried when a later
    attempt of the same logical agent exists, else unresolved."""
    by_unit = {}
    for e in g.edges:
        if e["kind"] == "contains" and e["to"].startswith("attempt:"):
            by_unit.setdefault(e["from"], []).append(e["to"])
    state = {}
    for atts in by_unit.values():
        for i, a in enumerate(atts):
            n = g.nodes[a]
            if n["outcome"] == "result":
                state[a] = ("ok", None)
            elif n["outcome"] == "failed":
                state[a] = ("failed", n["cause"])
            elif i < len(atts) - 1:
                state[a] = ("stalled-retried", "stall")
            else:
                state[a] = ("unresolved", None)
    return state


def _write_graph(db, g):
    agent_run = dict(db.execute("SELECT agent_id, run_id FROM agents"))
    agent_result = {e["from"]: e["status"] for e in g.edges if e["kind"] == "result" and e["from"].startswith("agent:")}
    states = _attempt_states(g)
    plain = {"id", "kind", "label", "start", "end", "status", "cause", "attempts", "tool_calls", "errors", "tokens",
             "phase", "run_id", "task_id", "instance"}
    for n in g.nodes.values():
        kind = n["kind"]
        agent_id = n["id"].split(":", 1)[1] if kind in ("agent", "attempt") else None
        run_id = n.get("run_id") if kind in ("workflow", "run") else (
            n["id"].split(":")[1] if kind in ("phase", "unit") else None)
        status, cause = n.get("status"), n.get("cause")
        if kind == "attempt":
            run_id = agent_run.get(agent_id)
            status, cause = states[n["id"]]
        if kind == "agent":
            status = agent_result.get(n["id"], "no-notification")
        extra = {k: v for k, v in n.items() if k not in plain}
        instance = n["id"] if kind == "workflow" else n.get("instance")
        db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (n["id"], kind, n.get("label"), agent_id, run_id, n.get("task_id"), instance, n.get("phase"),
                    n.get("start"), n.get("end"), status, cause, n.get("attempts"), n.get("tool_calls"), n.get("errors"),
                    n.get("tokens"), json.dumps(extra)))
    db.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                   [(e["from"], e["to"], e["kind"], e["at"], e.get("via"), int(bool(e.get("inferred"))),
                     e.get("tool_use_id")) for e in g.edges])


def build(main_path, out, clp_s=None, claude_home=None, force=False, log=lambda line: None):
    """Build a bundle of the session whose main log is main_path in the directory `out`. Returns a dict of
    what was built. `claude_home` (the directory that holds projects/, tasks/ and file-history/) defaults to
    the one main_path sits in; tasks and file snapshots are skipped when there is none."""
    started = time.time()
    main_path = os.path.abspath(main_path)
    if not os.path.isfile(main_path) or not main_path.endswith(".jsonl"):
        raise B.BundleError(f"not a session log (a .jsonl file): {main_path}")
    if claude_home is None:
        project_dir = os.path.dirname(main_path)
        if os.path.basename(os.path.dirname(project_dir)) == "projects":
            claude_home = os.path.dirname(os.path.dirname(project_dir))
    claude_home = os.path.abspath(claude_home) if claude_home else None
    clp_s = B.resolve_clp_s(clp_s)
    out = os.path.abspath(out)
    if os.path.exists(out):
        if force and os.path.isfile(os.path.join(out, "catalog.sqlite")):
            shutil.rmtree(out)
        else:
            raise B.BundleError(f"{out} exists" + ("" if not force else " and is not a bundle") + "; refusing to overwrite it")

    sid, files = inventory(main_path, claude_home)
    kinds = {}
    for rel, full, kind, where in files:
        kinds.setdefault(kind, []).append(full)
    log("INVENTORY files=%d " % len(files) + " ".join(f"{k}={len(v)}" for k, v in sorted(kinds.items())))

    graph = G.build_graph(main_path)
    facts, problems = G.check_graph(graph)

    os.makedirs(os.path.join(out, "archives"))
    os.makedirs(os.path.join(out, "files"))
    try:
        seen = set()
        for rel, full, kind, where in files:
            if where != "files":
                continue
            dest = os.path.join(out, "files", kind, os.path.basename(full))
            if dest in seen:
                raise B.BundleError(f"two {kind} files named {os.path.basename(full)}")
            seen.add(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(full, dest)

        archive_ids = {}
        for kind in ARCHIVED_KINDS:
            members = sorted(kinds.get(kind, []))
            if members:
                archive_ids[kind] = B.compress(clp_s, os.path.join(out, "archives"), members)
                log(f"ARCHIVE {kind} files={len(members)} ids={','.join(archive_ids[kind])}")

        db = sqlite3.connect(os.path.join(out, "catalog.sqlite"))
        db.executescript(B.SCHEMA)
        db.executemany("INSERT INTO bundle VALUES(?,?)", [
            ("layout", str(LAYOUT)), ("session_id", sid), ("source_root", claude_home or os.path.dirname(main_path)),
            ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), ("clp_s", clp_s),
            ("graph_problems", json.dumps(problems))])
        counts = B.archive_record_counts(clp_s, os.path.join(out, "archives")) if archive_ids else {}
        for kind, ids in archive_ids.items():
            for a in ids:
                db.execute("INSERT INTO archives VALUES(?,?,?)", (a, kind, counts.get(a, 0)))
        for rel, full, kind, where in files:
            m = re.search(r"agent-([0-9a-z]+)\.(?:meta\.)?json", rel)
            rm = re.search(r"/(wf_[^/]+)[/.]", rel)
            if where == "archive":
                loc = (",".join(archive_ids[kind]), None)
            elif where == "files":
                loc = (None, f"files/{kind}/{os.path.basename(full)}")
            else:
                loc = (None, None)
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)",
                       (rel, kind, os.path.getsize(full), _sha256(full), loc[0], loc[1], m.group(1) if m else None,
                        rm.group(1) if rm else None))
            if kind in ("agent-meta", "workflow-agent-meta"):
                with open(full, encoding="utf-8") as fh:
                    meta = json.load(fh)
                db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)",
                           (m.group(1), rm.group(1) if rm else None, meta.get("agentType"), meta.get("description"),
                            meta.get("spawnDepth"), meta.get("parentAgentId"), meta.get("toolUseId"),
                            int(bool(meta.get("isFork"))), meta.get("model")))
        _write_graph(db, graph)
        event_counts = _write_events(db, kinds)
        # the engine's own count of records with a uuid must equal the events written plus the unlisted
        for kind, (n_events, n_unlisted, _) in event_counts.items():
            total = sum(B.count_records_with(clp_s, os.path.join(out, "archives", a), "uuid") for a in archive_ids.get(kind, []))
            if total != n_events + n_unlisted:
                raise B.BundleError(f"events for {kind}: {n_events} + {n_unlisted} unlisted, but the archive counts "
                                    f"{total} records with a uuid")
        log("EVENTS " + " ".join(f"{k}={v[0]}(+{v[1]}unlisted,+{v[2]}no-uuid)" for k, v in event_counts.items()) + " match=archives")
        db.commit()
        db.execute("VACUUM")
        db.close()
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return {"session_id": sid, "out": out, "files": len(files), "archives": archive_ids, "events": event_counts,
            "graph_facts": facts, "graph_problems": problems, "seconds": time.time() - started,
            "catalog_bytes": os.path.getsize(os.path.join(out, "catalog.sqlite"))}
