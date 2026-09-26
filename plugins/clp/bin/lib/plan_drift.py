"""
plan_drift - flag query-plan entries whose field holds more than one type.

clp-s stores one schema-tree node per (parent, key, type), so a single field
path can hold several types across records:

  toolUseResult   ClpString:116   Object:4525

What a filter reaches of such a path depends on where the filter sits, and the
two directions are opposites. Measured with `clp-s-search-kql --count` on a
32,614-record agent-session archive:

  toolUseResult:*            116   ClpString only -- not the 4,525 Objects
  message.content:*          237   ClpString 229 + VarString 8 -- not the
                                   16,367 StructuredArrays
  attachment.content:*      9452   ClpString 8 + VarString 9444 -- not the
                                   Object 244 or StructuredArray 670
  toolUseResult.stderr:*    3027   ClpString 338 + VarString 2689
  totalCostUSD:*               3   Integer 1 + FormattedFloat 2
  parentUuid:*             32614   VarString 32553 + NullValue 61

So a filter written *at* a path reaches the union of that path's scalar types and
never an Object or an array; a filter written on a path *below* it reaches only
the records where the ancestor is structural, since the others have no such
subfield at all. `addressable` is that reachable share, and a path whose
reachable share is 1.0 hides nothing and is not reported: ClpString-vs-VarString
is clp-s splitting one string column, Integer-vs-FormattedFloat one number
column, and a NullValue answers `field:*` like any other scalar.

Structural drift is the real trap, and the direction matters. A filter at
`toolUseResult` reaches 116 of 4,641 records -- and those 116 are the failed tool
calls, the interesting ones -- while a filter on `toolUseResult.stderr` reaches
the other 4,525. Both are silent: the query is valid and returns hits. It is the
same trap as a NOT predicate over a field some records lack, except nothing in
the query says so.

`clp-s-schema-tree --drift --drift-file F` writes the archive's drifting paths as
NDJSON, one row per path:

  {"path", "kql_path", "types": [{"type", "id", "count"}, ...], "records"}

`kql_path` is the form a query uses (a structured array's `[]` segments dropped),
so it is what a plan's filter fields are matched against.

A plan entry may declare which shape a filter is for with `"types":
["ClpString"]` on its `match` leaf, and then the declared types' reachable share
is what it addresses.

Stdlib only, like the other log-shape-* helpers, except for the sibling modules
lib/kql_build and lib/schema_tree.
"""

import json

from kql_build import FilterError
from schema_tree import type_name

# The clp_s::NodeType ids (components/core/src/clp_s/SchemaTree.hpp, tabulated
# once in schema_tree.TYPE_NAMES) that hold a subtree rather than a value. The
# split is not a guess: it is what the counting queries in this module's
# docstring measured. `toolUseResult:*` matches 116, the ClpString records, and
# not the 4,525 Object ones, while `toolUseResult.stderr:*` matches all 3,027 of
# its ClpString and VarString records -- one filter spans a path's scalar types
# and stops at a structural one.
STRUCTURAL_TYPE_IDS = frozenset((5, 6, 9))  # Object, UnstructuredArray, StructuredArray
STRUCTURAL_TYPES = frozenset(type_name(i) for i in STRUCTURAL_TYPE_IDS)

# Where a filter sits relative to a drifting path, which decides what it reaches:
# AT_PATH the path itself (its scalar types), AT_DESCENDANT a path below it (the
# records where it is structural).
AT_PATH = "path"
AT_DESCENDANT = "descendant"

# Default for check-plan's --drift-fail-below: a plan that can reach less than
# this share of a path's records is answering about a minority or a bare
# majority of them, which is a wrong answer rather than an incomplete one.
DEFAULT_FAIL_BELOW = 0.95


def load_drift(path):
    """{kql_path: {"types": {type_name: count}, "ids": {type_name: id},
    "records": n}} from the NDJSON that clp-s-schema-tree --drift-file writes.
    Rows are keyed by `kql_path`, and two display paths that share one
    `kql_path` are merged: their counts add up because a query cannot tell them
    apart. `records` is the sum of the counts, so a merged row stays consistent.
    An empty file means nothing drifts. Raises ValueError on a row that is not a
    drift row."""
    drift = {}
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {lineno} is not JSON: {exc}") from None
            kql_path = row.get("kql_path") if isinstance(row, dict) else None
            types = row.get("types") if isinstance(row, dict) else None
            if not isinstance(kql_path, str) or not kql_path or not isinstance(types, list):
                raise ValueError(f"line {lineno} needs a 'kql_path' string and a 'types' array")
            entry = drift.setdefault(kql_path, {"types": {}, "ids": {}})
            for t in types:
                name = t.get("type") if isinstance(t, dict) else None
                count = t.get("count") if isinstance(t, dict) else None
                if not isinstance(name, str) or not name or not isinstance(count, int):
                    raise ValueError(f"line {lineno}: each type needs a 'type' and a 'count'")
                entry["types"][name] = entry["types"].get(name, 0) + count
                entry["ids"].setdefault(name, t.get("id"))
    for entry in drift.values():
        entry["records"] = sum(entry["types"].values())
    return drift


