"""session_layout_claude - where Claude Code keeps one session's files, and what each of them is.

Every directory and file name of a Claude Code session is written down here: the rules that say what
each file is and where it goes, which kinds become archives and which carry records, how a session id
or a session log reaches its main log and the Claude home beside it, and the walk that collects the
tasks and file snapshots kept outside the session directory. `inventory` is what the rest reads: the
list of every file of one session, which bundle_build copies, compresses and catalogs without naming a
directory itself. Stdlib only.

A Claude home holds projects/<project>/<session id>.jsonl (the main log) beside
projects/<project>/<session id>/ (that session's own directory), and tasks/<session id>/ and
file-history/<session id>/ next to projects/.
"""

import glob
import os
import re

import bundle as B

# kind, where it goes (an archive or files/), and a regex on the path relative to the session directory.
SESSION_RULES = [
    ("agent", "archive", r"^subagents/agent-[^/]+\.jsonl$"),
    ("agent-meta", "files", r"^subagents/agent-[^/]+\.meta\.json$"),
    ("agent-forked-skill", "files", r"^subagents/agent-[^/]+\.forked-skill(\.marker)?\.json$"),
    ("workflow-agent", "archive", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.jsonl$"),
    ("workflow-agent-meta", "files", r"^subagents/workflows/wf_[^/]+/agent-[^/]+\.meta\.json$"),
    ("workflow-journal", "archive", r"^subagents/workflows/wf_[^/]+/journal\.jsonl$"),
    ("workflow-run", "archive", r"^workflows/wf_[^/]+\.json$"),
    ("workflow-script", "files", r"^workflows/scripts/[^/]+\.js$"),
    ("tool-result", "files", r"^tool-results/[^/]+$"),
    ("session-title", "files", r"^custom-title\.json$"),
    ("classifier-error", "files", r"^auto-mode-classifier-error\.txt$"),
]
# the kind of a file no rule names: kept under files/ as it is, since only a log would be lost by that
UNCLASSIFIED_KIND = "unclassified"
# the kinds that are logs (one archive each), and those whose records the catalog lists as events
ARCHIVED_KINDS = ["main", "agent", "workflow-agent", "workflow-journal", "workflow-run"]
EVENT_KINDS = ("main", "agent", "workflow-agent")
# the record type that reports a file snapshot, and where that snapshot sits in the bundle
FILE_VERSION_RECORD = "file-history-delta"


def snapshot_file(backup_file_name):
    """The bundled path of the snapshot a file version refers to, under files/."""
    return f"files/file-history/{backup_file_name}"


def check_claude_home(path, default="~/.claude"):
    """The absolute Claude home, refusing a projects/ directory passed by mistake.

    Two flags name a Claude directory one level apart: --claude-home wants the
    directory that HOLDS projects/ (it also reads tasks/ and file-history/ from
    there), while the shell wrappers' --claude-root wants projects/ itself. Passing
    one where the other belongs finds no sessions and looks like an empty machine,
    so the wrong level is detected and the corrected path is named. Also expands ~,
    which the callers used to skip -- a quoted --claude-home '~/.claude' reached
    the tasks/ lookup unexpanded.
    """
    expanded = os.path.abspath(os.path.expanduser(path or default))
    if os.path.basename(expanded) == "projects":
        raise B.BundleError(
            f"{B.OPT_CLAUDE_HOME} wants the directory that holds projects/, not projects/ itself "
            f"(it also reads tasks/ and file-history/ from there). You passed {path}; "
            f"pass {os.path.dirname(expanded)}.")
    return expanded


def find_main_log(session_id, claude_home):
    """The main log of `session_id` under claude_home/projects; one session, or an error naming what
    was found instead."""
    hits = glob.glob(os.path.join(claude_home, "projects", "*", session_id + ".jsonl"))
    if len(hits) != 1:
        raise B.BundleError(f"expected one session {session_id} under {claude_home}/projects, found {len(hits)}")
    return hits[0]


def resolve_session(main_path, claude_home=None):
    """(main log, Claude home) of the session whose main log is main_path. The home is the directory
    that holds projects/, taken from the log's own path when the caller does not name one; it stays
    None for a log that does not sit in a Claude home, and then the tasks and file snapshots kept
    there are out of reach."""
    main_path = os.path.abspath(main_path)
    if not os.path.isfile(main_path) or not main_path.endswith(".jsonl"):
        raise B.BundleError(f"not a session log (a .jsonl file): {main_path}")
    if claude_home is None:
        project_dir = os.path.dirname(main_path)
        if os.path.basename(os.path.dirname(project_dir)) == "projects":
            claude_home = os.path.dirname(os.path.dirname(project_dir))
    return main_path, os.path.abspath(claude_home) if claude_home else None


def inventory(main_path, claude_home):
    """(session id, source root, [source]) for every file of the session. A source is a dict: name (its
    path relative to the session, which is also its path under files/), path (relative to the source
    root), full, kind, where (archive or files)."""
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
                    # A file Claude Code started writing after these rules were: kept, not refused. A log
                    # is the exception, since under files/ its records would be missing from the analysis.
                    if n.endswith(".jsonl"):
                        raise B.BundleError(f"unclassified log: {os.path.relpath(full, source_root)} "
                                            "(add a rule to session_layout_claude.SESSION_RULES or remove it)")
                    found.append(source(inner, full, UNCLASSIFIED_KIND, "files"))
    if claude_home:
        for kind in ("tasks", "file-history"):
            base = os.path.join(claude_home, kind, sid)
            for dirpath, _, names in os.walk(base):
                for n in names:
                    full = os.path.join(dirpath, n)
                    if dirpath != base:
                        raise B.BundleError(f"unclassified file (nested under {kind}): {os.path.relpath(full, source_root)}")
                    found.append(source(f"{kind}/{n}", full, kind.rstrip("s") if kind == "tasks" else kind, "files"))
    return sid, source_root, sorted(found, key=lambda s: s["name"])
