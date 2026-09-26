"""bundle_build - turn one Claude Code session's files into a bundle, and a bundle into its catalog.

  bundle/
    manifest.json    every source file: kind, size, hash, and where it went
    archives/        one clp-s archives dir, one archive per kind of log, nothing else in it
    files/           the files that are not JSON logs, at their paths relative to the session
    catalog.sqlite   the map, derived from the three above

make_bundle reads the session's files once: it classifies each by its path (a file that matches no rule
stops the build), copies the plain files, and compresses each kind of log in one run, removing NUL bytes
first (see bundle.prepare). build_catalog then reads only the bundle: each archive once, in its original
order, and the files under files/. So a catalog can be rebuilt with nothing but the bundle. The engine is
reached only through the functions in bundle.py. Stdlib only.
"""

import bisect
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time

import bundle as B
import session_graph as G
from session_turns import final_usage, is_human_prompt, usage_of

# kind, where it goes (an archive or files/), and a regex on the path relative to the session directory.
SESSION_RULES = [
    ("agent", "archive", r"^subagents/agent-[^/]+\.jsonl$"),
    ("agent-meta", "files", r"^subagents/agent-[^/]+\.meta\.json$"),
    ("workflow-agent", "archive", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.jsonl$"),
    ("workflow-agent-meta", "files", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.meta\.json$"),
    ("workflow-journal", "archive", r"^subagents/workflows/wf_[^/]+/journal\.jsonl$"),
    ("workflow-run", "archive", r"^workflows/wf_[^/]+\.json$"),
    ("workflow-script", "files", r"^workflows/scripts/[^/]+\.js$"),
    ("tool-result", "files", r"^tool-results/[^/]+$"),
    ("session-title", "files", r"^custom-title\.json$"),
    ("classifier-error", "files", r"^auto-mode-classifier-error\.txt$"),
]
ARCHIVED_KINDS = ["main", "agent", "workflow-agent", "workflow-journal", "workflow-run"]
EVENT_KINDS = ("main", "agent", "workflow-agent")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(main_path, claude_home):
    """(session id, [source]) for every file of the session. A source is a dict: name (its path relative
    to the session, which is also its path under files/), path (relative to the source root), full, kind,
    where (archive or files)."""
    sid = os.path.basename(main_path)[:-len(".jsonl")]
    project_dir = os.path.dirname(os.path.abspath(main_path))
    root = os.path.join(project_dir, sid)
    source_root = claude_home or project_dir

    def source(name, full, kind, where):
        return {"name": name, "path": os.path.relpath(full, source_root), "full": full, "kind": kind, "where": where}

    found = [source(os.path.basename(main_path), os.path.abspath(main_path), "main", "archive")]
    if os.path.isdir(root):
        for dirpath, _, names in os.walk(root):
            for n in names:
                full = os.path.join(dirpath, n)
                inner = os.path.relpath(full, root)
                for kind, where, rx in SESSION_RULES:
                    if re.match(rx, inner):
                        found.append(source(inner, full, kind, where))
                        break
                else:
                    raise B.BundleError(f"unclassified file: {os.path.relpath(full, source_root)} "
                                        "(add a rule to bundle_build.SESSION_RULES or remove it)")
    if claude_home:
        for kind in ("tasks", "file-history"):
            base = os.path.join(claude_home, kind, sid)
            for dirpath, _, names in os.walk(base):
                for n in names:
                    full = os.path.join(dirpath, n)
                    if dirpath != base:
                        raise B.BundleError(f"unclassified file (nested under {kind}): {os.path.relpath(full, source_root)}")
                    found.append(source(f"{kind}/{n}", full, kind.rstrip("s") if kind == "tasks" else kind, "files"))
    return sid, sorted(found, key=lambda s: s["name"])


def make_bundle(main_path, out, clp_s=None, claude_home=None, force=False, log=lambda line: None):
    """Make the bundle of the session whose main log is main_path in `out`, then its catalog. Returns
    build_catalog's result. `claude_home` (the directory holding projects/, tasks/ and file-history/)
    defaults to the one main_path sits in; tasks and file snapshots are skipped when there is none."""
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
        is_bundle = os.path.isfile(os.path.join(out, "manifest.json")) or os.path.isfile(os.path.join(out, "catalog.sqlite"))
        if force and is_bundle:
            shutil.rmtree(out)
        else:
            raise B.BundleError(f"{out} exists" + (" and is not a bundle" if force else "") + "; refusing to overwrite it")

    sid, sources = inventory(main_path, claude_home)
    counts = {}
    for s in sources:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
    log("INVENTORY files=%d " % len(sources) + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    os.makedirs(os.path.join(out, "archives"))
    os.makedirs(os.path.join(out, "files"))
    workdir = tempfile.mkdtemp(prefix="clp-bundle-")
    try:
        for s in sources:
            s["bytes"] = os.path.getsize(s["full"])
            s["sha256"] = _sha256(s["full"])
            if s["where"] == "files":
                s["file"] = f"files/{s['name']}"
                os.makedirs(os.path.dirname(os.path.join(out, s["file"])), exist_ok=True)
                shutil.copy2(s["full"], os.path.join(out, s["file"]))
        for kind in ARCHIVED_KINDS:
            members = [s for s in sources if s["kind"] == kind]
            if not members:
                continue
            prepared = [B.prepare(s["full"], workdir) for s in members]
            archive_id = B.compress(clp_s, os.path.join(out, "archives"), prepared)
            pos = 0
            for s, p in zip(members, prepared):
                s.update(archive_id=archive_id, first_pos=pos, records=p.records, nul_bytes=p.nul_bytes,
                         damaged_lines=p.damaged_lines)
                pos += p.records
                if p.nul_bytes:
                    log(f"REPAIRED {s['path']} nul_bytes={p.nul_bytes} damaged_lines={p.damaged_lines}")
            log(f"ARCHIVE {kind} files={len(members)} records={pos} id={archive_id}")
        manifest = {"layout": B.MANIFEST_LAYOUT, "session_id": sid, "source_root": claude_home or os.path.dirname(main_path),
                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "clp_s": clp_s,
                    "sources": [{k: v for k, v in s.items() if k != "full"} for s in sources]}
        with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1)
        result = build_catalog(out, clp_s, log)
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    result["seconds"] = time.time() - started
    return result


def _load_session(out, manifest, clp_s):
    """(Session, {kind: [(source, records)]}, record counts, [open ArchiveRecords]) from the bundle only.
    The records stay in the archives' output until read; close the ArchiveRecords when done."""
    archives = os.path.join(out, "archives")
    counts = B.archive_record_counts(clp_s, archives) if os.listdir(archives) else {}
    by_archive = {}
    for s in manifest["sources"]:
        if s["where"] == "archive":
            by_archive.setdefault(s["archive_id"], []).append(s)
    opened, records_of = [], {}
    try:
        for archive_id, members in by_archive.items():
            expected = sum(s["records"] for s in members)
            if counts.get(archive_id) != expected:
                raise B.BundleError(f"archive {archive_id} holds {counts.get(archive_id)} records; the manifest says "
                                    f"{expected} (every non-blank line of its files); clp-s left some out")
            archive = B.ArchiveRecords(clp_s, os.path.join(archives, archive_id), expected)
            opened.append(archive)
            for s in members:
                records_of[s["name"]] = B.SourceRecords(archive, s["first_pos"], s["records"])

        def meta(name):
            with open(os.path.join(out, "files", name), encoding="utf-8") as fh:
                return json.load(fh)

        main = next(s for s in manifest["sources"] if s["kind"] == "main")
        metas, transcripts, runs, journals = {}, {}, {}, {}
        for s in manifest["sources"]:
            name, kind = s["name"], s["kind"]
            agent = re.search(r"agent-([0-9a-z]+)\.(?:meta\.)?json", name)
            run = re.search(r"(wf_[^/]+?)(?:/|\.json$)", name)
            if kind in ("agent-meta", "workflow-agent-meta"):
                m = meta(name)
                m["workflow_dir"] = run.group(1) if kind == "workflow-agent-meta" else None
                metas[agent.group(1)] = m
            elif kind in ("agent", "workflow-agent"):
                transcripts[agent.group(1)] = records_of[name]
            elif kind == "workflow-run":
                for r in records_of[name]:
                    runs[r["runId"]] = r
            elif kind == "workflow-journal":
                journals[run.group(1)] = list(records_of[name])
        session = G.Session(records_of[main["name"]], metas, transcripts, runs, journals)
        positioned = {}
        for s in manifest["sources"]:
            if s["where"] == "archive":
                positioned.setdefault(s["kind"], []).append((s, records_of[s["name"]]))
        return session, positioned, counts, opened
    except BaseException:
        for archive in opened:
            archive.close()
        raise


def build_catalog(out, clp_s=None, log=lambda line: None):
    """(Re)build out/catalog.sqlite from the bundle alone: manifest.json, archives/ and files/. The new
    catalog is written beside the old one and replaces it only once complete."""
    started = time.time()
    out = os.path.abspath(out)
    try:
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
    except FileNotFoundError:
        raise B.BundleError(f"not a bundle (no manifest.json): {out}") from None
    if manifest.get("layout") != B.MANIFEST_LAYOUT:
        raise B.BundleError(f"{out} was made with bundle layout {manifest.get('layout')}, not {B.MANIFEST_LAYOUT}; "
                            "make it again with `build --force`")
    clp_s = B.resolve_clp_s(clp_s)
    session, positioned, counts, opened = _load_session(out, manifest, clp_s)
    tmp = os.path.join(out, "catalog.sqlite.tmp")
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    try:
        graph = G.build_graph(session)
        facts, problems = G.check_graph(graph)
        db.executescript(B.SCHEMA)
        title = None
        for s in manifest["sources"]:
            if s["kind"] == "session-title":
                with open(os.path.join(out, s["file"]), encoding="utf-8") as fh:
                    title = json.load(fh).get("customTitle")
        db.executemany("INSERT INTO bundle VALUES(?,?)", [
            ("layout", str(B.LAYOUT)), ("session_id", manifest["session_id"]), ("source_root", manifest["source_root"]),
            ("built_at", manifest["built_at"]), ("catalog_built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            ("clp_s", clp_s), ("title", title), ("graph_problems", json.dumps(problems))])
        for s in manifest["sources"]:
            if s["where"] == "archive" and not db.execute("SELECT 1 FROM archives WHERE archive_id = ?", (s["archive_id"],)).fetchone():
                db.execute("INSERT INTO archives VALUES(?,?,?)", (s["archive_id"], s["kind"], counts.get(s["archive_id"], 0)))
        for s in manifest["sources"]:
            agent = re.search(r"agent-([0-9a-z]+)\.(?:meta\.)?json", s["name"])
            run = re.search(r"(wf_[^/]+?)(?:/|\.json$)", s["name"])
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (s["path"], s["kind"], s["bytes"], s["sha256"], s.get("archive_id"), s.get("file"),
                        agent.group(1) if agent else None, run.group(1) if run else None, s.get("first_pos"),
                        s.get("records"), s.get("nul_bytes"), s.get("damaged_lines")))
        for aid, m in session.metas.items():
            db.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)",
                       (aid, m["workflow_dir"], m.get("agentType"), m.get("description"), m.get("spawnDepth"),
                        m.get("parentAgentId"), m.get("toolUseId"), int(bool(m.get("isFork"))), m.get("model")))
        _write_graph(db, graph)
        event_counts = _write_events(db, positioned)
        log("EVENTS " + " ".join(f"{k}={v[0]}(+{v[1]}unlisted,+{v[2]}no-uuid)" for k, v in event_counts.items()))
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()
        for archive in opened:
            archive.close()
    os.replace(tmp, os.path.join(out, "catalog.sqlite"))
    return {"session_id": manifest["session_id"], "out": out, "files": len(manifest["sources"]),
            "archives": {s["kind"]: s["archive_id"] for s in manifest["sources"] if s["where"] == "archive"},
            "events": event_counts, "graph_facts": facts, "graph_problems": problems,
            "repaired": [(s["path"], s["nul_bytes"], s["damaged_lines"]) for s in manifest["sources"] if s.get("nul_bytes")],
            "seconds": time.time() - started, "catalog_bytes": os.path.getsize(os.path.join(out, "catalog.sqlite"))}


def _texts(message):
    c = message.get("content") if isinstance(message, dict) else None
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return []


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _input_hashes(name, tool_input):
    """(input_hash, file_hash, read_hash) of a tool call's input; the catalog keeps hashes, not text."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    path = path if isinstance(path, str) else None
    read = [path, tool_input.get("offset"), tool_input.get("limit")] if name == "Read" and path else None
    return _hash(tool_input), _hash(path) if path else None, _hash(read) if read else None


def _event(kind, pos, r):
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
            tools.append((blk.get("id"), "use", blk.get("name"), None, *_input_hashes(blk.get("name"), blk.get("input"))))
        elif blk.get("type") == "tool_result":
            err = int(bool(blk.get("is_error")))
            is_error |= err
            tools.append((blk.get("tool_use_id"), "result", None, err, None, None, None))
    ts = r.get("timestamp")
    usage = None
    if r.get("type") == "assistant" and isinstance(message.get("id"), str) and message.get("model") != "<synthetic>":
        usage = usage_of(message)
    row = dict(message_id=message.get("id") if r.get("type") == "assistant" else None, usage=usage,
               uuid=uuid, kind=kind, pos=pos, agent_id=r.get("agentId"), ts=ts[:23] if isinstance(ts, str) else None,
               type=r.get("type"), turn=None,
               human=int(kind == "main" and r.get("type") == "user" and any(is_human_prompt(r, t) for t in texts)),
               interrupt=int(any(t.lstrip().startswith("[Request interrupted") for t in texts)), is_error=is_error,
               ref_agent_id=tur.get("agentId") if isinstance(tur, dict) and isinstance(tur.get("agentId"), str) else None,
               ref_task_id=task.group(1) if task else None)
    if r.get("type") in ("user", "assistant") or row["ref_agent_id"] or row["ref_task_id"]:
        return row, tools
    return None, tools


TEST_RUNNER = re.compile(r"\b(cargo (?:test|nextest)|pytest|python3? -m (?:pytest|unittest)|go test|npm (?:run )?test|"
                         r"yarn test|pnpm test|jest|vitest|ctest|mvn test|gradle test|task test)")
COMMIT = re.compile(r"\bgit commit\b")
PR_CREATE = re.compile(r"\bgh pr create\b")


def _action(command, output, failed):
    """(action, fields) for a commit, PR or test command, or None. Fields hold what its output confirmed."""
    if COMMIT.search(command):
        m = re.search(r"\[([^\]\s]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\]", output)
        return "commit", {"confirmed": int(bool(m)), "branch": m.group(1) if m else None, "sha": m.group(2) if m else None}
    if PR_CREATE.search(command):
        m = re.search(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", output)
        return "pr", {"confirmed": int(bool(m)), "pr_url": m.group(0) if m else None}
    if TEST_RUNNER.search(command):
        passed = failed_tests = None
        cargo = re.findall(r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed", output)
        if cargo:
            passed, failed_tests = sum(int(p) for p, _ in cargo), sum(int(f) for _, f in cargo)
        else:
            p = re.search(r"\b(\d+) passed", output)
            f = re.search(r"\b(\d+) failed", output)
            ran = re.search(r"\bRan (\d+) tests?\b", output)
            if p or f:
                passed, failed_tests = int(p.group(1)) if p else 0, int(f.group(1)) if f else 0
            elif ran:
                fm = re.search(r"FAILED \((?:failures|errors)=(\d+)", output)
                passed = int(ran.group(1)) - (int(fm.group(1)) if fm else 0)
                failed_tests = int(fm.group(1)) if fm else 0
        return "test", {"confirmed": int(passed is not None), "tests_passed": passed, "tests_failed": failed_tests}
    return None


def _result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return str(content or "")


def _write_events(db, positioned):
    """Events for the main log and the agent transcripts, and the outcomes found among them (file versions,
    actions); returns {kind: (events, unlisted, no uuid)}."""
    counts = {}
    prompts, turn_of_prompt = [], {}
    for kind in EVENT_KINDS:
        rows, unlisted, no_uuid = [], 0, 0
        deltas, pending, actions = [], {}, []
        for _source, records in positioned.get(kind, []):
            for pos, r in enumerate(records, records.first):
                if r.get("type") == "file-history-delta":
                    deltas.append(r)
                message = r.get("message") if isinstance(r.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    if blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                        command = (blk.get("input") or {}).get("command")
                        if isinstance(command, str):
                            pending[blk.get("id")] = (command, r.get("uuid"), r.get("agentId"), r.get("timestamp"))
                    elif blk.get("type") == "tool_result" and blk.get("tool_use_id") in pending:
                        command, uuid, agent, ts = pending.pop(blk["tool_use_id"])
                        found = _action(command, _result_text(blk), bool(blk.get("is_error")))
                        if found:
                            actions.append((uuid, agent, ts, found[0], int(bool(blk.get("is_error"))), found[1]))
                if not r.get("uuid"):
                    no_uuid += 1
                    continue
                row, tools = _event(kind, pos, r)
                if row is None:
                    unlisted += 1
                else:
                    rows.append((row, tools))
        # one record per response carries its final usage (the most output, with cache fields; the later
        # record on a tie), so a token column sums each response once
        carrier = {}
        for i, (r, _) in enumerate(rows):
            if r["usage"] is not None:
                key = (r["agent_id"], r["message_id"])
                best = carrier.get(key)
                if best is None or final_usage(rows[best][0]["usage"], r["usage"]) is r["usage"] or \
                        r["usage"]["final"] == rows[best][0]["usage"]["final"]:
                    carrier[key] = i
        carriers = set(carrier.values())
        for i, (r, _) in enumerate(rows):
            u = r["usage"] if i in carriers else None
            for name in ("input", "output", "cache_read", "cache_write"):
                r[f"tokens_{name}"] = u[name] if u else None
        if kind == "main":  # a turn runs from one human prompt to the next
            prompts = sorted(r["ts"] for r, _ in rows if r["human"] and r["ts"])
            turn_of_prompt = {r["uuid"]: bisect.bisect_right(prompts, r["ts"]) for r, _ in rows if r["human"] and r["ts"]}
        # an agent's records take the main thread's turn at their time
        for r, _ in rows:
            r["turn"] = bisect.bisect_right(prompts, r["ts"]) if r["ts"] and prompts else None
        cols = ["uuid", "kind", "pos", "agent_id", "ts", "type", "turn", "human", "interrupt", "is_error", "ref_agent_id",
                "ref_task_id", "message_id", "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write"]
        for r, tools in rows:
            cur = db.execute(f"INSERT INTO events({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [r[c] for c in cols])
            db.executemany("INSERT INTO event_tools VALUES(?,?,?,?,?,?,?,?)", [(cur.lastrowid, *t) for t in tools])
        for d in deltas:
            backup = d.get("backup") if isinstance(d.get("backup"), dict) else {}
            parent, tracked = backup.get("realParentDir"), d.get("trackingPath")
            path = os.path.join(parent, os.path.basename(tracked)) if parent and tracked else tracked
            ts = d.get("timestamp") or backup.get("backupTime")
            turn = turn_of_prompt.get(d.get("snapshotMessageId"))
            if turn is None and isinstance(ts, str) and prompts:
                turn = bisect.bisect_right(prompts, ts[:23])
            name = backup.get("backupFileName")
            db.execute("INSERT INTO file_versions VALUES(?,?,?,?,?,?,?,?)",
                       (turn, ts[:23] if isinstance(ts, str) else None, path, _hash(path) if path else None,
                        backup.get("version"), f"files/file-history/{name}" if name else None, d.get("messageId"),
                        d.get("snapshotMessageId")))
        for uuid, agent, ts, action, failed, fields in actions:
            turn = bisect.bisect_right(prompts, ts[:23]) if isinstance(ts, str) and prompts else None
            db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (uuid, kind, agent, turn, ts[:23] if isinstance(ts, str) else None, action, failed,
                        fields.get("confirmed"), fields.get("branch"), fields.get("sha"), fields.get("pr_url"),
                        fields.get("tests_passed"), fields.get("tests_failed")))
        if rows or unlisted or no_uuid:
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
    no_tokens = {"input": None, "output": None, "cache_read": None, "cache_write": None}
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
        tokens = n.get("tokens") or no_tokens
        db.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (n["id"], kind, n.get("label"), agent_id, run_id, n.get("task_id"), instance, n.get("phase"),
                    n.get("start"), n.get("end"), status, cause, n.get("attempts"), n.get("tool_calls"), n.get("errors"),
                    tokens["input"], tokens["output"], tokens["cache_read"], tokens["cache_write"], json.dumps(extra)))
    db.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                   [(e["from"], e["to"], e["kind"], e["at"], e.get("via"), int(bool(e.get("inferred"))),
                     e.get("tool_use_id")) for e in g.edges])