def at_risk_paths(drift, field):
    """[(kql_path, where)] for the drifting paths a filter on `field` is at risk
    from, outermost first: its drifting ancestors (where=AT_DESCENDANT, the
    filter sits below them) and then `field` itself (where=AT_PATH).

    A drifting path *below* `field` is not included: it is a different field, and
    its shapes are not what this filter reads. Each filter field is matched on
    its own, so a plan that filters both `toolUseResult` and
    `toolUseResult.stderr` is told about both, and `toolUseResult` is reported
    twice -- once per direction, which is two different reachable shares."""
    parts = field.split(".")
    return [
        (p, AT_PATH if p == field else AT_DESCENDANT)
        for p in (".".join(parts[:i]) for i in range(1, len(parts) + 1))
        if p in drift
    ]


def is_structural(row, name):
    """Whether one type of a drift row holds a subtree rather than a value. The
    id from the drift file decides it; the name is the fallback for a row
    written without ids."""
    type_id = row["ids"].get(name)
    if type_id is None:
        return name in STRUCTURAL_TYPES
    return type_id in STRUCTURAL_TYPE_IDS


def reachable(row, where, declared=()):
    """{type_name: count} of the types a filter reaches: `where` picks the side
    of the scalar/structural split, and `declared`, when given, keeps only the
    types the filter declared it is for."""
    want = where == AT_DESCENDANT
    return {
        name: count for name, count in row["types"].items()
        if is_structural(row, name) == want and (not declared or name in declared)
    }


def addressable(row, reach):
    """The share of a path's records a filter reaches: the most it can say
    anything about, the rest being records it is silent on."""
    if not row["records"]:
        return 1.0
    return sum(reach.values()) / row["records"]


def type_counts(row):
    """"ClpString:116,Object:4525" -- every type of one path, in the drift
    file's own order (by clp-s NodeType id) so the line reads like the row."""
    def key(item):
        type_id = row["ids"].get(item[0])
        return (type_id is None, type_id, item[0])
    return ",".join(f"{name}:{count}" for name, count in sorted(row["types"].items(), key=key))


def dominant(reach):
    """"Object:4525" -- the largest of the types a filter reaches, or "none:0"
    when it reaches none of them."""
    if not reach:
        return "none:0"
    name, count = max(reach.items(), key=lambda kv: (kv[1], kv[0]))
    return f"{name}:{count}"


def _check_declared(field, path, row, declared):
    """Raise FilterError when a "types" declaration cannot hold: a declaration
    naming a type the path does not have is a bug, not an exemption, and so is
    one naming only types a filter at that path cannot reach."""
    missing = [name for name in declared if name not in row["types"]]
    if missing:
        raise FilterError(
            f"field {field} declares type(s) {','.join(missing)}, which {path} does not hold; "
            f"its types are {type_counts(row)}"
        )
    if not reachable(row, AT_PATH, declared):
        raise FilterError(
            f"field {field} declares only {','.join(declared)}, which a filter at {path} cannot "
            f"reach (measured: {path}:* matches that path's scalar types, not its subtrees); "
            f"filter a field under {path} instead"
        )


def entry_lines(drift, entry_id, fields, fail_below=DEFAULT_FAIL_BELOW):
    """(lines, risks, declared_count, fails) for one plan entry.

    `fields` is the entry's [(field, declared_types)] from
    kql_build.filter_fields. One line per (drifting path, direction) a field is
    at risk from, unless the filter reaches all of that path's records:
    DRIFT_DECLARED when the filter says which shape it is for, DRIFT_RISK
    otherwise, and DRIFT_DECLARED_UNVERIFIED for a declaration on a path the
    drift file does not list -- the file holds only the drifting paths, so a typo
    there cannot be rejected, only shown. `declared_count` counts the declared
    lines of both kinds, so risks + declared_count is the line count. `fails`
    counts the undeclared risks whose `addressable` is below `fail_below`.
    Raises FilterError on a declaration that cannot hold (_check_declared)."""
    lines, risks, declared_count, fails = [], 0, 0, 0
    seen = set()
    for field, declared in fields:
        for path, where in at_risk_paths(drift, field):
            if (path, where) in seen:
                continue
            seen.add((path, where))
            row = drift[path]
            # A leaf's "types" speaks for the field it names, not for an ancestor
            # of it, so an ancestor is reported as a plain risk.
            if declared and where == AT_PATH:
                _check_declared(field, path, row, declared)
                declared_count += 1
                share = addressable(row, reachable(row, where, declared))
                lines.append(
                    f"DRIFT_DECLARED entry={entry_id} path={path} at={where} "
                    f"types={type_counts(row)} declared={','.join(declared)} "
                    f"addressable={share * 100:.1f}%"
                )
                continue
            reach = reachable(row, where)
            share = addressable(row, reach)
            if share >= 1.0:
                # Nothing is out of reach: the drift is clp-s splitting one
                # column (ClpString/VarString, Integer/FormattedFloat) or a
                # null, all of which one filter matches.
                continue
            risks += 1
            if share < fail_below:
                fails += 1
            lines.append(
                f"DRIFT_RISK entry={entry_id} path={path} at={where} "
                f"types={type_counts(row)} dominant={dominant(reach)} "
                f"addressable={share * 100:.1f}%"
            )
        if declared and field not in drift:
            declared_count += 1
            lines.append(
                f"DRIFT_DECLARED_UNVERIFIED entry={entry_id} path={field} "
                f"declared={','.join(declared)} reason=not-in-drift-set"
            )
    return lines, risks, declared_count, fails
