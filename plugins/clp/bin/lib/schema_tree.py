"""schema_tree - the field paths of a clp-s archive, from its merged schema tree.

`clp-s s ARCHIVE stats.schema_tree` prints one JSON object per archive:
{"archive_id": ..., "nodes": [{"id", "parent_id", "key", "type", "count",
"children"}, ...]}. Every distinct path/type pair in the archive's records is
one node, and `count` is how many records carry it, so the tree lists every
field without sampling a single record.

A field is addressed in KQL by the keys on its path. An element of a
structured array has an empty key and is skipped, so `message.content[].name`
is queried as `message.content.name`. The same path can occur with several
types (a field that is a string in some records and an array in others), and
each type is its own node.

Some JSON uses data as keys (one key per file path, per user, ...). An object
with more than max_children children that nearly all look alike (the same type
and the same child keys) is collapsed: its children's keys are replaced by `*`,
so their subtrees merge into one row instead of thousands. The root is never
collapsed, and neither is an object whose many children differ, since those
are real fields.

Other readings of the same tree answer questions a field list cannot.
`type_drift` reports the paths stored under more than one type -- a field that
is a string in some records and an object in others, which a query written for
one of the two shapes silently misses. `field_counts` reports the record root's
children grouped by record count, which is how a block of fields that appear
together shows itself.

What the tree cannot give is a partition of the records. It stores a count per
node and nothing about which nodes appear together, so per-field counts
overlap: a record that populates `type`, `sessionId` and `message` is counted
under all three. Two readings derive a real partition, both verified by
counting queries the caller runs:

- `value_partition_fields` + `partition_by_value`: the values of a universal
  low-cardinality scalar field. Exact and cheap -- 15 queries on a Claude
  session archive, where `type` has 14 values summing to every record -- and
  the first choice whenever such a field exists.
- `family_candidates` + `partition_families`: which subtree a record populates,
  for archives with no such field. Approximate (a record kind with no field of
  its own falls into the residual) and hundreds of queries, but it needs
  nothing of the data but its shape.

Stdlib only, like the other log-shape-* helpers, except for the sibling module
lib/kql_build, whose value escaping decides what is safe to put in a query.
"""

import json
import re

from kql_build import escape_value

# clp_s::NodeType (components/core/src/clp_s/SchemaTree.hpp).
TYPE_NAMES = {
    0: "Integer",
    1: "Float",
    2: "ClpString",
    3: "VarString",
    4: "Boolean",
    5: "Object",
    6: "UnstructuredArray",
    7: "NullValue",
    8: "DeprecatedDateString",
    9: "StructuredArray",
    10: "Metadata",
    11: "DeltaInteger",
    12: "FormattedFloat",
    13: "DictionaryFloat",
    14: "Timestamp",
    15: "LogMessage",
    16: "LogType",
    17: "LogTypeID",
    18: "ParentRule",
}

# Types that hold other nodes rather than values.
CONTAINER_TYPES = {"Object", "StructuredArray", "Metadata"}

DEFAULT_MAX_CHILDREN = 50

# Share of an object's children that must look alike for it to be collapsed.
UNIFORM_SHARE = 0.8

# A max_children no object reaches: drift and the family readings report the
# real keys, since each child of a data-as-keys object carries its own types
# and count.
NO_COLLAPSE = 1 << 62

# Counting queries one `--record-families` run may spend. High enough that a
# normal archive finishes: stopping early reports a residual larger than the
# truth, and an inflated "unexplained" number is worse than half a minute. A
# Claude session archive of 44,818 records spends about 400 of them, at roughly
# 0.06 s each.
DEFAULT_MAX_FAMILY_QUERIES = 1000

# A path safe to drop into KQL bare. JSON keys can be data -- a whole question
# sentence, a file path -- and an unquoted key with a space in it makes clp-s
# read the query as natural language and fail on a missing embedding endpoint,
# so a path outside this shape is not used as a discriminator at all.
QUERYABLE_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\]-]*$")

# Field types the bootstrap's DIST line samples: the scalars whose values are
# worth distributing, which is the same question a value partition asks. Kept
# here so the two cannot drift apart.
DIST_FIELD_TYPES = ("VarString", "Integer", "Boolean")

