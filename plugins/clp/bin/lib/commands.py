"""
commands - the whole surface of `clp`: one table, and the rule for reading its
first argument.

`clp` is the only command in bin/ (bin/clp-s is the engine shim the wrappers
resolve, not a command anyone types). Everything a user invokes is a subcommand
of it, and the implementations all live next to this file in bin/lib/, reached
through subcommand.dispatch.

One argument is not a subcommand: the TARGET of the analysis router, which is
what `clp <log file | folder | archive | bundle | session id>` runs. So the first
argument is read like this, in this order:

  1. nothing at all            list the subcommands and exit 2 (a usage error)
  2. a name in the table       that subcommand, always -- a file in the working
                               directory called `search` is reached as ./search
  3. an option (-...)          the router, with no target: it lists the sessions
                               it can see and stops. `-h` is the router's help,
                               which carries the subcommand table with it
  4. target-shaped             the router, on that target: the argument holds a
                               '/', a '.' or a '~', names something that exists,
                               or is a session id (a UUID)
  5. anything else             error: unknown subcommand: <it>, and exit 2

No subcommand name holds a '/', a '.' or a '~', so rule 4 can never swallow a
mistyped subcommand: `clp searhc` names what was not understood, while
`clp ./searhc` and `clp logs/app.log` are targets.
"""

import os
import re
import sys

from subcommand import LIB_DIR, Group, dispatch, table_lines  # noqa: F401

PROG = "clp"
BIN_DIR = os.path.dirname(LIB_DIR)
# The one command, by absolute path. Every argv this plugin builds for itself
# starts here, so a printed command is one the user can retype.
CLP = os.path.join(BIN_DIR, PROG)

SUMMARY = ("Analyse logs with CLP: `clp <TARGET>` identifies which application wrote them,\n"
           "prepares what the analysis needs and names the next step; every step of that flow,\n"
           "and every drill-down after it, is one of the subcommands below.")

TARGET_HELP = ("TARGET is a log file, a folder of log files, a CLP archive directory, a session\n"
               "bundle directory or a Claude Code session id -- the one argument that is not a\n"
               "subcommand. It is read as a TARGET when it holds a '/', a '.' or a '~', names\n"
               "something that exists, or is a session id; no subcommand name holds any of those.")

# The general route's stages sit at the top level rather than under an `insights`
# group: they are the stages of the one pipeline `clp <TARGET>` hands off to, so a
# group would name the tool twice.
SUBCOMMANDS = {
    "list-sessions": ("list-sessions.sh",
                      "list the Claude Code and Codex sessions on this machine"),
    "detect": ("detect.py",
               "read the start of each log file and report what is there"),
    "compress": (Group(
        "Compress logs into a CLP archive directory.",
        {
            "session": ("compress-session.sh",
                        "one selected Claude Code or Codex session JSONL, its payload arrays "
                        "structurized into columns"),
            "folder": ("compress-folder.sh",
                       "log files and folders of log files, at the format `clp detect` found"),
            "status": ("compress_status.py",
                       "the state of a `clp compress folder` run: running, done, failed or died"),
        }), "compress logs into an archive, or report a running compression"),
    "search": ("search.sh",
               "search archives with KQL, including semantic search"),
    "decompress": ("decompress.sh",
                   "decompress an archive directory into an output directory"),
    "schema": ("schema_tree_cli.py",
               "every field of an archive, its type drift, its record families"),
    "kql": ("kql_build_cli.py",
            "render a JSON filter as KQL, or check a query or a plan"),
    "session": (Group(
        "Measure one Claude Code session's trajectory, then score it.",
        {
            "turns": ("session_turns_cli.py",
                      "where a session's time went, per turn"),
            "measure": ("session_measure.py",
                        "run the seven category checks and write every figure of the report"),
            "score": ("session_score.py",
                      "apply a scale to the measured axis values and emit the scores as JSON"),
        }), "measure and score one Claude Code session"),
    "bundle": ("bundle_cli.py",
               "one session bundle: its catalog, and the records behind it"),
    "bundle-review": ("bundle_review_cli.py",
                      "a directory of bundles: what went wrong, where, how often"),
    "report": (Group(
        "Check a report against its facts file, or save it where the user asked.",
        {
            "check": ("report_check.py",
                      "flag figures and KQL fields a report's own inputs do not support"),
            "save": ("report_save.py",
                     "save a finished report as Markdown, HTML, PDF or a page to publish"),
        }), "check a finished report against its inputs, and save it"),
    "bootstrap": ("insights-bootstrap.sh",
                  "sample an archive's fields, shape frequencies and cache"),
    "baseline-plan": ("insights_baseline_plan.py",
                      "write a run's app-agnostic baseline queries to a plan file"),
    "extract": ("insights_extract.py",
                "build the report writer's prompt pieces from a classification"),
    "run": ("insights_run.py",
            "execute a query plan, reporting each entry as it completes"),
    "focus": ("insights_focus.py",
              "turn the user's chosen focus into queries for the plan pool"),
    "facts": ("insights_facts.py",
              "compute every number of a log-shape-baseline report in code"),
    "shape-cache": ("shape_cache.py",
                    "the log shape classification cache: hit, growth, new shapes"),
    "shape-cluster": ("shape-cluster.sh",
                      "merge semantically similar log shapes before classifying"),
}

SESSION_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def command(subcommand, *args):
    """argv for `clp <subcommand> ...`, as a user would type it.

    `subcommand` may name a nested one ("compress folder", "session measure").
    """
    return [CLP, *subcommand.split(), *[str(a) for a in args]]


def search_argv():
    """The default search wrapper: `clp search`, which is what --search-wrapper replaces."""
    return command("search")


def table_text():
    """The subcommand table, as the help and the usage both print it."""
    return "Subcommands:\n" + "\n".join(table_lines(SUBCOMMANDS))


def is_target(arg):
    """Whether this first argument is the router's TARGET rather than a subcommand name.

    Called only for an argument that is not in the table, so a subcommand name
    always wins over a path that happens to share it.
    """
    if arg.startswith("-"):
        # Options with no target: the router, which lists the sessions and stops.
        return True
    if any(c in arg for c in "/.~"):
        return True
    return bool(SESSION_ID.match(arg)) or os.path.exists(arg)


def usage(stream):
    print(f"Usage:\n"
          f"  {PROG} <TARGET> [options]    analyse these logs: identify the application that\n"
          f"                              wrote them, prepare what the analysis needs, and\n"
          f"                              name the next step\n"
          f"  {PROG} <SUBCOMMAND> [...]   one step of that flow, or one drill-down, on its own\n"
          f"\n{TARGET_HELP}\n\n{table_text()}\n\n"
          f"Run `{PROG} <SUBCOMMAND> --help` for that subcommand's own options, and\n"
          f"`{PROG} --help` for the target route's own options.", file=stream)


def router(argv):
    """The analysis router: `clp <TARGET>`, and `clp --help`, which is its help."""
    import analyze_cli
    sys.argv = [PROG, *argv]
    return analyze_cli.main()


def main(argv):
    if not argv:
        usage(sys.stderr)
        return 2
    first = argv[0]
    if first in SUBCOMMANDS:
        return dispatch(PROG, SUMMARY, SUBCOMMANDS, argv)
    if is_target(first):
        return router(argv)
    print(f"error: unknown subcommand: {first}", file=sys.stderr)
    usage(sys.stderr)
    return 2
