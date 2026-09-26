"""
subcommand - run one stage of a multi-stage command.

A dispatcher in bin/ is a name plus a table mapping each subcommand to the
implementation next to this file, and `dispatch` runs the one the first
positional argument names. Everything after that name is the implementation's
own argv, untouched.

Each Python implementation is a module with a `main()` that reads `sys.argv`, so
the dispatcher rewrites `sys.argv[0]` to "<prog> <sub>" before calling it:
argparse takes its `prog` from `os.path.basename(sys.argv[0])`, and a name
holding a space holds no path separator, so basename returns it whole and every
usage line argparse prints names the subcommand the caller typed. A shell
implementation is exec'd instead, which leaves it owning the process, its exit
status and its signals.
"""

import importlib.util
import os
import sys

LIB_DIR = os.path.dirname(os.path.realpath(__file__))


def _load(module_file):
    """The implementation module, imported by path rather than by package name.

    bin/lib is not a package, and a dispatcher must not import every stage just
    to run one of them.
    """
    spec = importlib.util.spec_from_file_location(
        module_file[: -len(".py")], os.path.join(LIB_DIR, module_file)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def usage(prog, summary, subcommands, stream):
    print(f"Usage: {prog} <SUBCOMMAND> [options]", file=stream)
    print(f"\n{summary}\n\nSubcommands:", file=stream)
    width = max(len(name) for name in subcommands)
    for name, (_, blurb) in subcommands.items():
        print(f"  {name:<{width}}  {blurb}", file=stream)
    print(f"\nRun `{prog} <SUBCOMMAND> --help` for that subcommand's own options.",
          file=stream)


def dispatch(prog, summary, subcommands, argv):
    """Run the subcommand argv names and return its exit status.

    No subcommand, or one that is not in the table, lists the subcommands and
    returns 2 -- an unknown stage is a usage error, never a silent no-op.
    """
    if not argv:
        usage(prog, summary, subcommands, sys.stderr)
        return 2
    sub, rest = argv[0], argv[1:]
    if sub in ("-h", "--help"):
        usage(prog, summary, subcommands, sys.stdout)
        return 0
    if sub not in subcommands:
        print(f"error: unknown subcommand: {sub}", file=sys.stderr)
        usage(prog, summary, subcommands, sys.stderr)
        return 2

    impl = subcommands[sub][0]
    path = os.path.join(LIB_DIR, impl)
    if impl.endswith(".sh"):
        # The kernel passes `path` to the interpreter named by the shebang, so
        # the script still sees itself in BASH_SOURCE and finds lib/ from it.
        os.execv(path, [path, *rest])
    sys.argv = [f"{prog} {sub}", *rest]
    return _load(impl).main()