# Distinct values a field may have and still partition the records readably.
DEFAULT_MAX_PARTITION_VALUES = 64

# Share of the records a field must carry to partition them, and not 1.0: on a
# bundle of five session archives `type` covers 111,187 of 111,223 records, and
# refusing it over those 36 buys an approximate answer for 716 queries in place
# of an exact one for 15. The records a field misses become the residual, as
# the records no discriminator matches would under the other method, so the
# arithmetic is untouched -- this only widens which field may be chosen.
DEFAULT_MIN_PARTITION_COVERAGE = 0.99

# Leaves a disjunction discriminator may OR together. A real subtree needs more
# than a handful: on a Claude session archive `toolUseResult` takes 10, one per
# kind of tool result, and stopping at 6 leaves 120 of its 4,525 records
# uncovered and the whole subtree unnamed.
DEFAULT_MAX_DISCRIMINATOR_LEAVES = 12


def type_name(type_id):
    """The clp_s::NodeType name, or `typeN` for an id this script predates."""
    return TYPE_NAMES.get(type_id, f"type{type_id}")


def load_trees(lines):
    """The per-archive tree objects in stats.schema_tree output, skipping any
    line that is not one."""
    trees = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("nodes"), list):
            trees.append(obj)
    return trees


def node_fields(tree, max_children=DEFAULT_MAX_CHILDREN):
    """{node_id: (kql_path, display_path, type_name, collapsed)} for one
    archive's tree. The root and the Metadata subtree have no field and are
    left out."""
    nodes = {n["id"]: n for n in tree["nodes"]}
    fields = {}

    def uniform(children):
        signatures = {}
        for child in children:
            node = nodes[child]
            keys = frozenset(nodes[c]["key"] for c in node.get("children") or [])
            signature = (node["type"], keys)
            signatures[signature] = signatures.get(signature, 0) + 1
        return max(signatures.values()) >= UNIFORM_SHARE * len(children)

    def visit(node_id, kql_parts, display_parts, collapsed):
        node = nodes[node_id]
        node_type = type_name(node["type"])
        if node_type == "Metadata":
            return
        key = node["key"]
        if node["parent_id"] == -1:
            kql, display = kql_parts, display_parts
        elif collapsed:
            kql, display = kql_parts + ["*"], display_parts + ["*"]
        elif key == "":
            kql, display = kql_parts, display_parts + ["[]"]
        else:
            kql, display = kql_parts + [key], display_parts + [key]
        if node["parent_id"] != -1:
            fields[node_id] = (".".join(kql), ".".join(display), node_type, collapsed)
        children = node.get("children") or []
        collapse_children = (
            node["parent_id"] != -1
            and node_type == "Object"
            and len(children) > max_children
            and uniform(children)
        )
        for child in children:
            visit(child, kql, display, collapse_children)

    for node in tree["nodes"]:
        if node["parent_id"] == -1:
            visit(node["id"], [], [], False)
    return fields


def summarize(trees, max_children=DEFAULT_MAX_CHILDREN):
    """Rows merged across archives and collapsed keys:
    {(display_path, type_name): {"path", "display", "type", "records",
    "collapsed_keys"}}. `records` sums node counts; `collapsed_keys` counts the
    distinct keys merged into a `*` row."""
    rows = {}
    for tree in trees:
        nodes = {n["id"]: n for n in tree["nodes"]}
        for node_id, (kql, display, node_type, collapsed) in node_fields(
                tree, max_children).items():
            row = rows.setdefault((display, node_type), {
                "path": kql, "display": display, "type": node_type,
                "records": 0, "collapsed_keys": set(),
            })
            row["records"] += nodes[node_id]["count"]
            if collapsed:
                row["collapsed_keys"].add(nodes[node_id]["key"])
    return rows


