"""session_meta - the first useful value of a session JSONL's header records.

`clp list-sessions` names each session by reading the first lines of its JSONL:
the harness that wrote it, its working directory, and its title. Those lines are
written by the harness, not by us, so a field can be absent, null, or empty, and
different harness versions disagree on where it lives — hence a fallback chain
and a scan of the first records rather than a fixed line.

This replaces the jq programs the shell used to run for that scan.

Subcommands:
  first-value --file F --field PATH [--where-path PATH --where-equals VALUE]
              [--non-empty]
      Print the first record's value at PATH, and nothing when no record has
      one. --where-path/--where-equals restrict the scan to records whose own
      PATH equals VALUE (the session_meta record, say); --non-empty also skips
      empty strings. Only the first 200 lines are read: these fields are in the
      header, and a session file can be tens of megabytes.

Conventions: stdout carries data only, diagnostics go to stderr as `error: ...`,
exit 2 for a usage error. Stdlib only.
"""

import argparse
import json
import sys

SCAN_LINES = 200


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(2)


def at_path(record, path):
    """The value at a dotted path, or None when any step is missing."""
    value = record
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def as_text(value):
    """A value as `jq -r` prints it: strings raw, other scalars as JSON."""
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False)


def first_value(args):
    try:
        handle = open(args.file, encoding="utf-8")
    except OSError:
        return None
    with handle:
        for number, line in enumerate(handle):
            if number >= SCAN_LINES:
                break
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.where_path and at_path(record, args.where_path) != args.where_equals:
                continue
            value = at_path(record, args.field)
            if value is None or value is False:
                continue
            if args.non_empty and value == "":
                continue
            return as_text(value)
    return None


def build_parser():
    parser = Parser(prog="session_meta.py", description=__doc__.splitlines()[0])
    parser.add_argument("--file", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--where-path", default="")
    parser.add_argument("--where-equals", default="")
    parser.add_argument("--non-empty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    value = first_value(args)
    if value is not None:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
