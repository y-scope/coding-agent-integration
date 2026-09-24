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

Stdlib only, like the other log-shape-* helpers.
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
        type_name = TYPE_NAMES.get(node["type"], f"Type{node['type']}")
        if type_name == "Metadata":
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
            fields[node_id] = (".".join(kql), ".".join(display), type_name, collapsed)
        children = node.get("children") or []
        collapse_children = (
            node["parent_id"] != -1
            and type_name == "Object"
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
        for node_id, (kql, display, type_name, collapsed) in node_fields(
                tree, max_children).items():
            row = rows.setdefault((display, type_name), {
                "path": kql, "display": display, "type": type_name,
                "records": 0, "collapsed_keys": set(),
            })
            row["records"] += nodes[node_id]["count"]
            if collapsed:
                row["collapsed_keys"].add(nodes[node_id]["key"])
    return rows