def type_drift(trees):
    """(rows, paths): the field paths stored under more than one type, and how
    many distinct field paths there are to compare that against.

    A row is {"path", "kql_path", "types": [{"type", "id", "count"}, ...],
    "records"}, most records first; the types are ordered by NodeType id and
    `records` sums their counts. Counts are summed across archives, so a path
    that drifts only between two archives of the same dir is reported too.

    The MST stores one node per (parent, key, type), so the drift is already in
    the tree: `toolUseResult` as a ClpString on a failed tool call and as an
    Object on a success is two children of the same parent. An array's elements
    (the keyless nodes under a structured array) are not a field whose shape
    changed -- they are one array's element types, and KQL addresses them by
    the array's own path -- so they are left out of both numbers.
    """
    paths = {}
    for tree in trees:
        nodes = {n["id"]: n for n in tree["nodes"]}
        for node_id, (kql, display, _, _) in node_fields(tree, NO_COLLAPSE).items():
            node = nodes[node_id]
            if node["key"] == "":
                continue
            path = paths.setdefault(display, {"kql_path": kql, "types": {}})
            path["types"][node["type"]] = (
                path["types"].get(node["type"], 0) + node["count"]
            )
    rows = []
    for display, path in paths.items():
        if len(path["types"]) < 2:
            continue
        types = [
            {"type": type_name(type_id), "id": type_id, "count": count}
            for type_id, count in sorted(path["types"].items())
        ]
        rows.append({
            "path": display, "kql_path": path["kql_path"], "types": types,
            "records": sum(t["count"] for t in types),
        })
    rows.sort(key=lambda r: (-r["records"], r["path"]))
    return rows, len(paths)


def subtree_size(nodes, node_id):
    """How many nodes hang below node_id, not counting it."""
    size = 0
    stack = list(nodes[node_id].get("children") or [])
    while stack:
        size += 1
        stack.extend(nodes[stack.pop()].get("children") or [])
    return size


def field_counts(trees):
    """(root, groups): the record root and its children grouped by record
    count.

    The record root is the `parent_id == -1` node with the most records: a tree
    also roots the archive's Metadata, which one record carries. `root` is
    {"ids", "type", "records", "children"}; a group is {"count", "share",
    "fields": [{"key", "type", "subtree_nodes"}, ...], "subtree_nodes"}, most
    records first.

    Children that read the same count are grouped because they are carried by
    the same records: seven fields all reading 32,614 are one block that
    appears together, not seven separate facts. Each type of a drifting child
    is its own field here, since each is its own node.

    These counts are per field and they overlap: a record populating `type`,
    `sessionId` and `message` is counted in all three groups, so `share` is a
    share of the records per field and the shares neither partition the records
    nor sum to 100%. Grouping by count is the right input for choosing family
    discriminators, but the partition itself has to be verified by query --
    `family_candidates` and `partition_families`.

    Across archives the roots' counts are summed and children merged by key and
    type; `subtree_nodes` is then the largest subtree seen for that child,
    since the same field's subtree is not a different subtree per archive.
    """
    root = {"ids": [], "type": "", "records": 0, "children": 0}
    children = {}
    for tree in trees:
        nodes = {n["id"]: n for n in tree["nodes"]}
        roots = [n for n in tree["nodes"] if n["parent_id"] == -1]
        if not roots:
            continue
        record_root = max(roots, key=lambda n: n["count"])
        root["ids"].append(record_root["id"])
        root["type"] = type_name(record_root["type"])
        root["records"] += record_root["count"]
        for child_id in record_root.get("children") or []:
            child = nodes[child_id]
            key = (child["key"], type_name(child["type"]))
            entry = children.setdefault(key, {
                "key": child["key"], "type": key[1], "count": 0,
                "subtree_nodes": 0,
            })
            entry["count"] += child["count"]
            entry["subtree_nodes"] = max(
                entry["subtree_nodes"], subtree_size(nodes, child_id)
            )
    root["children"] = len(children)

    by_count = {}
    for entry in children.values():
        by_count.setdefault(entry["count"], []).append(entry)
    groups = []
    for count, fields in sorted(by_count.items(), key=lambda kv: -kv[0]):
        groups.append({
            "count": count,
            "share": round(count / root["records"], 4) if root["records"] else 0.0,
            "fields": fields,
            "subtree_nodes": sum(f["subtree_nodes"] for f in fields),
        })
    return root, groups


