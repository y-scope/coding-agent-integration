"""analyze - which application produced these logs, and what preparing them takes.

There is one pipeline -- acquire, structure, categorise, measure, report -- and it runs on logs.
A Claude Code session is not a different kind of thing from a vLLM worker log; it is logs from a
particular application. So the only question this module asks is:

    which application produced these logs?

and then: does that application have a registered optimisation? Claude Code does, because its file
layout and its record graph are known in advance and its categories are pre-trained -- the plugin
ships their classification -- so the categorise stage classifies nothing at run time and the acquire
stage has a graph to build. Every other application takes the
general route, which discovers structure and categories and caches the classification per
application -- which is what an optimisation is an optimisation *of*. A second application can earn
its own route later by being registered here; it does not need a new mode.

The application is read from the records, never from the path. An archive's records come from
`clp schema --field-counts`; a raw file's come from `clp detect`, which also names the
text formats it can convert. A path, a directory layout or a bundle manifest is evidence about which
application wrote the logs, not a category of its own.

Nothing here analyses or compresses anything itself: every stage is one of the plugin's existing
commands, run as a subprocess, with the flags that command's own report asked for. Stdlib only.
"""

import glob
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

import bundle as B
import session_layout_claude as L
from commands import CLP, command  # noqa: F401

# The bin/ directory, which holds the one command every step here runs.
BIN_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# The two routes through the one pipeline.
SPECIALISED = "specialised"
GENERAL = "general"
# What the output says when the records match no registered application. Not an error: logs whose
# application is unrecognised are exactly what the general route is for.
UNRECOGNISED = "unrecognised"

# The skill that owns the interactive stages after either route. One skill covers both: it branches
# on the route this module reports, so there is nothing to choose between here.
ANALYSIS_SKILL = "analyze-logs"

# The KQL that decides whether a Claude Code session needs a bundle: its main log records that it
# launched an agent or a workflow, but not what they then did. Kept as one constant because it is
# the question a user would otherwise have to know to ask.
LAUNCH_KQL = "message.content.name:Agent OR message.content.name:Workflow"

# The formats clp detect reports for a file that holds no usable log records. A target made
# only of these is not logs, which is the one classification failure that is a hard error.
NOT_LOGS_FORMATS = ("empty", "binary", "compressed", "unreadable")


class AnalyzeError(Exception):
    """A target that cannot be read, or is not logs at all."""


# --- the applications this plugin recognises ----------------------------------------------------


class Application:
    """One recognised application: how its records give it away, how its logs are acquired, and
    which route analyses them.

    `requires` are root field names every record set of this application has; `markers` are the ones
    it draws from, of which `min_markers` must be present. `exact_roots` are complete root field
    sets, for an application whose schema is fixed and short enough that a subset rule would be
    guesswork. `formats` are the names clp detect gives this application's text formats.
    """

    def __init__(self, name, route, acquire, requires=(), markers=(), min_markers=0,
                 exact_roots=(), formats=(), skips="", agent=""):
        self.name = name
        self.route = route
        self.acquire = acquire          # "session" or "folder": which compressor its logs need
        self.agent = agent              # for a session acquire: what clp compress session --agent takes
        self.requires = tuple(requires)
        self.markers = tuple(markers)
        self.min_markers = min_markers
        self.exact_roots = tuple(frozenset(r) for r in exact_roots)
        self.formats = tuple(formats)
        self.skips = skips              # what its optimisation lets the pipeline skip

    def match_records(self, roots):
        """Evidence that these root field names are this application's records, or None."""
        roots = set(roots)
        for exact in self.exact_roots:
            if roots == set(exact):
                return ("its records carry exactly the field set this application writes: "
                        + ", ".join(sorted(exact)))
        if not self.requires and not self.markers:
            return None
        missing = [r for r in self.requires if r not in roots]
        if missing:
            return None
        hit = [m for m in self.markers if m in roots]
        if len(hit) < self.min_markers:
            return None
        return (f"its records carry {', '.join(self.requires)} and {len(hit)} of this application's "
                f"{len(self.markers)} marker fields ({', '.join(hit)})")

    def match_format(self, fmt):
        """Evidence that clp detect' bundled-format name is this application's, or None."""
        if fmt in self.formats:
            return f"clp detect matched its lines against the bundled {fmt} format"
        return None


