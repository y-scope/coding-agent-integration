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

Stdlib only, like the other plugin helpers.
"""

import json

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

# A max_children no object reaches: drift reports the real keys, since each
# child of a data-as-keys object carries its own types and count.
NO_COLLAPSE = 1 << 62


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
    nor sum to 100%.

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