def _block_leaves(nodes, fields, subtree_id, records):
    """One leaf per direct child of a subtree, most records first: the candidate
    terms of a disjunction discriminator.

    One per child block, because the leaves under one child are mostly the same
    records -- `toolUseResult.stdout` adds nothing to `toolUseResult.interrupted`
    -- and a term that adds nothing costs a query to discover. A leaf inside a
    structured array is ranked last: its node count is per element, not per
    record, so it cannot be compared with the others.
    """
    leaves = []
    for block_id in nodes[subtree_id].get("children") or []:
        best = None
        stack = [(block_id, 0)]
        while stack:
            node_id, depth = stack.pop()
            node = nodes[node_id]
            kids = node.get("children") or []
            if not kids and node_id in fields and QUERYABLE_PATH.match(fields[node_id][0]):
                key = ("[]" in fields[node_id][1], depth,
                       -min(node["count"], records), fields[node_id][0])
                if best is None or key < best:
                    best = key
            for kid in kids:
                stack.append((kid, depth + 1))
        if best is not None:
            leaves.append({"path": best[3], "count": -best[2], "in_array": best[0]})
    leaves.sort(key=lambda leaf: (leaf["in_array"], -leaf["count"], leaf["path"]))
    seen = set()
    unique = []
    for leaf in leaves:
        if leaf["path"] in seen:
            continue
        seen.add(leaf["path"])
        unique.append(leaf)
    return unique


def family_candidates(trees):
    """(root_records, candidates): one discriminator per child subtree of the
    record root, for `partition_families` to test by query.

    A candidate is {"path", "predicate", "kind", "count", "co_equal"}. `path`
    names it and `predicate` is the KQL that selects it, since that is what
    actually queries:

    - "scalar": a scalar child is its own discriminator.
    - "structural": an object or array child with a leaf descendant whose count
      equals the subtree's own count, so every record reaching the subtree
      carries it -- the shallowest such leaf, then the shortest path.
    - "structural-disjunction": no single leaf covers the subtree, so the
      candidate carries `leaves` (see `_block_leaves`) and its predicate is
      built by `partition_families`, which ORs them until the count matches the
      subtree's. Most of these are real families: `toolUseResult` needs one term
      per kind of tool result and is 10% of a session archive's records.

    Every child is its own candidate. An earlier version merged candidates that
    read the same count, on the assumption that the same records carry them --
    which is false and expensive: a Claude session archive has three kinds of
    record numbering exactly 2,249, and merging `aiTitle` into `mode` dropped a
    real family and put its 2,249 records in the residual. Duplicates are cheap
    to find honestly instead: `partition_families` discards them as "same
    records as" the family they duplicate, one query each, and hangs them on
    that family as `co_equal`.

    Candidates are ordered structural first, then by count descending. That
    order is what `partition_families` rests on: which subtree a record
    populates is what makes it a kind of record, while a scalar at the root can
    be carried by records of several kinds, so a subtree's discriminator is
    tried first and a scalar becomes a family only when it is disjoint from
    every family already found.
    """
    root_records = 0
    candidates = {}
    for tree in trees:
        nodes = {n["id"]: n for n in tree["nodes"]}
        roots = [n for n in tree["nodes"] if n["parent_id"] == -1]
        if not roots:
            continue
        record_root = max(roots, key=lambda n: n["count"])
        root_records += record_root["count"]
        fields = node_fields(tree, NO_COLLAPSE)
        for child_id in record_root.get("children") or []:
            child = nodes[child_id]
            if child_id not in fields:
                continue
            if not child.get("children"):
                if not QUERYABLE_PATH.match(fields[child_id][0]):
                    continue
                key, entry = fields[child_id][0], {
                    "path": fields[child_id][0], "kind": "scalar",
                    "predicate": f"{fields[child_id][0]}:*", "count": 0,
                }
            else:
                best = None
                stack = [(child_id, 0)]
                while stack:
                    node_id, depth = stack.pop()
                    node = nodes[node_id]
                    kids = node.get("children") or []
                    if (not kids and node["count"] == child["count"]
                            and node_id in fields
                            and QUERYABLE_PATH.match(fields[node_id][0])):
                        leaf = (depth, len(fields[node_id][0]), fields[node_id][0])
                        if best is None or leaf < best:
                            best = leaf
                    for kid in kids:
                        stack.append((kid, depth + 1))
                if best is not None:
                    key, entry = best[2], {
                        "path": best[2], "kind": "structural",
                        "predicate": f"{best[2]}:*", "count": 0,
                    }
                else:
                    shown = fields[child_id][1]
                    key, entry = ("disjunction", shown), {
                        "path": shown, "kind": "structural-disjunction",
                        "predicate": None, "count": 0, "leaves": [],
                    }
            entry = candidates.setdefault(key, entry)
            entry["count"] += child["count"]
            if entry["kind"] == "structural-disjunction":
                known = {leaf["path"] for leaf in entry["leaves"]}
                entry["leaves"] += [
                    leaf for leaf in _block_leaves(nodes, fields, child_id, child["count"])
                    if leaf["path"] not in known
                ]
                entry["leaves"].sort(key=lambda leaf: (leaf["in_array"], -leaf["count"],
                                                       leaf["path"]))
    return root_records, sorted(
        (dict(entry, co_equal=[]) for entry in candidates.values()),
        key=lambda c: (c["kind"] == "scalar", -c["count"], len(c["path"]), c["path"]),
    )