CLAUDE_CODE = Application(
    name="claude-code", route=SPECIALISED, acquire="session", agent="claude",
    # Every record of a Claude Code transcript carries the session it belongs to and its own id.
    requires=("sessionId", "uuid"),
    markers=("type", "sessionId", "uuid", "parentUuid", "timestamp", "cwd", "gitBranch",
             "isSidechain", "userType", "version", "entrypoint", "message", "toolUseResult",
             "requestId"),
    min_markers=6,
    skips=("the categorise stage, because its seven categories are pre-trained: the plugin ships "
           "their classification, so nothing is classified at run time. Acquire also builds the graph of launches, retries and "
           "lost results, which plain logs do not have"),
)

CODEX = Application(
    # Recognised, and on the general route: it is an agent harness too, but nothing about its record
    # graph or its categories is registered here, so its structure and categories are discovered
    # like any other application's. What being recognised buys it is the right acquire -- a rollout
    # compressed as a session, so the arrays its payload records carry become queryable columns
    # instead of one opaque string. This is what "an application earns a route by being registered"
    # looks like at the acquire stage alone.
    name="codex", route=GENERAL, acquire="session", agent="codex",
    exact_roots=(("timestamp", "ordinal", "type", "payload"),
                 ("timestamp", "type", "payload")),
)

VLLM = Application(
    name="vllm", route=GENERAL, acquire="folder",
    # After acquire its records are whatever structurize.py wrote, which is a fixed short schema --
    # so an exact set is evidence where a subset rule would match half the world's logs.
    exact_roots=(("timestamp", "logger", "level", "worker", "message"),
                 ("timestamp", "logger", "level", "message")),
    formats=("vllm-sflow", "vllm-raw"),
)

APPLICATIONS = (CLAUDE_CODE, CODEX, VLLM)


def application(name):
    for app in APPLICATIONS:
        if app.name == name:
            return app
    raise AnalyzeError(f"no application named {name!r} is registered; "
                       f"registered: {', '.join(a.name for a in APPLICATIONS)}")


def identify_from_records(roots):
    """(Application, evidence) for a set of root record field names, or (None, None)."""
    for app in APPLICATIONS:
        why = app.match_records(roots)
        if why:
            return app, why
    return None, None


def identify_from_format(fmt):
    """(Application, evidence) for a clp detect bundled-format name, or (None, None)."""
    for app in APPLICATIONS:
        why = app.match_format(fmt)
        if why:
            return app, why
    return None, None


# --- small helpers ------------------------------------------------------------------------------


def human_bytes(n):
    """The same sizes clp-common.sh's human_bytes prints, so a size reads the same everywhere."""
    units = ("B", "KB", "MB", "GB", "TB")
    value, i = float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{int(value)} B" if i == 0 else f"{value:.1f} {units[i]}"


def dir_bytes(path):
    total = 0
    for dirpath, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def sha256_file(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_to_epoch(text):
    """An archive metadata createdAt ("2026-09-24T15:34:33Z") as a UNIX timestamp."""
    from datetime import datetime, timezone
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def quoted(argv):
    return " ".join(shlex.quote(a) for a in argv)


class Runner:
    """Runs the plugin's commands. Their output is the narration and goes to stderr, so this
    command's own stdout stays KEY=VALUE only; every command run is recorded so the caller can
    print the audit trail."""

    def __init__(self, clp_s=None, quiet=False):
        self.clp_s = clp_s
        self.quiet = quiet
        self.ran = []

    def env(self):
        env = dict(os.environ)
        if self.clp_s:
            env["CLP_S_BIN"] = self.clp_s
        return env

    def run(self, argv, capture=True, record=True):
        """(returncode, output). With capture the output is returned and echoed to stderr;
        without it the child writes straight through to stderr."""
        if record:
            self.ran.append(argv)
        if not self.quiet:
            print(f"[clp] running: {quoted(argv)}", file=sys.stderr, flush=True)
        if not capture:
            proc = subprocess.run(argv, stdout=sys.stderr, stderr=sys.stderr, env=self.env())
            return proc.returncode, ""
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=self.env())
        out = (proc.stdout or "") + (proc.stderr or "")
        if not self.quiet and out.strip():
            print("\n".join(f"  | {line}" for line in out.strip().splitlines()[:400]),
                  file=sys.stderr, flush=True)
        return proc.returncode, out

    def check(self, argv, what, archive=None, capture=True):
        rc, out = self.run(argv, capture=capture)
        if rc != 0:
            note = ""
            if archive:
                note = B.clp_s_diagnosis(B.resolve_clp_s(self.clp_s), archive, out)
            raise AnalyzeError(f"{what} failed (exit {rc}): {quoted(argv)}{note}")
        return out


