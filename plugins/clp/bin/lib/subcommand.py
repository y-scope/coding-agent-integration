"""
subcommand - run one step of `clp`.

There is one command in bin/, `clp`, and one table (lib/commands.py) mapping each
subcommand to the implementation next to this file. `dispatch` runs the one the
first positional argument names. Everything after that name is the
implementation's own argv, untouched.

A subcommand whose implementation is a `Group` has subcommands of its own, and
dispatch recurses into it with the typed name added to the program name, so
`clp session measure` reaches session_measure.py as prog "clp session measure".

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


class Group:
    """A subcommand that is itself a table of subcommands, not one implementation."""

    def __init__(self, summary, subcommands):
        self.summary = summary
        self.subcommands = subcommands

    @property
    def names(self):
        return "|".join(self.subcommands)


def _load(module_file):
    """The implementation module, imported by path rather than by package name.

    bin/lib is not a package, and the dispatcher must not import every stage just
    to run one of them.
    """
    spec = importlib.util.spec_from_file_location(
        module_file[: -len(".py")], os.path.join(LIB_DIR, module_file)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_lines(subcommands):
    """The subcommand table as printable lines; a group is shown with its own names."""
    labels = {name: (f"{name} <{impl.names}>" if isinstance(impl, Group) else name)
              for name, (impl, _) in subcommands.items()}
    width = max(len(label) for label in labels.values())
    return [f"  {labels[name]:<{width}}  {blurb}" for name, (_, blurb) in subcommands.items()]


def usage(prog, summary, subcommands, stream):
    print(f"Usage: {prog} <SUBCOMMAND> [options]", file=stream)
    print(f"\n{summary}\n\nSubcommands:", file=stream)
    for line in table_lines(subcommands):
        print(line, file=stream)
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
    if isinstance(impl, Group):
        return dispatch(f"{prog} {sub}", impl.summary, impl.subcommands, rest)
    path = os.path.join(LIB_DIR, impl)
    if impl.endswith(".sh"):
        # The kernel passes `path` to the interpreter named by the shebang, so
        # the script still sees itself in BASH_SOURCE and finds lib/ from it.
        os.execv(path, [path, *rest])
    sys.argv = [f"{prog} {sub}", *rest]
    return _load(impl).main()
