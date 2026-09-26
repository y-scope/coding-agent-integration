"""bootstrap_json - the four JSON readings `clp bootstrap` used to make with jq.

The bootstrap samples an archive through three files: the `stats.archives` dump
(per-archive counts), the schema tree's `--json-out` rows (every field with its
type and record count), and the sample records themselves. This module reads
them, so the bootstrap does not need jq, which is not present on a base install.

Subcommands:
  sum-field --file F --field NAME
      Sum one numeric field across the JSON objects in F (0 when a line lacks it).
  archive-lines --file F
      One ARCHIVE_STAT line per archive in F.
  scalar-fields --tree-file F [--limit 8]
      The paths of the scalar fields outside arrays that the most records carry,
      most-recorded first.
  field-values --record-file F --field PATH
      The value of a dotted field path in every record that has one, one per line.

Conventions: stdout carries data only, diagnostics go to stderr as `error: ...`,
exit 2 for a usage error and 1 when an input cannot be read. Stdlib only.
"""

import argparse
import json
import sys

# The types a value distribution makes sense for: a scalar a reader can compare.
SCALAR_TYPES = ("VarString", "Integer", "Boolean")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(2)


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    return 1


def read_objects(path):
    """The JSON objects on the lines of a clp-s dump; other lines are skipped."""
    objects = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("{"):
                try:
                    objects.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return objects


def cmd_sum_field(args):
    try:
        objects = read_objects(args.file)
    except OSError as exc:
        return fail(f"cannot read {args.file}: {exc}")
    total = 0
    for obj in objects:
        value = obj.get(args.field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    print(total)
    return 0


def cmd_archive_lines(args):
    try:
        objects = read_objects(args.file)
    except OSError as exc:
        return fail(f"cannot read {args.file}: {exc}")
    for obj in objects:
        print(f"ARCHIVE_STAT archive_id={obj.get('archive_id')} "
              f"log_shapes={obj.get('num_log_shapes')} vars={obj.get('num_vars')}")
    return 0


def cmd_scalar_fields(args):
    try:
        with open(args.tree_file, encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"cannot read {args.tree_file}: {exc}")
    if not isinstance(rows, list):
        return fail(f"{args.tree_file} is not a list of fields")

    candidates = [row for row in rows
                  if isinstance(row, dict)
                  and row.get("type") in SCALAR_TYPES
                  and "[]" not in (row.get("display") or "")
                  and "collapsed_keys" not in row]
    candidates.sort(key=lambda row: -row.get("records", 0))
    for row in candidates[:args.limit]:
        print(row.get("path", ""))
    return 0


def jq_tostring(value):
    """jq's `tostring`: strings as they are, everything else as compact JSON."""
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value)


def cmd_field_values(args):
    """The dotted path's value in each record, skipping records without one.

    A missing path and a null value are both absent, like jq's `// empty`.
    """
    parts = args.field.split(".")
    try:
        handle = open(args.record_file, encoding="utf-8")
    except OSError as exc:
        return fail(f"cannot read {args.record_file}: {exc}")

    with handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = record
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            # jq's `//` treats false as absent too, so a false value is skipped.
            if value is None or value is False:
                continue
            print(jq_tostring(value))
    return 0


def build_parser():
    parser = Parser(prog="bootstrap_json.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(sub, *names, **kwargs):
        sub.add_argument(*names, **kwargs)

    total = subparsers.add_parser("sum-field")
    add(total, "--file", required=True)
    add(total, "--field", required=True)
    total.set_defaults(func=cmd_sum_field)

    lines = subparsers.add_parser("archive-lines")
    add(lines, "--file", required=True)
    lines.set_defaults(func=cmd_archive_lines)

    fields = subparsers.add_parser("scalar-fields")
    add(fields, "--tree-file", required=True)
    add(fields, "--limit", type=int, default=8)
    fields.set_defaults(func=cmd_scalar_fields)

    values = subparsers.add_parser("field-values")
    add(values, "--record-file", required=True)
    add(values, "--field", required=True)
    values.set_defaults(func=cmd_field_values)

    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