# --- reading the records ------------------------------------------------------------------------


def archive_roots(runner, archives_dir):
    """{root field name: records carrying it} for every archive under archives_dir, from its merged
    schema tree. No search runs: the tree is archive metadata."""
    with tempfile.TemporaryDirectory(prefix="clp-classify-") as tmp:
        out_file = os.path.join(tmp, "field-counts.ndjson")
        runner.check(command("schema", "--field-counts",
                             "--field-counts-file", out_file, archives_dir),
                     "reading the archive's field counts", archive=archives_dir)
        roots = {}
        with open(out_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                for field in json.loads(line).get("fields", []):
                    key = field.get("key")
                    if key:
                        roots[key] = max(roots.get(key, 0), field.get("count") or 0)
    return roots


def detect(runner, paths, extensions=None, recursive=True):
    """clp detect' report for these paths, parsed: per-file format, root field names and
    timestamp key, plus the compress commands it suggests."""
    argv = command("detect", "--show-lines", "3", "--show-records", "1")
    if extensions:
        argv += ["--extensions", extensions]
    if not recursive:
        argv += ["--no-recursive"]
    argv += list(paths)
    rc, out = runner.run(argv)
    if rc != 0:
        first = next((line for line in out.splitlines() if line.startswith("error:")), out.strip())
        first = first[len("error:"):].strip() if first.startswith("error:") else first
        hint = ""
        if "no log files matched" in first:
            listed = sorted(os.listdir(paths[0])) if os.path.isdir(paths[0]) else []
            hint = ("\n  It holds " + (f"{len(listed)} entries: " + ", ".join(listed[:10])
                                       if listed else "nothing") +
                    ".\n  Only files with one of those extensions count as logs. Pass "
                    "--extensions '*' to take every file, or point at the log files themselves.")
        raise AnalyzeError(f"that target is not logs: {first}.{hint}")
    return parse_detect(out)


def parse_detect(out):
    """clp detect' report as {"files": [...], "suggest": [...]}.

    A file entry carries name, format (its `format:` word), bundled (the bundled text format it
    matched, if any), roots (top-level field names of its JSON records), timestamp and skip. The
    roots come from the `roots:` line, which lists every top-level field; the `fields:` line lists
    only the first 30 paths, so it is read only when a report has no `roots:` line.
    """
    files, suggest, current = [], [], None
    for line in out.splitlines():
        if line.startswith("== "):
            current = {"name": line[3:].split(" (")[0], "format": "", "bundled": "",
                       "roots": [], "timestamp": "", "skip": ""}
            files.append(current)
        elif current is None:
            continue
        elif line.startswith("format:"):
            rest = line.split(":", 1)[1].strip()
            current["format"] = rest.split(":")[0].strip()
            match = re.search(r"bundled (\S+) format", rest)
            if match:
                current["bundled"] = match.group(1)
        elif line.startswith("fields:"):
            paths = [part.strip().split(":")[0].strip()
                     for part in line.split(":", 1)[1].split(" · ")]
            current["roots"] = sorted({p.split(".")[0] for p in paths
                                       if p and not p.startswith("(+")})
        elif line.startswith("roots:"):
            # Printed after `fields:`, so it replaces the partial set read from there.
            names = [part.strip().split(" (")[0] for part in line.split(":", 1)[1].split(" · ")]
            current["roots"] = sorted({n for n in names if n and not n.startswith("(+")})
        elif line.startswith("timestamp:"):
            current["timestamp"] = line.split(":", 1)[1].strip()
        elif line.startswith("skip:"):
            current["skip"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUGGEST "):
            suggest.append(shlex.split(line[len("SUGGEST "):]))
    return {"files": files, "suggest": suggest}


def app_of_detected(report):
    """(Application, evidence) for a clp detect report: the one application every log file in
    it belongs to, or (None, evidence) when they disagree or none is registered."""
    usable = [f for f in report["files"] if f["format"] not in NOT_LOGS_FORMATS]
    if not usable:
        seen = sorted({f"{f['name']}: {f['format']}" for f in report["files"]})
        raise AnalyzeError("that target is not logs. clp detect found nothing it could "
                           "compress:\n  " + "\n  ".join(seen) +
                           "\n  Point it at files of JSON records or text lines instead.")
    found = {}
    for entry in usable:
        app, why = (identify_from_format(entry["bundled"]) if entry["bundled"]
                    else identify_from_records(entry["roots"]))
        found.setdefault(app.name if app else UNRECOGNISED, (app, why, entry["name"]))
    if len(found) == 1:
        app, why, name = next(iter(found.values()))
        if app:
            return app, f"{why} (read from {os.path.basename(name)})"
        return None, ("no registered application matches these "
                      + (f"{usable[0]['format']} records carrying "
                         + ", ".join(usable[0]["roots"][:8]) if usable[0]["roots"]
                         else f"{usable[0]['format']} lines, and no bundled format does either"))
    return None, ("the files are not all one application (" +
                  ", ".join(sorted(found)) + "), so the general route takes them together")


# --- classification -----------------------------------------------------------------------------


SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                           r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def looks_like_archive(path):
    """The Python twin of clp-common.sh's looks_like_clp_s_archive_dir."""
    return (os.path.isdir(path) and os.path.isfile(os.path.join(path, "header"))
            and os.path.isfile(os.path.join(path, "table_metadata")))


def archive_dirs_under(path):
    if looks_like_archive(path):
        return [path]
    if not os.path.isdir(path):
        return []
    return sorted(child for child in glob.glob(os.path.join(path, "*")) if looks_like_archive(child))


def archive_metadata(archives_dir):
    """The .yscope-clp-archive.json a compression wrapper left beside the archive, or None."""
    for candidate in (archives_dir, os.path.dirname(os.path.abspath(archives_dir))):
        path = os.path.join(candidate, ".yscope-clp-archive.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, ValueError):
                return None
    return None


def classify(target, runner, claude_home=None, extensions=None, recursive=True):
    """What the target is, which application produced its logs, and the evidence for both.

    The order is by how directly each case answers the question. A bundle manifest and an archive's
    own schema tree are first-party records of what was compressed; a file's first 128 KiB is next;
    a path or a directory layout is only ever used to *find* records to read, never to decide.
    """
    if target is None:
        return {"form": "none", "target": None, "app": None, "app_evidence": [], "why": ""}

    expanded = os.path.expanduser(target)
    result = {"target": os.path.abspath(expanded) if os.path.exists(expanded) else target,
              "app": None, "app_evidence": [], "why": "", "archive": None, "bundle": None,
              "main_log": None, "claude_home": None, "session_id": None, "detect": None}

    # 1. A bundle: its own manifest says which session it holds, so nothing has to be inferred.
    manifest_path = os.path.join(expanded, "manifest.json")
    if os.path.isdir(expanded) and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError) as err:
            raise AnalyzeError(f"{expanded} holds a manifest.json that cannot be read: {err}")
        if "session_id" not in manifest:
            raise AnalyzeError(f"{expanded} holds a manifest.json that is not a session bundle's "
                               "(it has no session_id)")
        catalog = os.path.join(expanded, "catalog.sqlite")
        result.update(form="bundle", bundle=os.path.abspath(expanded), app=CLAUDE_CODE,
                      manifest=manifest, session_id=manifest["session_id"],
                      catalog_present=os.path.isfile(catalog))
        result["app_evidence"] = [
            f"manifest.json (layout {manifest.get('layout')}) names Claude Code session "
            f"{manifest['session_id']} under {manifest.get('source_root')}",
            f"catalog.sqlite is {'present' if result['catalog_present'] else 'missing'}; "
            f"{len(manifest.get('sources', []))} source files are recorded",
        ]
        result["why"] = ("a session bundle: its manifest names the Claude Code session it was "
                         "built from")
        # The manifest records each source's path relative to the session's root, so the log the
        # bundle was built from can be found again and its freshness checked.
        main = next((s for s in manifest.get("sources", []) if s.get("kind") == "main"), None)
        if main and manifest.get("source_root"):
            candidate = os.path.join(manifest["source_root"], main["path"])
            if os.path.isfile(candidate):
                result["main_log"] = candidate
                if os.path.isdir(os.path.join(manifest["source_root"], "projects")):
                    result["claude_home"] = manifest["source_root"]
                result["app_evidence"].append(
                    f"the session log it was built from is still there: {candidate}")
        return result

    # 2. An archive: ask its records, from the merged schema tree, which application wrote them.
    archives = archive_dirs_under(expanded) if os.path.isdir(expanded) else []
    if archives:
        roots = archive_roots(runner, os.path.abspath(expanded))
        app, why = identify_from_records(roots)
        total = max(roots.values()) if roots else 0
        metadata = archive_metadata(os.path.abspath(expanded))
        result.update(form="archive", archive=os.path.abspath(expanded), app=app,
                      metadata=metadata, roots=roots)
        top = sorted(roots.items(), key=lambda kv: -kv[1])[:10]
        result["app_evidence"] = [
            f"{len(archives)} clp-s archive(s) under it; the merged schema tree has "
            f"{len(roots)} root fields over about {total} records",
            "most-carried root fields: " + ", ".join(f"{k} ({v})" for k, v in top),
        ]
        if app:
            result["app_evidence"].append(why)
            result["why"] = f"{app.name} logs, read from the archive's own records: {why}"
        else:
            result["why"] = ("logs from no registered application, read from the archive's own "
                             "records, so the general route analyses them")
        if metadata and metadata.get("session", {}).get("file"):
            result["main_log"] = metadata["session"]["file"]
            result["session_id"] = metadata["session"].get("id")
            root = metadata.get("sourceRoot") or ""
            if os.path.basename(root.rstrip("/")) == "projects":
                result["claude_home"] = os.path.dirname(root.rstrip("/"))
            result["app_evidence"].append(
                f"its metadata names the session log it was compressed from: {result['main_log']}")
        return result

    # 3. A session id: not a path, so the Claude home layout is used to find the log to read.
    if not os.path.exists(expanded) and os.sep not in target and SESSION_ID_RE.match(target):
        home = L.check_claude_home(claude_home)
        main_log = L.find_main_log(target, home)
        result.update(target=target, form="session-id", main_log=main_log, claude_home=home,
                      session_id=target)
        result["app_evidence"].append(
            f"{target} is not a path; one main log with that id is under {home}/projects: {main_log}")
        return _classify_log_file(result, main_log, runner, claude_home=home)

    if not os.path.exists(expanded):
        raise AnalyzeError(
            f"no such target: {target}. It is not a file or directory, and it is not a session id "
            f"under {L.check_claude_home(claude_home)}/projects "
            f"(a session id looks like 0f3c1d2e-4a5b-6c7d-8e9f-0a1b2c3d4e5f). Pass a log file, a "
            f"folder of log files, a CLP archive directory, a session bundle directory, or a "
            f"session id; with no target at all it lists the sessions it can see.")

    # 4. A log file, or a folder of them: clp detect reads the records.
    if os.path.isfile(expanded):
        result["form"] = "log-file"
        return _classify_log_file(result, os.path.abspath(expanded), runner, claude_home=claude_home)

    if os.path.isdir(expanded):
        report = detect(runner, [os.path.abspath(expanded)], extensions=extensions,
                        recursive=recursive)
        app, why = app_of_detected(report)
        result.update(form="log-folder", app=app, detect=report)
        result["app_evidence"] = [
            f"{len(report['files'])} log file(s) found under it; "
            + ", ".join(sorted({f["format"] for f in report["files"]})) + " formats",
            why,
        ]
        result["why"] = (f"{app.name} logs, read from the files' own records: {why}" if app
                         else f"unrecognised logs: {why}")
        return result

    raise AnalyzeError(f"{target} is neither a file nor a directory, so it cannot hold logs.")


def _classify_log_file(result, path, runner, claude_home=None):
    """Finish classifying a single log file: its records name the application, and for one whose
    acquire needs a session layout, that layout is resolved from the file's own path."""
    report = detect(runner, [path])
    app, why = app_of_detected(report)
    entry = report["files"][0]
    result.update(app=app, detect=report, main_log=result.get("main_log") or path)
    result["app_evidence"] += [
        f"{entry['format']} records in the first 128 KiB of {os.path.basename(path)}"
        + (f"; {entry['timestamp']}" if entry["timestamp"] else ""),
        why,
    ]
    if app is not None and app.acquire == "session":
        if not path.endswith(".jsonl"):
            raise AnalyzeError(f"records say these are {app.name} logs, whose logs are acquired one "
                               f"session log at a time, but {path} is not a .jsonl session log.")
        result["session_id"] = os.path.basename(path)[: -len(".jsonl")]
    if app is CLAUDE_CODE:
        try:
            main_log, home = L.resolve_session(path, claude_home)
        except B.BundleError as err:
            raise AnalyzeError(f"records say this is a Claude Code session log, but it cannot be "
                               f"resolved as one: {err}")
        result.update(main_log=main_log, claude_home=home,
                      session_id=os.path.basename(main_log)[: -len(".jsonl")])
        if home:
            result["app_evidence"].append(
                f"it sits in a Claude home, so the session's agent transcripts, tasks and file "
                f"snapshots are reachable: {home}")
        else:
            result["app_evidence"].append(
                "it does not sit in a Claude home (projects/<project>/<id>.jsonl), so the agent "
                "transcripts, tasks and file snapshots that a bundle needs are out of reach")
    result["why"] = (f"{app.name} logs, read from the file's own records: {why}" if app
                     else f"unrecognised logs: {why}")
    return result


# --- what is already prepared -------------------------------------------------------------------


def archives_root(runner, requested=None):
    """The archives root `clp compress` would use, asked of it rather than
    worked out again here -- so the precedence (flag, env, config file, ${TMPDIR:-/tmp}) has one
    implementation."""
    argv = command("compress folder")
    if requested:
        argv += ["--archives-root", requested]
    argv.append("--show-archives-root")
    out = runner.check(argv, "asking where archives go")
    for line in out.splitlines():
        if line.startswith("Archives root:"):
            return line.split(":", 1)[1].strip()
    raise AnalyzeError("clp compress folder --show-archives-root printed no archives root")


def bundles_root(requested=None):
    """Where bundles go: the flag, then CLP_S_BUNDLES_ROOT, then ${TMPDIR:-/tmp}/yscope-clp-bundles
    -- the same shape as the archives root's default, and nothing about a home or an install."""
    root = (requested or os.environ.get("CLP_S_BUNDLES_ROOT")
            or os.path.join(os.environ.get("TMPDIR", "/tmp"), "yscope-clp-bundles"))
    root = os.path.abspath(os.path.expanduser(root))
    refuse_broad_dir(root, "--bundles-root")
    return root


BROAD_DIRS = ("/", "/home", "/Users")


def refuse_broad_dir(path, flag):
    """Refuse a directory that holds a whole machine's or a whole agent's files, as
    clp-common.sh's is_broad_output_dir does for archive roots."""
    real = os.path.abspath(path)
    broad = set(BROAD_DIRS)
    for home in (os.environ.get("HOME"), os.environ.get("CODEX_HOME")):
        if not home:
            continue
        home = os.path.abspath(os.path.expanduser(home))
        broad.update({home, os.path.join(home, ".claude"), os.path.join(home, ".claude", "projects"),
                      os.path.join(home, ".codex"), os.path.join(home, ".codex", "sessions")})
    if real in broad or re.fullmatch(r"/(home|Users)/[^/]+(/\.(claude|codex)(/(projects|sessions))?)?",
                                     real):
        raise AnalyzeError(f"refusing broad {flag}: {real}")
    return real


def existing_session_archive(root, main_log, sha):
    """The newest archive under `root` compressed from exactly this session log, unchanged since --
    matched on the sha256 its metadata recorded, so a reused archive is the same bytes.
    ({} when there is none; also returns how many matched.)"""
    matches = []
    for child in sorted(glob.glob(os.path.join(root, "*"))):
        metadata = archive_metadata(child) if os.path.isdir(child) else None
        session = (metadata or {}).get("session") or {}
        if not session.get("file"):
            continue
        if os.path.abspath(session["file"]) != os.path.abspath(main_log):
            continue
        if session.get("sha256") != sha:
            continue
        if not archive_dirs_under(child):
            continue
        matches.append((metadata.get("createdAt", ""), child, metadata))
    if not matches:
        return {}
    created, path, metadata = sorted(matches)[-1]
    return {"dir": path, "metadata": metadata, "created": created, "matches": len(matches),
            "why": (f"its metadata names this session log and the sha256 it recorded "
                    f"({sha[:12]}…) is the log's sha256 now")}


def newest_mtime(paths):
    """When anything at or under these paths was last modified. A folder's own mtime does not move
    when a log inside it is appended to, so every file under it is looked at."""
    newest = 0.0
    for path in paths:
        if os.path.isdir(path):
            for dirpath, _, names in os.walk(path):
                for name in names:
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                    except OSError:
                        pass
        else:
            try:
                newest = max(newest, os.path.getmtime(path))
            except OSError:
                pass
    return newest


def existing_archive_at(output_dir):
    """The archive already sitting at an explicit --output-dir, or {}."""
    if not output_dir or not archive_dirs_under(output_dir):
        return {}
    return {"dir": os.path.abspath(output_dir), "metadata": archive_metadata(output_dir),
            "created": "", "matches": 1,
            "why": "the --output-dir given already holds a clp-s archive"}


def existing_log_archive(root, paths):
    """The newest archive under `root` compressed from exactly these paths, nothing under them
    touched since -- matched on the source paths its metadata recorded and their mtimes against the
    archive's createdAt. The sizes cannot be compared: with --structurize the recorded input size is
    the converted JSONL's, not the log file's."""
    wanted = sorted(os.path.abspath(p) for p in paths)
    modified = newest_mtime(wanted)
    matches = []
    for child in sorted(glob.glob(os.path.join(root, "*"))):
        metadata = archive_metadata(child) if os.path.isdir(child) else None
        source = (metadata or {}).get("source") or {}
        if sorted(os.path.abspath(p) for p in source.get("paths") or []) != wanted:
            continue
        if not archive_dirs_under(child):
            continue
        try:
            created = iso_to_epoch(metadata["createdAt"])
        except (KeyError, ValueError):
            continue
        if created < modified:
            continue
        matches.append((metadata["createdAt"], child, metadata))
    if not matches:
        return {}
    created, path, metadata = sorted(matches)[-1]
    return {"dir": path, "metadata": metadata, "created": created, "matches": len(matches),
            "why": (f"its metadata names exactly the {len(wanted)} source path"
                    f"{'s' if len(wanted) > 1 else ''} given, and it was written at {created}, "
                    f"after anything under "
                    f"{'them' if len(wanted) > 1 else 'it'} was last modified")}


def bundle_state(bundle_dir, main_log=None, sha=None):
    """What a bundle directory holds: whether it is this session's, whether its catalog can be
    opened, and what would have to run to make it usable."""
    state = {"dir": bundle_dir, "exists": os.path.isdir(bundle_dir), "manifest": None,
             "fresh": None, "catalog": False, "catalog_error": "", "why": ""}
    if not state["exists"]:
        return state
    path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.isfile(path):
        state["why"] = "the directory exists but holds no manifest.json, so it is not a bundle"
        return state
    try:
        with open(path, encoding="utf-8") as handle:
            state["manifest"] = json.load(handle)
    except (OSError, ValueError) as err:
        state["why"] = f"its manifest.json cannot be read ({err})"
        return state
    manifest = state["manifest"]
    state["layout_current"] = manifest.get("layout") == B.MANIFEST_LAYOUT
    if sha is not None:
        main = next((s for s in manifest.get("sources", []) if s.get("kind") == "main"), None)
        state["fresh"] = bool(main and main.get("sha256") == sha)
        state["why"] = (f"its manifest records the same main log sha256 ({sha[:12]}…)"
                        if state["fresh"] else
                        "its manifest records a different main log sha256, so the session changed "
                        "since it was built")
    else:
        state["why"] = ("its manifest names session " + str(manifest.get("session_id")) +
                        "; the source log was not reachable, so freshness was not checked")
    try:
        B.open_catalog(bundle_dir).close()
        state["catalog"] = True
    except B.BundleError as err:
        state["catalog_error"] = str(err)
    return state


# --- preparation --------------------------------------------------------------------------------


def adjacent_logs(main_log, claude_home=None):
    """The agent and workflow logs in the session's own directory beside its main log: what a bundle
    would compress besides the main log. Read from the file layout before anything is compressed, so
    the session is compressed once, as a bundle or as one archive, and never both."""
    _, _, sources = L.inventory(main_log, claude_home)
    return [s for s in sources if s["where"] == "archive" and s["kind"] != "main"]


def count_launches(runner, archives_dir):
    """How many records of the session's main log launched an agent or a workflow. This is the
    count a user would otherwise have to know to run: a non-zero answer means the main log records
    only the launches and the rest of the session is in files a bundle collects."""
    out = runner.check(command("search", "--count", archives_dir, LAUNCH_KQL),
                       "counting agent and workflow launches", archive=archives_dir)
    total, rows = 0, 0
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "count" in row:
            total += int(row["count"])
            rows += 1
    if rows == 0:
        raise AnalyzeError("the launch count printed no count row; "
                           f"ran: {quoted(command('search', '--count', archives_dir, LAUNCH_KQL))}")
    return total


def compress_session_argv(main_log, claude_home, archives_root_value=None, output_dir=None,
                          clp_s=None, agent="claude"):
    """clp compress session for one session's main log. --structurize-arrays and
    --timestamp-key timestamp are that wrapper's own doing; only the location is chosen here."""
    argv = command("compress session", "--agent", agent, "--session-file", main_log,
                   "--timestamp-key", "timestamp")
    if claude_home and agent == "claude":
        argv += ["--claude-root", os.path.join(claude_home, "projects")]
    if output_dir:
        argv += ["--output-dir", output_dir]
    elif archives_root_value:
        argv += ["--archives-root", archives_root_value]
    if clp_s:
        argv += ["--clp-s-bin", clp_s]
    return argv


def compress_folder_argv(suggested, archives_root_value=None, output_dir=None):
    """The compress command clp detect suggested, with this run's archive location added.

    The flags come from the detector's report -- the format, the timestamp key, whether the text
    needs structurizing -- so nothing about a log format is decided here.
    """
    if suggested[:3] != ["clp", "compress", "folder"]:
        raise AnalyzeError(f"clp detect suggested a command this wrapper does not run: "
                           f"{quoted(suggested)}")
    argv = command("compress folder", *suggested[3:])
    if output_dir:
        argv += ["--output-dir", output_dir]
    elif archives_root_value:
        argv += ["--archives-root", archives_root_value]
    return argv


def archive_dir_from_output(out):
    """The archive directory a compression wrapper reported making."""
    for line in out.splitlines():
        if line.startswith("Archives dir:"):
            return line.split(":", 1)[1].strip()
    raise AnalyzeError("the compression wrapper printed no 'Archives dir:' line")
