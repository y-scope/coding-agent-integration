"""bundle_review - review many session bundles at once: build them, measure each, rank, group errors.

A harness developer's view of a directory of bundles (one per Claude Code session). Per session it
computes a signal vector from the catalog and a few counts on the main log's archive, then reports:

  zero-tolerance signals   any occurrence is a finding (damaged logs, a tool call without a result, a
                           workflow reported completed with failed attempts, ...), with example IDs
  rate signals             compared with the median and 90th percentile of the sessions active enough
                           to rate, and the sessions flagged most often
  error groups             failed tool calls grouped by CLP's own template, shape(toolUseResult), then
                           merged a second time: command failures (Error: Exit code ...) by a shared
                           beginning and end, other messages by embeddings from the plugin's built-in
                           semantic endpoint (or one named); if the endpoint fails, the review fails.

Engine calls go through the search wrapper and bundle.py, like the rest of clp-bundle. Stdlib only.
"""

import glob
import importlib.machinery
import importlib.util
import json
import os
import re
import statistics
import subprocess

import bundle as B

BIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_NAME = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl$")
COMMAND_FAILURE = "Error: Exit code"

# name: (SQL returning one number, SQL returning up to three example ids or None)
SQL = {
    "main_records": ("SELECT COALESCE(SUM(records), 0) FROM archives WHERE kind = 'main'", None),
    "human_prompts": ("SELECT COUNT(*) FROM events WHERE kind = 'main' AND human = 1", None),
    "agents": ("SELECT COUNT(*) FROM nodes WHERE kind = 'agent'", None),
    "workflow_runs": ("SELECT COUNT(*) FROM nodes WHERE kind = 'run'", None),
    "attempts": ("SELECT COUNT(*) FROM nodes WHERE kind = 'attempt'", None),
    "tool_calls": ("SELECT COUNT(*) FROM event_tools WHERE role = 'use'", None),
    "tool_errors": ("SELECT COUNT(*) FROM event_tools WHERE role = 'result' AND is_error = 1", None),
    "structured_output_calls": ("SELECT COUNT(*) FROM event_tools WHERE role = 'use' AND name = 'StructuredOutput'", None),
    "structured_output_errors": (
        "SELECT COUNT(*) FROM event_tools u JOIN event_tools r ON r.tool_use_id = u.tool_use_id AND r.role = 'result' "
        "AND r.is_error = 1 WHERE u.role = 'use' AND u.name = 'StructuredOutput'", None),
    "agents_failed": ("SELECT COUNT(*) FROM nodes WHERE kind = 'agent' AND status = 'failed'", None),
    "attempts_stalled": ("SELECT COUNT(*) FROM nodes WHERE kind = 'attempt' AND status = 'stalled-retried'", None),
    "tokens_input": ("SELECT COALESCE(SUM(tokens_input), 0) FROM nodes WHERE kind IN ('main', 'agent', 'attempt')", None),
    "tokens_output": ("SELECT COALESCE(SUM(tokens_output), 0) FROM nodes WHERE kind IN ('main', 'agent', 'attempt')", None),
    # zero-tolerance signals
    "nul_bytes": ("SELECT COALESCE(SUM(nul_bytes), 0) FROM sources",
                  "SELECT path FROM sources WHERE nul_bytes > 0 LIMIT 3"),
    "repeated_uuids": ("SELECT COUNT(*) - COUNT(DISTINCT kind || uuid) FROM events",
                       "SELECT uuid FROM events GROUP BY kind, uuid HAVING COUNT(*) > 1 LIMIT 3"),
    "calls_without_result": (
        "SELECT COUNT(*) FROM event_tools u WHERE u.role = 'use' AND NOT EXISTS "
        "(SELECT 1 FROM event_tools r WHERE r.tool_use_id = u.tool_use_id AND r.role = 'result')",
        "SELECT u.tool_use_id FROM event_tools u WHERE u.role = 'use' AND NOT EXISTS "
        "(SELECT 1 FROM event_tools r WHERE r.tool_use_id = u.tool_use_id AND r.role = 'result') LIMIT 3"),
    "agents_no_notification": ("SELECT COUNT(*) FROM nodes WHERE kind = 'agent' AND status = 'no-notification'",
                               "SELECT id FROM nodes WHERE kind = 'agent' AND status = 'no-notification' LIMIT 3"),
    "completed_runs_with_failed_attempts": (
        "SELECT COUNT(*) FROM (SELECT r.id FROM nodes r JOIN nodes a ON a.kind = 'attempt' AND a.run_id = r.run_id "
        "WHERE r.kind = 'run' AND r.status = 'completed' GROUP BY r.id HAVING SUM(a.status != 'ok') > 0)",
        "SELECT r.id FROM nodes r JOIN nodes a ON a.kind = 'attempt' AND a.run_id = r.run_id WHERE r.kind = 'run' "
        "AND r.status = 'completed' GROUP BY r.id HAVING SUM(a.status != 'ok') > 0 LIMIT 3"),
    "resume_reruns": (
        "SELECT COUNT(*) FROM (SELECT u.id FROM nodes u JOIN edges e ON e.src = u.id AND e.kind = 'contains' "
        "JOIN nodes a ON a.id = e.dst AND a.status = 'ok' WHERE u.kind = 'unit' GROUP BY u.id "
        "HAVING COUNT(DISTINCT a.instance) > 1)",
        "SELECT u.id FROM nodes u JOIN edges e ON e.src = u.id AND e.kind = 'contains' JOIN nodes a ON a.id = e.dst "
        "AND a.status = 'ok' WHERE u.kind = 'unit' GROUP BY u.id HAVING COUNT(DISTINCT a.instance) > 1 LIMIT 3"),
    "launch_errors": ("SELECT COUNT(*) FROM nodes WHERE kind = 'launch_error'",
                      "SELECT id FROM nodes WHERE kind = 'launch_error' LIMIT 3"),
    "configuration_errors": ("SELECT COUNT(*) FROM nodes WHERE kind IN ('agent', 'attempt') AND cause = 'api-400'",
                             "SELECT id FROM nodes WHERE kind IN ('agent', 'attempt') AND cause = 'api-400' LIMIT 3"),
}
KQL = {
    "api_errors": 'isApiErrorMessage:true',
    "compactions": 'subtype:"compact_boundary"',
    "denials": 'message.content.content:"*doesn*t want to proceed*"',
    "interrupts": 'message.content.text:"[Request interrupted*"',
    "truncated_reads": 'attachment.type:"read_truncation_notice"',
}
ZERO = [
    ("nul_bytes", "logs damaged by lost writes (NUL bytes removed)"),
    ("repeated_uuids", "records rewritten with the same uuid"),
    ("calls_without_result", "tool calls with no result"),
    ("agents_no_notification", "agents that never reported back"),
    ("completed_runs_with_failed_attempts", "workflow runs reported completed with failed attempts"),
    ("resume_reruns", "logical agents re-run by a resume after succeeding"),
    ("launch_errors", "workflow launches rejected"),
    ("configuration_errors", "agents or attempts failed with API 400 (configuration)"),
]
# name, value, whether a session is active enough to rate, format
RATES = [
    ("tool error rate", lambda s: s["tool_errors"] / s["tool_calls"], lambda s: s["tool_calls"] >= 50, "{:.1%}"),
    ("stalled attempts / attempts", lambda s: s["attempts_stalled"] / s["attempts"], lambda s: s["attempts"] >= 20, "{:.1%}"),
    ("failed agents / agents", lambda s: s["agents_failed"] / s["agents"], lambda s: s["agents"] >= 5, "{:.1%}"),
    ("StructuredOutput error rate", lambda s: s["structured_output_errors"] / s["structured_output_calls"],
     lambda s: s["structured_output_calls"] >= 20, "{:.1%}"),
    ("API errors per 1k main records", lambda s: 1000 * s["api_errors"] / s["main_records"],
     lambda s: s["main_records"] >= 500, "{:.2f}"),
    ("compactions per hour", lambda s: s["compactions"] / s["hours"], lambda s: s["hours"] >= 1, "{:.2f}"),
    ("idle share of turn time", lambda s: s["idle_s"] / s["e2e_s"], lambda s: s["hours"] >= 1, "{:.0%}"),
    ("human-wait share of turn time", lambda s: s["human_s"] / s["e2e_s"], lambda s: s["hours"] >= 1, "{:.0%}"),
    ("interrupts per human prompt", lambda s: s["interrupts"] / s["human_prompts"], lambda s: s["human_prompts"] >= 10, "{:.2f}"),
    ("truncated reads per 1k tool calls", lambda s: 1000 * s["truncated_reads"] / s["tool_calls"],
     lambda s: s["tool_calls"] >= 50, "{:.2f}"),
    ("output tokens per human prompt", lambda s: s["tokens_output"] / s["human_prompts"],
     lambda s: s["human_prompts"] >= 10, "{:,.0f}"),
]
MIN_RATED = 5