def partition_families(root_records, candidates, count_records,
                       max_queries=DEFAULT_MAX_FAMILY_QUERIES,
                       max_discriminator_leaves=DEFAULT_MAX_DISCRIMINATOR_LEAVES):
    """A partition of the records into families, every disjointness proved by a
    query rather than inferred from the tree.

    `count_records(kql)` returns how many records match; the caller runs it
    (the library never searches). A candidate is accepted as a family only
    after it counts 0 records in common with every family already accepted, so
    the accepted set is pairwise disjoint by construction and the records left
    over are exactly `root_records` minus their sum.

    A candidate that is not disjoint from a family is discarded with the
    relation that ruled it out: a superset of the family (a common field that
    spans kinds of records, such as a `uuid` every record carries), inside it (a
    finer split of one kind), the same records (another name for it), or a
    partial overlap. The superset test is what keeps a common field out, and it
    runs against families already accepted -- which is why the order in
    `family_candidates` matters: read the other way it would discard any family
    that happens to contain a smaller optional field.

    A "structural-disjunction" candidate has its predicate built first, by
    `_resolve_disjunction`; it is tested like any other once it has one, and
    reported as undiscriminated when no disjunction covers its subtree.

    Returns {"families": [{"path", "predicate", "kind", "count", "share",
    "co_equal", "leaves"?}], "residual": {"count", "share"}, "discarded":
    [{..., "reason"}], "undiscriminated": [{"path", "records", "reason"}],
    "queries", "tested", "untested", "cap_hit"}. `residual` is reported even
    when it is zero. Testing stops when one more candidate could exceed
    max_queries; `untested` and `cap_hit` say so rather than letting a truncated
    partition look complete, and the caller is expected to print that it makes
    the residual an upper bound.
    """
    families = []
    discarded = []
    undiscriminated = []
    spent = [0]
    tested = 0

    def query(kql):
        """The count, or None when the budget is gone."""
        if spent[0] >= max_queries:
            return None
        spent[0] += 1
        return count_records(kql)

    for candidate in candidates:
        # A candidate is tested whole or not at all: its own count, then one
        # query per family already accepted.
        if spent[0] + 1 + len(families) > max_queries:
            break
        tested += 1
        # Free verdict: the families are disjoint, so a candidate carrying more
        # records than the ones still uncovered has to overlap one of them. A
        # scalar root child's own count is a lower bound on what `field:*`
        # matches (drift only adds types), so this needs no query.
        remaining = root_records - sum(f["count"] for f in families)
        if candidate["kind"] == "scalar" and candidate["count"] > remaining:
            discarded.append(dict(candidate, reason=(
                f"more records than the {remaining} left uncovered, so it spans"
                " families")))
            continue
        if candidate["kind"] == "structural-disjunction":
            reason = _resolve_disjunction(candidate, query, max_discriminator_leaves)
            if reason is not None:
                undiscriminated.append({
                    "path": candidate["path"], "records": candidate["count"],
                    "reason": reason,
                })
                continue
            count = candidate["count"]
        else:
            count = query(candidate["predicate"])
            if count is None:
                tested -= 1
                break
            if count <= 0:
                discarded.append(dict(candidate, count=0,
                                      reason="no records match it"))
                continue
        candidate = dict(candidate, count=count)
        reason = None
        # Largest family first: an overlap is likeliest there, and the query
        # that finds one ends the candidate.
        for family in sorted(families, key=lambda f: -f["count"]):
            common = query(f"{candidate['predicate']} AND {family['predicate']}")
            if common is None:
                reason = "the query budget ran out before it could be tested"
                undiscriminated.append({
                    "path": candidate["path"], "records": candidate["count"],
                    "reason": reason,
                })
                break
            if common == 0:
                continue
            if common == count == family["count"]:
                reason = f"same records as {family['path']}"
            elif common == family["count"]:
                reason = f"superset of {family['path']}"
            elif common == count:
                reason = f"inside {family['path']}"
            else:
                reason = f"overlaps {family['path']} on {common} records"
            discarded.append(dict(candidate, reason=reason))
            break
        else:
            families.append(candidate)
    covered = sum(f["count"] for f in families)
    residual = root_records - covered
    if residual < 0:
        raise ValueError(
            f"families cover {covered} of {root_records} records: the counts "
            "are inconsistent, so this is not a partition"
        )
    for family in families:
        family["share"] = round(family["count"] / root_records, 6) if root_records else 0.0
        # Verified duplicates, not fields that happen to read the same count.
        family["co_equal"] = [
            row["path"] for row in discarded
            if row["reason"] == f"same records as {family['path']}"
        ]
    families.sort(key=lambda f: (-f["count"], f["path"]))
    undiscriminated.sort(key=lambda u: (-u["records"], u["path"]))
    return {
        "families": families,
        "residual": {
            "count": residual,
            "share": round(residual / root_records, 6) if root_records else 0.0,
        },
        "discarded": discarded,
        "undiscriminated": undiscriminated,
        "queries": spent[0],
        "tested": tested,
        "untested": len(candidates) - tested,
        "cap_hit": tested < len(candidates),
    }


