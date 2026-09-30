#!/usr/bin/env python3
"""
clp - one command for analysing logs with CLP. Given a TARGET it is the door into the analysis
flow: which application produced these logs, and everything deterministic that has to happen
before a model and a user take over.

Usage:
  clp [TARGET] [options]        this route: classify the target, prepare it, name the next step
  clp <SUBCOMMAND> [...]        one step of that flow, or one drill-down, on its own

TARGET is a log file, a folder of log files, a CLP archive directory, a session bundle directory,
or a Claude Code session id. With no TARGET it lists the sessions it can see and stops; it never
guesses which logs you meant.

TARGET is the one argument that is not a subcommand, and the first argument is read as one only
when it cannot be a subcommand: a name in the table below is always that subcommand, an argument
holding a '/', a '.' or a '~', naming something that exists, or shaped like a session id is the
TARGET, and anything else is a mistyped subcommand and is named as one. No subcommand holds any of
those characters, so `clp searhc` says what it did not understand while `clp ./searhc` is a target.

There is one pipeline (acquire, structure, categorise, measure, report), and it runs on logs. A
Claude Code session is logs from one particular application, just as a vLLM worker log is. So the
only question asked is which application produced these logs, and then whether that application
has a registered optimisation:

  claude-code   the specialised route. Its file layout and its record graph are known in advance,
                so acquire also builds the graph of launches, retries and lost results; its seven
                categories are pre-trained, shipped with the plugin, so categorise classifies nothing
                at run time.
  codex, vllm   recognised, and on the general route. Recognition gets them the right acquire: a
                Codex rollout is compressed as a session so its payload arrays become columns, and a
                vLLM text log is structurized. The analysis after that is the general one.
  anything else the general route on its own terms: discover the structure and the categories from
                the logs, and cache the classification per application. An application gets a
                specialised route by being registered here.

The application is read from the records, never from the path: an archive's from its merged schema
tree, a file's from the first 128 KiB that clp detect reads. A path, a directory layout or a
bundle manifest is only ever used to find records to read. The classification and its evidence are
printed before any work starts, so no route is ever taken silently.

What it does:
  detect      classify the target, print the application, the route and the evidence for both
  prepare     compress what is not compressed, and for a Claude Code session run the launch count
              that decides whether a bundle is needed and build one when it is. Idempotent: pointed
              at something already prepared it reports that and does no work.
  hand off    print the next command as NEXT=. The stages that need a model and a user (the
              category pass, the focus questions, the report) belong to the skill and are not
              attempted here.

It runs its own subcommands and reimplements none of them: `clp list-sessions`, `clp detect`,
`clp compress session`, `clp compress folder`, `clp schema`, `clp search` and `clp bundle`. Every
one it runs is printed as a RAN= line.

The subcommands used after a finding (`clp search`, `clp bundle sql|evidence|who`, `clp schema`)
still work on their own, and the DRILL= lines name them for the artefacts in hand.

Options:
  --app NAME            Assert the application (claude-code, codex, vllm). Refused when the records
                        say a different one; it stands when they match none.
  --general             Take the general route even for an application that has a specialised one.
                        Acquire still follows the application, so a session log is still compressed
                        as a session; --general changes only which route reads the result.
  --claude-home DIR     The Claude home: the directory that HOLDS projects/, tasks/ and
                        file-history/ (default ~/.claude), not projects/ itself.
  --archives-root DIR   Parent directory for archives. Passed to `clp compress`, whose
                        own precedence (this, CLP_S_ARCHIVES_ROOT, the config file,
                        ${TMPDIR:-/tmp}/yscope-clp-archives) decides.
  --output-dir DIR      Exact archive directory, instead of an auto-named one under the root.
  --bundles-root DIR    Parent directory for bundles. Default: this, CLP_S_BUNDLES_ROOT, then
                        ${TMPDIR:-/tmp}/yscope-clp-bundles. A bundle goes in <root>/<session id>.
  --bundle-dir DIR      Exact bundle directory.
  --extensions EXT,..   Extensions to match in a folder target (default: `clp detect`'s).
  --no-recursive        Search only the top level of a folder target.
  --clp-s PATH          Use this clp-s for this invocation only, as CLP_S_BIN does.
  --force               Prepare again even when the target is already prepared.
  --dry-run             Print the plan and do nothing. Every check it still makes is read-only.
  --quiet               Do not echo the commands it runs.
  -h, --help            Show this help.

Output is KEY=VALUE on stdout, one per line, like every other subcommand here; their own output
is the narration and goes to stderr.

  TARGET=            what was given
  FORM=              bundle | archive | log-file | log-folder | session-id | none
  APP=               claude-code | vllm | unrecognised
  APP_FROM=          records | manifest | format | asserted
  WHY=               one sentence: the application and the evidence for it
  EVIDENCE=          the evidence in full, one line each
  ROUTE=             specialised | general
  ROUTE_WHY=         why that route, including when --general overrode a specialised one
  RAN=               each command run, in order
  ARCHIVE= ARCHIVE_BYTES= ARCHIVE_SIZE=     the archive, when there is one
  BUNDLE= BUNDLE_BYTES= BUNDLE_SIZE=        the bundle, when there is one
  LAUNCHES= BUNDLE_WHY=                     the launch count and what it decided
  PREPARED=          the artefacts this run made
  ALREADY_PREPARED=  the artefacts that were already there and were reused. Both appear when both
                     apply; at least one always does
  PLAN=              with --dry-run, each command that would run
  NEXT= NEXT_SKILL=  the next command, and the skill that owns the stages after it
  DRILL=             the drill-down commands for these artefacts

Exit codes: 0 classified (and prepared, unless --dry-run), 1 the target cannot be read or is not
logs at all, 2 usage error.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import commands as C  # noqa: E402
import analyze as A  # noqa: E402
import bundle as B  # noqa: E402


def out(key, value):
    print(f"{key}={value}", flush=True)


class Prep:
    """What this run found already there and what it made, so one line can report each."""

    def __init__(self):
        self.made = []
        self.reused = []

    def report(self):
        if self.made:
            out("PREPARED", ",".join(self.made))
        if self.reused:
            out("ALREADY_PREPARED", ",".join(self.reused))
        if not self.made and not self.reused:
            out("PREPARED", "nothing")


def report_artefact(key, path):
    size = A.dir_bytes(path)
    out(key, path)
    out(f"{key}_BYTES", size)
    out(f"{key}_SIZE", A.human_bytes(size))


def choose_route(classified, args):
    """The route, and the sentence that says why it is that one."""
    app = classified["app"]
    if app is None:
        return A.GENERAL, ("the general route: it discovers the structure and the categories and "
                           "caches the classification, which is what logs from an unregistered "
                           "application need")
    if app.route == A.SPECIALISED and args.general:
        return A.GENERAL, (f"the general route, forced by --general over {app.name}'s specialised "
                           f"one, so these logs get the same discovery and caching as any other "
                           f"application's")
    if app.route == A.SPECIALISED:
        return A.SPECIALISED, (f"{app.name}'s specialised route: it is registered, so the pipeline "
                               f"skips {app.skips}")
    return A.GENERAL, (f"the general route: {app.name} is recognised but has no specialised route "
                       f"registered, so its structure and categories are discovered like any "
                       f"other application's")


def assert_app(classified, name):
    """Apply --app: it stands over an unrecognised record set, and is refused by a recognised one
    that disagrees, because an assertion that silently loses to detection asserts nothing."""
    asserted = A.application(name)
    found = classified["app"]
    if found is asserted:
        classified["app_evidence"].append(f"--app {name} agrees with the records")
        return classified
    if found is not None:
        raise A.AnalyzeError(
            f"--app {name} disagrees with the records: they are {found.name}'s.\n"
            f"  evidence: {classified['why']}\n"
            f"  Drop --app to use what the records say, or --app {found.name} to confirm it.")
    classified["app"] = asserted
    classified["app_from"] = "asserted"
    classified["app_evidence"].append(
        f"--app {name} asserted it; the records matched no registered application on their own")
    classified["why"] = f"{name} logs, asserted with --app over records that match no application"
    if asserted.acquire == "session" and classified.get("main_log"):
        # The assertion also changes how the logs are acquired, so the layout that acquire needs is
        # resolved now rather than failing later inside the compressor.
        import session_layout_claude as L
        main_log, home = L.resolve_session(classified["main_log"], classified.get("claude_home"))
        classified.update(main_log=main_log, claude_home=home,
                          session_id=os.path.basename(main_log)[: -len(".jsonl")])
    return classified


def list_sessions(runner, args):
    """No target: show what there is and stop. Guessing which logs someone meant is not a thing a
    wrapper should do, so this route never prepares anything."""
    import session_layout_claude as L
    argv = A.command("list-sessions")
    if args.claude_home:
        argv += ["--claude-root", os.path.join(L.check_claude_home(args.claude_home), "projects")]
    out("TARGET", "")
    out("FORM", "none")
    out("APP", "")
    out("WHY", "no target was given, so nothing was classified and nothing was prepared")
    runner.run(argv, capture=False)
    out("RAN", A.quoted(argv))
    out("NEXT", f"{os.path.join(A.BIN_DIR, 'clp')} <session id, log file, folder, archive "
                f"or bundle>")
    return 0


def prepare_archive(classified, args, runner, prep):
    """The acquire stage: the archive this target's logs need, made or found.

    Which compressor runs is the application's, not the route's: a Claude Code session log is
    compressed as a session (its message.content array becomes columns) whether the specialised or
    the general route then reads it.
    """
    if classified.get("archive"):
        prep.reused.append("archive")
        out("ARCHIVE_WHY", "the target is the archive")
        return classified["archive"]
    if classified["form"] == "bundle":
        archives = os.path.join(classified["bundle"], "archives")
        out("ARCHIVE_WHY", "the bundle's own archives directory holds the session's records, one "
                           "archive per kind of log")
        if os.path.isdir(archives):
            prep.reused.append("archive")
            return archives
        return None

    root = A.archives_root(runner, args.archives_root)
    out("ARCHIVES_ROOT", root)
    app = classified["app"]

    if app is not None and app.acquire == "session":
        main_log = classified.get("main_log")
        if not main_log:
            raise A.AnalyzeError(
                f"{app.name}'s logs are acquired one session log at a time, and this target names "
                f"no single session log ({classified['form']}). Point at one session log, or its "
                f"session id.")
        sha = A.sha256_file(main_log)
        found = A.existing_archive_at(args.output_dir) if args.output_dir \
            else A.existing_session_archive(root, main_log, sha)
        argv = A.compress_session_argv(main_log, classified.get("claude_home"),
                                       archives_root_value=args.archives_root or root,
                                       output_dir=args.output_dir, clp_s=args.clp_s,
                                       agent=app.agent or "auto")
    else:
        suggested = classified["detect"]["suggest"]
        if not suggested:
            # Nothing can be compressed as it stands, and detect says why per file. The usual
            # case is text in a format with no bundled converter, which needs a parser first.
            skipped = [f for f in classified["detect"]["files"] if f.get("skip")]
            reasons = "\n  ".join(f"{f['name']}: {f['skip']}" for f in skipped) or \
                "clp detect gave no compression command and no reason"
            parser = any("--parser" in f["skip"] for f in skipped)
            raise A.AnalyzeError(
                "clp detect found nothing here it can compress as it stands:\n  " + reasons +
                ("\n  Write a parser for these lines, then run `clp detect "
                 f"{A.quoted([classified['target']])} --parser FILE`. Once the parser works it "
                 "prints the command that compresses them; point clp at the archive that makes."
                 if parser else ""))
        if len(suggested) > 1:
            raise A.AnalyzeError(
                "clp detect found files that need different compression settings. One archive "
                "takes one --timestamp-key, so this command will not pick between them:\n  "
                + "\n  ".join(A.quoted(s) for s in suggested) +
                "\n  Point clp at one group's files, or run the commands above and point it "
                "at each archive.")
        paths = [suggested[0][i + 1] for i, a in enumerate(suggested[0]) if a == "--path"]
        found = A.existing_archive_at(args.output_dir) if args.output_dir \
            else A.existing_log_archive(root, paths)
        argv = A.compress_folder_argv(suggested[0],
                                      archives_root_value=args.archives_root or root,
                                      output_dir=args.output_dir)

    if args.force:
        found = {}
    if found:
        prep.reused.append("archive")
        out("ARCHIVE_WHY", found["why"]
            + (f"; {found['matches']} archives matched and the newest was taken"
               if found["matches"] > 1 else ""))
        return found["dir"]
    if args.dry_run:
        out("PLAN", A.quoted(argv))
        return None
    result = runner.check(argv, "compressing the target")
    prep.made.append("archive")
    out("ARCHIVE_WHY", "nothing under the archives root was compressed from this target, so it was "
                       "compressed now")
    return A.archive_dir_from_output(result)


def prepare_bundle(classified, args, runner, prep, archive):
    """The Claude Code optimisation's own acquire: the graph of launches, retries and lost results,
    which lives in files the main log only points at.

    The decision is the launch count -- the query a user would otherwise have to know to run.
    """
    if classified["form"] == "bundle":
        main_log = classified.get("main_log")
        sha = A.sha256_file(main_log) if main_log and os.path.isfile(main_log) else None
        state = A.bundle_state(classified["bundle"], main_log, sha)
        out("BUNDLE_WHY", "the target is the bundle; " + state["why"])
        if state["fresh"] is False:
            out("BUNDLE_STALE", "the session has changed since this bundle was built; "
                                "rebuild it from the session to pick that up")
        if not state["catalog"]:
            if args.dry_run:
                out("PLAN", A.quoted(A.command("bundle", classified["bundle"], "rebuild")))
                return classified["bundle"]
            runner.check(A.command("bundle", classified["bundle"], "rebuild",
                                   *(["--clp-s", args.clp_s] if args.clp_s else [])),
                         "rebuilding the bundle's catalog")
            prep.made.append("catalog")
        else:
            prep.reused.append("bundle")
        return classified["bundle"]

    session_id = classified["session_id"]
    bundle_dir = args.bundle_dir or os.path.join(A.bundles_root(args.bundles_root), session_id)

    if archive is None:
        # --dry-run before the archive exists: the count cannot run, so what it would decide is
        # stated as a condition rather than guessed at.
        out("BUNDLE_WHY", "there is no archive yet, so the launch count that decides this has not "
                          "run; on a real run it decides right after the compression above")
        out("PLAN", A.quoted(A.command("search", "--count", "<ARCHIVE>", A.LAUNCH_KQL)))
        out("PLAN", A.quoted(A.command("bundle", bundle_dir, "build", "--session-id", session_id))
            + "   (only if that count is not zero)")
        return None

    launches = A.count_launches(runner, archive)
    out("LAUNCHES", launches)
    if launches == 0:
        out("BUNDLE_WHY", "no record launched an agent or a workflow, so the main archive is the "
                          "whole session and no bundle is needed")
        return None

    main_log = classified.get("main_log")
    sha = A.sha256_file(main_log) if main_log and os.path.isfile(main_log) else None
    state = A.bundle_state(bundle_dir, main_log, sha)
    usable = state["manifest"] and state["catalog"] and state["fresh"] is not False

    if usable and not args.force:
        prep.reused.append("bundle")
        out("BUNDLE_WHY", f"{launches} records launched an agent or a workflow, so the session's "
                          f"other files matter; a bundle for it is already there and " + state["why"])
        return bundle_dir
    if state["manifest"] and state["catalog_error"] and state["fresh"] is not False and not args.force:
        out("BUNDLE_WHY", f"{launches} records launched an agent or a workflow; the bundle is there "
                          f"but its catalog is not usable, so only the catalog is made again")
        if args.dry_run:
            out("PLAN", A.quoted(A.command("bundle", bundle_dir, "rebuild")))
            return bundle_dir
        runner.check(A.command("bundle", bundle_dir, "rebuild",
                               *(["--clp-s", args.clp_s] if args.clp_s else [])),
                     "rebuilding the bundle's catalog")
        prep.made.append("catalog")
        return bundle_dir

    if main_log is None or not os.path.isfile(main_log):
        out("BUNDLE_WHY", f"{launches} records launched an agent or a workflow, so a bundle would "
                          f"say what they did. The session log this archive was made from is not "
                          f"reachable, so no bundle can be built, and the measure stage runs on the "
                          f"archive alone.")
        return None

    argv = A.command("bundle", bundle_dir, "build", "--session-id", session_id)
    if classified.get("claude_home"):
        argv += ["--claude-home", classified["claude_home"]]
    if args.clp_s:
        argv += ["--clp-s", args.clp_s]
    if state["exists"]:
        argv += ["--force"]
    reason = (f"{launches} records launched an agent or a workflow, so the main log records only "
              f"the launches and the rest of the session is in files a bundle collects")
    if state["exists"] and not args.force:
        reason += f"; the bundle there is replaced because {state['why']}"
    out("BUNDLE_WHY", reason)
    if args.dry_run:
        out("PLAN", A.quoted(argv))
        return None
    runner.check(argv, "building the session bundle")
    prep.made.append("bundle")
    return bundle_dir


def hand_off(route, archive, bundle, dry_run=False):
    """The next command, which is the first stage that needs a model and a user behind it."""
    # With --dry-run nothing was compressed, so the paths are the ones the PLAN commands will print.
    placeholder = "<ARCHIVE>" if dry_run else ""
    if route == A.SPECIALISED:
        if bundle:
            out("NEXT", A.quoted(A.command("session", "measure", "--bundle", bundle)))
        elif archive or placeholder:
            out("NEXT", A.quoted(A.command("session", "measure", "--archive",
                                           archive or placeholder)))
        else:
            out("NEXT", "")
    elif archive or placeholder:
        out("NEXT", A.quoted(A.command("bootstrap", archive or placeholder)))
    else:
        out("NEXT", "")
    if dry_run and not archive:
        out("NEXT_PENDING", "the archive path above is the one the planned compression will print "
                            "as its 'Archives dir:'; on the specialised route the bundle replaces "
                            "--archive when the launch count is not zero")
    out("NEXT_SKILL", A.ANALYSIS_SKILL)
    out("NEXT_STAGES", "classify, focus and report need a model and a user, so they are the skill's "
                       "and are not attempted here")
    if archive:
        out("DRILL", A.quoted(A.command("search", archive, "<KQL>")))
        out("DRILL", A.quoted(A.command("schema", archive)))
    if bundle:
        out("DRILL", A.quoted(A.command("bundle", bundle, "sql", "<QUERY>")))
        out("DRILL", A.quoted(A.command("bundle", bundle, "evidence", "<ID>")))
        out("DRILL", A.quoted(A.command("bundle", bundle, "who", "--agent-id", "<ID>")))


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False, usage=argparse.SUPPRESS)
    parser.add_argument("target", nargs="?")
    parser.add_argument("--app", choices=tuple(a.name for a in A.APPLICATIONS))
    parser.add_argument("--general", action="store_true")
    parser.add_argument("--claude-home")
    parser.add_argument("--archives-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--bundles-root")
    parser.add_argument("--bundle-dir")
    parser.add_argument("--extensions")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--clp-s")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args, rest = parser.parse_known_args(argv)
    if args.help:
        # One help text for the one command: this route's own options, then every subcommand.
        print(__doc__.strip())
        print()
        print(C.table_text())
        print("\nRun `clp <SUBCOMMAND> --help` for that subcommand's own options.")
        raise SystemExit(0)
    if rest:
        print(f"error: unknown argument: {rest[0]}\n"
              f"  clp takes only the options listed in `clp --help` and passes nothing else "
              f"through.",
              file=sys.stderr)
        raise SystemExit(2)
    if args.force and args.dry_run:
        print("error: --force and --dry-run contradict each other", file=sys.stderr)
        raise SystemExit(2)
    return args


def run(args):
    runner = A.Runner(clp_s=args.clp_s, quiet=args.quiet)
    classified = A.classify(args.target, runner, claude_home=args.claude_home,
                            extensions=args.extensions, recursive=not args.no_recursive)
    if classified["form"] == "none":
        return list_sessions(runner, args)

    classified.setdefault("app_from", {"bundle": "manifest", "archive": "records"}
                          .get(classified["form"], "records"))
    if classified["form"] in ("log-file", "log-folder", "session-id") and classified["app"] \
            and classified["app"].formats:
        detected = classified.get("detect") or {"files": []}
        if any(f.get("bundled") for f in detected["files"]):
            classified["app_from"] = "format"
    if args.app:
        classified = assert_app(classified, args.app)

    route, route_why = choose_route(classified, args)

    # Always before any work: what this is, and why.
    out("TARGET", classified["target"])
    out("FORM", classified["form"])
    out("APP", classified["app"].name if classified["app"] else A.UNRECOGNISED)
    out("APP_FROM", classified["app_from"])
    out("WHY", classified["why"])
    for line in classified["app_evidence"]:
        out("EVIDENCE", line)
    out("ROUTE", route)
    out("ROUTE_WHY", route_why)
    if args.dry_run:
        out("DRY_RUN", "1")

    prep = Prep()
    archive = prepare_archive(classified, args, runner, prep)
    bundle = None
    if route == A.SPECIALISED and classified["app"] is A.CLAUDE_CODE:
        bundle = prepare_bundle(classified, args, runner, prep, archive)
    elif classified["form"] == "bundle":
        bundle = classified["bundle"]

    for argv in runner.ran:
        out("RAN", A.quoted(argv))
    if archive and os.path.isdir(archive):
        report_artefact("ARCHIVE", archive)
    if bundle and os.path.isdir(bundle):
        report_artefact("BUNDLE", bundle)
    if args.dry_run:
        out("DRY_RUN_RESULT", "nothing was written; every check above was read-only")
    prep.report()
    hand_off(route, archive, bundle, dry_run=args.dry_run)
    return 0


def main():
    args = parse_args(sys.argv[1:])
    try:
        return run(args)
    except (A.AnalyzeError, B.BundleError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
