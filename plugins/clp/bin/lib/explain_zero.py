"""
explain_zero - say why a query matched nothing, when the archive's own structure
says it could never have matched.

clp-s stores one schema-tree node per path *and type*, and a scalar filter reaches
only the scalar types: a filter written at a path that the archive holds as an
Object or an array matches nothing however many records carry it. `message:*`
returning zero on an archive where 16,604 records have a `message` is the trap,
because "no records" and "not a thing you can filter at" look identical.

That distinction already exists in lib/plan_drift for checking stored query plans.
This is the same rule applied to one ad-hoc query, after the fact: the plan check
happens before a plan runs, and an interactive query has no plan.

Three answers are worth telling apart, and all three come from the archive:
  structural  the path is there but only as an Object or array -- name the
              queryable leaves under it
  unknown     the path is not in the tree at all -- a typo, so offer the nearest
              real paths
  present     the path is there and scalar, so the zero is about the data and
              nothing is said

Reads a stats.schema_tree dump on stdin and the KQL as the one argument. Prints
the explanation, or nothing when the structure does not explain the zero. Stdlib
only, like the other plugin helpers.
"""

import difflib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from plan_drift import STRUCTURAL_TYPES  # noqa: E402
from schema_tree import load_trees, node_fields  # noqa: E402

# A KQL field is the identifier before a colon. Quoted values, parentheses and the
# operators are not fields, and `shape(...)`/`decompose(...)` are functions, so a
# name followed by "(" is skipped. This is deliberately a lexer and not a parser:
# over-collecting a name costs one "not in the tree" line that the tree then
# refutes, while a real parser here would be a second grammar to keep in step.
FIELD_RE = re.compile(r'(?<![\w.$"])([A-Za-z_][\w.]*)\s*(?=:)')
NOT_A_FIELD = frozenset(("and", "or", "not", "shape", "decompose", "semantic"))
MAX_LEAVES = 6
MAX_SUGGESTIONS = 3


def query_fields(kql):
    """Every field path the query filters on, in order, without duplicates."""
    seen, out = set(), []
    for name in FIELD_RE.findall(kql):
        if name.lower() in NOT_A_FIELD or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def archive_paths(lines):
    """{kql_path: {type names}} merged over every archive in the dump.

    Merged because a filter is written once for the whole directory, and a path
    that is scalar in any archive is one a filter can reach.
    """
    paths = {}
    for tree in load_trees(lines):
        for _, (kql_path, _display, type_name, _collapsed) in node_fields(tree).items():
            if kql_path:
                paths.setdefault(kql_path, set()).add(type_name)
    return paths


def leaves_under(paths, field):
    """Queryable (scalar) paths directly beneath field, shallowest first."""
    prefix = field + "."
    under = [(p.count("."), p) for p, types in paths.items()
             if p.startswith(prefix) and not types <= STRUCTURAL_TYPES]
    return [p for _, p in sorted(under)[:MAX_LEAVES]]


def explain(kql, paths):
    """The lines to print, or [] when the structure does not explain the zero."""
    if not paths:
        return []
    lines = []
    for field in query_fields(kql):
        types = paths.get(field)
        if types is None:
            near = difflib.get_close_matches(field, list(paths), n=MAX_SUGGESTIONS, cutoff=0.7)
            lines.append(f"  `{field}` is not a path in this archive.")
            if near:
                lines.append("    Did you mean: " + ", ".join(f"`{n}`" for n in near) + "?")
        elif types <= STRUCTURAL_TYPES:
            kinds = ", ".join(sorted(types))
            lines.append(f"  `{field}` exists, but the archive holds it as {kinds}, "
                         f"which a filter cannot match.")
            leaves = leaves_under(paths, field)
            if leaves:
                lines.append("    Filter a leaf beneath it: "
                             + ", ".join(f"`{leaf}`" for leaf in leaves))
            else:
                lines.append("    It has no scalar leaf in this archive, so nothing under it "
                             "can be filtered either.")
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: explain_zero.py <kql>  (stats.schema_tree on stdin)", file=sys.stderr)
        return 2
    lines = explain(argv[0], archive_paths(sys.stdin.read().splitlines()))
    if lines:
        print("The archive's structure explains the zero:")
        print("\n".join(lines))
    return 0


# Unlike the other lib modules this one is also run as a script, by the shell
# wrappers that have no Python of their own.
if __name__ == "__main__":
    sys.exit(main())