def _resolve_disjunction(candidate, query, max_leaves):
    """Give a subtree with no full-coverage leaf a predicate that selects it
    exactly: its block leaves ORed together until the count matches the
    subtree's own. Sets candidate["predicate"] and candidate["leaves"] to what
    was used, and returns None on success or the reason it could not.

    A term that adds no records is dropped rather than kept, so the width is
    spent on terms that cover something: on a session archive the first five
    `toolUseResult` leaves by count are four names for the same 3,027 bash
    results. Each attempt is one counting query, so the search also stops once
    the remaining leaves cannot add up to what is missing.
    """
    target = candidate["count"]
    leaves = candidate.get("leaves") or []
    if not leaves:
        return "no queryable leaf under it"
    chosen = []
    covered = 0
    for i, leaf in enumerate(leaves):
        if len(chosen) >= max_leaves:
            return (f"{max_leaves} leaves cover {covered} of its {target} records"
                    " (--max-discriminator-leaves)")
        # The leaves left cannot close the gap, so no wider disjunction will.
        if covered + sum(other["count"] for other in leaves[i:]) < target:
            break
        count = query(" OR ".join(f"{p}:*" for p in chosen + [leaf["path"]]))
        if count is None:
            return "the query budget ran out before its discriminator was built"
        if count > target:
            return (f"{leaf['path']} matches records outside the subtree"
                    f" ({count} against its {target})")
        if count > covered:
            chosen.append(leaf["path"])
            covered = count
        if covered == target:
            candidate["leaves"] = chosen
            candidate["predicate"] = (
                chosen[0] + ":*" if len(chosen) == 1
                else "(" + " OR ".join(f"{p}:*" for p in chosen) + ")"
            )
            return None
    return f"its leaves cover {covered} of its {target} records"


def record_root_count(trees):
    """Records under the record roots, summed across archives."""
    total = 0
    for tree in trees:
        roots = [n for n in tree["nodes"] if n["parent_id"] == -1]
        if roots:
            total += max(roots, key=lambda n: n["count"])["count"]
    return total