def _load_script(name, path):
    """A plugin script without a .py name, loaded as a module (to reuse its functions exactly)."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def default_endpoint():
    """The plugin's built-in semantic endpoint (the first of DEFAULT_SEMANTIC_ENDPOINTS in clp-common.sh,
    which log-shape-cluster and semantic search also fall back to)."""
    with open(os.path.join(BIN_DIR, "lib", "clp-common.sh"), encoding="utf-8") as fh:
        m = re.search(r"DEFAULT_SEMANTIC_ENDPOINTS=\(\s*\"([^\"]+)\"", fh.read())
    if not m:
        raise B.BundleError("no DEFAULT_SEMANTIC_ENDPOINTS in lib/clp-common.sh")
    return m.group(1)


def _search(wrapper, bundle_dir, archive_id, args, missing_ok=None):
    """Search one archive of a bundle. `missing_ok` names an engine message that means "no such column
    in this archive" and gives an empty result instead of an error."""
    proc = subprocess.run([wrapper, *args[:-1], "--archive-id", archive_id, os.path.join(bundle_dir, "archives"), args[-1]],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        if missing_ok and missing_ok in proc.stderr:
            return []
        raise B.BundleError(f"search failed on {bundle_dir}: {proc.stderr.strip()[-300:]}")
    return [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]


# ---- building

def find_sessions(claude_home):
    """Every Claude Code session log under claude_home/projects (a session-UUID name)."""
    return sorted(p for p in glob.glob(os.path.join(claude_home, "projects", "*", "*.jsonl"))
                  if SESSION_NAME.match(os.path.basename(p)))


def ensure_bundles(sessions, bundles_dir, clp_s=None, skip=(), log=lambda line: None):
    """Make bundles_dir/<session id> current for every session: build a missing or out-of-date bundle
    (its main log has changed size since), rebuild a catalog of an older layout. Returns
    {session id: status}; status is kept, built, rebuilt, skipped (why) or failed (why)."""
    import bundle_build
    os.makedirs(bundles_dir, exist_ok=True)
    status = {}
    for main in sessions:
        sid = os.path.basename(main)[:-len(".jsonl")]
        if any(sid.startswith(s) for s in skip):
            status[sid] = "skipped: asked to"
            continue
        out = os.path.join(bundles_dir, sid)
        manifest_path = os.path.join(out, "manifest.json")
        try:
            if os.path.isfile(manifest_path):
                with open(manifest_path, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                main_source = next(s for s in manifest["sources"] if s["kind"] == "main")
                if manifest.get("layout") == B.MANIFEST_LAYOUT and main_source["bytes"] == os.path.getsize(main):
                    if _catalog_layout(out) == B.LAYOUT:
                        status[sid] = "kept"
                        continue
                    bundle_build.build_catalog(out, clp_s)
                    status[sid] = "rebuilt"
                    log(f"BUILD {sid} rebuilt")
                    continue
            bundle_build.make_bundle(main, out, clp_s=clp_s, force=True)
            status[sid] = "built"
        except B.BundleError as err:
            text = str(err)
            status[sid] = ("skipped: still being written" if "last record is cut off" in text else "failed: " + text)[:300]
        log(f"BUILD {sid} {status[sid]}")
    return status


def _catalog_layout(out):
    import sqlite3
    try:
        db = sqlite3.connect(os.path.join(out, "catalog.sqlite"))
        row = db.execute("SELECT v FROM bundle WHERE k = 'layout'").fetchone()
        db.close()
        return int(row[0]) if row and str(row[0]).isdigit() else None
    except sqlite3.Error:
        return None


# ---- signals

def signals(bundle_dir, wrapper):
    """The signal vector of one bundle."""
    db = B.open_catalog(bundle_dir)
    try:
        s = {"session": os.path.basename(os.path.normpath(bundle_dir)), "bundle": bundle_dir, "examples": {}}
        s["project"] = db.execute("SELECT path FROM sources WHERE kind = 'main'").fetchone()[0].split("/")[-2]
        for name, (value_sql, example_sql) in SQL.items():
            s[name] = db.execute(value_sql).fetchone()[0]
            if example_sql and s[name]:
                s["examples"][name] = [r[0] for r in db.execute(example_sql)]
        main = db.execute("SELECT archive_id FROM archives WHERE kind = 'main'").fetchone()[0]
    finally:
        db.close()
    for name, kql in KQL.items():
        s[name] = sum(r.get("count", 0) for r in _search(wrapper, bundle_dir, main, ["--count", kql]))
    proc = subprocess.run([os.path.join(BIN_DIR, "clp-s-session-turns"), "--json", "--top", "0", "--waits", "1",
                           "--search-wrapper", wrapper, os.path.join(bundle_dir, "archives", main)],
                          capture_output=True, text=True, encoding="utf-8")
    t = json.loads(proc.stdout)["total_s"] if proc.returncode == 0 else {}
    s.update(e2e_s=t.get("e2e_s", 0), human_s=t.get("human_s", 0), idle_s=t.get("idle_s", 0),
             hours=t.get("e2e_s", 0) / 3600)
    return s


# ---- ranking

def rank(sessions):
    """{"zero": [...], "rates": [...], "flagged": [...]} over a list of signal vectors."""
    zero = []
    for key, label in ZERO:
        hits = sorted(((s, s[key]) for s in sessions if s[key]), key=lambda x: -x[1])
        zero.append({"signal": key, "label": label, "sessions": len(hits), "total": sum(n for _, n in hits),
                     "hits": [{"session": s["session"], "count": n, "examples": s["examples"].get(key, [])}
                              for s, n in hits]})
    rates, flags = [], {}
    for name, value, active, fmt in RATES:
        vals = sorted(((value(s), s) for s in sessions if active(s)), key=lambda x: x[0])
        entry = {"signal": name, "rated": len(vals), "format": fmt, "median": None, "p90": None, "above": []}
        if len(vals) >= MIN_RATED:
            xs = [v for v, _ in vals]
            entry["median"], entry["p90"] = statistics.median(xs), xs[int(0.9 * (len(xs) - 1))]
            above = [(v, s) for v, s in vals if v > entry["p90"] and v > entry["median"]]
            entry["above"] = [{"session": s["session"], "value": v} for v, s in sorted(above, key=lambda x: -x[0])]
            for _, s in above:
                flags.setdefault(s["session"], []).append(name)
        rates.append(entry)
    by_id = {s["session"]: s for s in sessions}
    flagged = [{"session": sid, "project": by_id[sid]["project"], "signals": names}
               for sid, names in sorted(flags.items(), key=lambda x: (-len(x[1]), x[0]))]
    return {"zero": zero, "rates": rates, "flagged": flagged}


# ---- error groups

def error_templates(bundles, wrapper):
    """Failed tool calls across bundles, grouped by CLP's template: {template: group}. A group has n,
    sessions, projects and examples ("<session> <uuid>")."""
    shapes = _load_script("log_shape_cache", os.path.join(BIN_DIR, "log-shape-cache"))
    groups = {}
    for bundle_dir in bundles:
        db = B.open_catalog(bundle_dir)
        try:
            project = db.execute("SELECT path FROM sources WHERE kind = 'main'").fetchone()[0].split("/")[-2]
            archives = db.execute("SELECT archive_id FROM archives WHERE kind IN ('main', 'agent', 'workflow-agent')").fetchall()
        finally:
            db.close()
        session = os.path.basename(os.path.normpath(bundle_dir))
        for (archive_id,) in archives:
            # an archive where toolUseResult is never a plain string has no column for shape() to apply to
            rows = _search(wrapper, bundle_dir, archive_id, ["--experimental", "--projection", "uuid,shape(toolUseResult)",
                                                              "message.content.is_error:true"],
                           missing_ok='no such nodes match column "toolUseResult"')
            for r in rows:
                if not isinstance(r.get("toolUseResult"), str):
                    continue
                template = shapes.shape_line_to_log_shape({"shape": r["toolUseResult"]})
                g = groups.setdefault(template, {"n": 0, "sessions": set(), "projects": set(), "examples": []})
                g["n"] += 1
                g["sessions"].add(session)
                g["projects"].add(project)
                if len(g["examples"]) < 3:
                    g["examples"].append(f"{session} {r.get('uuid')}")
    return groups


def _affix_share(a, b):
    """How much of the longer text a common beginning and end cover, and the beginning's length."""
    p = len(os.path.commonprefix([a, b]))
    s = len(os.path.commonprefix([a[p:][::-1], b[p:][::-1]]))
    return (p + s) / max(len(a), len(b)), p


def merge_affix(templates, share=0.6):
    """Greedy groups of templates whose common beginning and end cover `share` of the longer one."""
    leaders = []
    for t in templates:
        text = " ".join(t.split())
        for lead in leaders:
            fraction, prefix = _affix_share(text, lead[0])
            if fraction >= share and prefix >= 15:
                lead[1].append(t)
                break
        else:
            leaders.append((text, [t]))
    return [members for _, members in leaders]


def merge_embeddings(templates, endpoint, threshold=0.80):
    """Greedy leader clusters of templates by cosine similarity of their embeddings (the semantic
    server at `endpoint`), as log-shape-cluster computes them."""
    cluster = _load_script("log_shape_cluster", os.path.join(BIN_DIR, "log-shape-cluster.py"))
    unique, index_of = cluster.dedup_truncated(templates, 500)
    vectors = cluster.embed_texts(cluster.embeddings_url(cluster.resolve_endpoint(endpoint)), unique, 256, 100_000_000)
    sums, members = [], []
    for i, k in enumerate(index_of):
        v = vectors[k]
        best, best_sim = -1, -1.0
        for c, total in enumerate(sums):
            norm = sum(x * x for x in total) ** 0.5
            sim = sum(a * b for a, b in zip(total, v)) / norm if norm else 0.0
            if sim > best_sim:
                best_sim, best = sim, c
        if best >= 0 and best_sim >= threshold:
            sums[best] = [a + b for a, b in zip(sums[best], v)]
            members[best].append(templates[i])
        else:
            sums.append(list(v))
            members.append([templates[i]])
    return members


def error_groups(groups, endpoint):
    """Second-level groups over the templates: command failures by shared beginning and end, other
    messages by embeddings from `endpoint` (an unreachable endpoint raises). Each group: leader, n,
    templates, sessions, projects, examples, method."""
    ordered = sorted(groups, key=lambda t: -groups[t]["n"])
    commands = [t for t in ordered if t.startswith(COMMAND_FAILURE)]
    others = [t for t in ordered if not t.startswith(COMMAND_FAILURE)]
    merged = [(m, "affix") for m in merge_affix(commands)]
    if others:
        try:
            merged += [(m, "embeddings") for m in merge_embeddings(others, endpoint)]
        except (SystemExit, Exception) as err:          # the embedding helper exits on an unreachable server
            raise B.BundleError(f"the semantic endpoint {endpoint} failed: {' '.join(str(err).split())[:200]}") from None
    out = []
    for members, method in merged:
        members = sorted(members, key=lambda t: -groups[t]["n"])
        out.append({"leader": " ".join(members[0].split()), "n": sum(groups[t]["n"] for t in members),
                    "templates": len(members), "method": method,
                    "sessions": sorted(set().union(*(groups[t]["sessions"] for t in members))),
                    "projects": sorted(set().union(*(groups[t]["projects"] for t in members))),
                    "examples": [e for t in members for e in groups[t]["examples"]][:3]})
    return sorted(out, key=lambda g: (-len(g["projects"]), -len(g["sessions"]), -g["n"]))