def value_partition_fields(trees, max_children=DEFAULT_MAX_CHILDREN,
                           min_coverage=DEFAULT_MIN_PARTITION_COVERAGE):
    """(root_records, fields, closest): the scalar fields that carry enough of
    the records to partition them, best coverage first, and -- when there are
    none -- the scalar field that came closest, so a caller can say how near the
    archive was rather than only that it failed.

    The filter is the one behind the bootstrap's DIST line -- a scalar of
    DIST_FIELD_TYPES, outside any array, not a collapsed data-as-keys row --
    since that line exists to find exactly this kind of field, the
    severity/logger-like one whose values name a kind of record. What is added
    here is coverage: a field has to carry at least `min_coverage` of the
    records, and the ones it misses are the residual.

    Best coverage first, so a field the whole archive carries always beats one
    that nearly does, however few values the latter has; then the shallowest,
    since a field at the root describes the record rather than a part of it;
    then the fewest values, for the most records per family. Cardinality costs a
    query, so the caller measures it.

    A field that drifts between types covers less by this test, and rightly:
    each of its types is its own row, and only what one type covers can be
    counted on.
    """
    root_records = record_root_count(trees)
    scalars = [
        {"path": row["path"], "display": row["display"], "type": row["type"],
         "records": row["records"], "depth": row["display"].count(".")}
        for row in summarize(trees, max_children).values()
        if row["type"] in DIST_FIELD_TYPES
        and "[]" not in row["display"]
        and not row["collapsed_keys"]
        and root_records > 0
        and QUERYABLE_PATH.match(row["path"])
    ]
    fields = [f for f in scalars if f["records"] >= min_coverage * root_records]
    fields.sort(key=lambda f: (-f["records"], f["depth"], len(f["path"]), f["path"]))
    closest = None
    if not fields and scalars:
        closest = max(scalars, key=lambda f: (f["records"], -f["depth"], f["path"]))
    return root_records, fields, closest


def value_predicate(field, value):
    """`field:"value"` for one value of a field, or None for a value no
    predicate can express.

    Strings are quoted and escaped by lib/kql_build's own escaping, the rule the
    rest of the plugin renders values with: a space would otherwise make clp-s
    read the query as natural language, and a `*` or `?` would silently turn
    the value into a pattern. Numbers and booleans are written bare, so they
    match as numbers rather than as text. Anything else -- a null, an object --
    has no value predicate, and the caller reports it instead of guessing.
    """
    if isinstance(value, bool):
        return f"{field}:{'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{field}:{value}"
    if not isinstance(value, str):
        return None
    return f'{field}:"{escape_value(value)}"'


def partition_by_value(field, values, root_records, count_records,
                       max_queries=DEFAULT_MAX_FAMILY_QUERIES):
    """A partition of the records by the values of one universal field, in the
    shape `partition_families` returns.

    A scalar field holds one value per record, so the families are disjoint by
    construction and need no pairwise queries -- one count per value, and the
    sum is the proof: it has to be every record, and the caller fails loudly
    when it is not. A value with no expressible predicate is discarded with that
    reason, which the residual then accounts for.
    """
    families = []
    discarded = []
    queries = 0
    tested = 0
    for value in values:
        predicate = value_predicate(field, value)
        name = predicate if predicate else f"{field}={value!r}"
        if predicate is None:
            discarded.append({
                "path": name, "predicate": None, "kind": "value", "count": 0,
                "co_equal": [], "value": value,
                "reason": "no KQL value predicate can express it",
            })
            continue
        if queries + 1 > max_queries:
            break
        tested += 1
        queries += 1
        count = count_records(predicate)
        families.append({
            "path": predicate, "predicate": predicate, "kind": "value",
            "count": count, "co_equal": [], "value": value,
        })
    covered = sum(f["count"] for f in families)
    residual = root_records - covered
    if residual < 0:
        raise ValueError(
            f"the values of {field} cover {covered} of {root_records} records: "
            "it does not partition them, so it is not a partition field"
        )
    for family in families:
        family["share"] = round(family["count"] / root_records, 6) if root_records else 0.0
    families.sort(key=lambda f: (-f["count"], f["path"]))
    return {
        "families": families,
        "residual": {
            "count": residual,
            "share": round(residual / root_records, 6) if root_records else 0.0,
        },
        "discarded": discarded,
        "undiscriminated": [],
        "queries": queries,
        "tested": tested,
        "untested": len(values) - tested - len(discarded),
        "cap_hit": tested + len(discarded) < len(values),
    }
