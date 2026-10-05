#!/usr/bin/env python3
"""
clp schema - list every field of a clp-s archive from its merged schema
tree: the KQL path, the type, and how many records carry it.

Usage:
  clp schema [options] ARCHIVES_DIR

Options:
  --log-shapes-file F   A raw stats.log_shapes dump of the same archive. When its
                        lines carry node_counts (clp-s builds that store
                        per-field log shape counts), each text field also gets
                        how many values were templated in it and how many
                        distinct templates they use.
  --max-children N      Collapse an object with more than N children (data used
                        as keys, such as one key per file path) into one `*`
                        row. Default: 50.
  --tree-file F         Read the stats.schema_tree output from F instead of
                        running the query.
  --top N               Print only the N fields with the most records (the
                        JSON output and TEXT_FIELDS keep them all).
  --json-out F          Also write the rows as JSON to F.
  --drift               Report the field paths the archive stores under more
                        than one type, instead of the field rows.
  --drift-file F        Also write those paths as NDJSON to F (implies --drift;
                        an empty file means nothing drifts).
  --field-counts        Report the record root's children grouped by record
                        count, instead of the field rows. No queries.
  --field-counts-file F Also write those groups as NDJSON to F (implies
                        --field-counts).
  --search-wrapper P    Search wrapper: one executable (default: `clp
                        search`).

Output, one line each:
  TREE_ARCHIVES=N
  TREE_FIELDS=N          rows after collapsing
  FIELD path=P type=T records=N [values=V templates=K] [collapsed_keys=C] [shown=D]
                         one per field, most records first. P is the KQL path;
                         D, printed when it differs, marks array elements with
                         [] (message.content[].name is queried as
                         message.content.name). Containers
                         (Object, StructuredArray) are left out except for a
                         collapsed row
  TEXT_FIELDS=P1,P2,...  the ClpString fields (the ones templated into log
                         shapes), most values first when node counts are known,
                         else most records first

With --drift, instead:
  DRIFT_PATHS=N          distinct field paths in the tree
  DRIFT_COUNT=N          how many of them carry more than one type
  DRIFT path=P types=ClpString:116,Object:4525 records=4641 [kql_path=K]
                         one per drifting path, most records first. P marks
                         array elements with []; K, printed when it differs, is
                         the form to query. The types are ordered by NodeType
                         id and records is their sum

Why: the merged schema tree keeps one node per path and type, so a field that
is a ClpString on a failed tool call and an Object on a success is two nodes
under one key -- drift the tree already knows. It is worth knowing because a
query written for one of the two shapes silently misses the records that carry
the other: a projection of the scalar returns nothing for the object records,
and a NOT predicate over the object matches every scalar record. Array
elements are left out; their element types are not a field that changed shape.

With --field-counts, instead:
  FIELD_COUNT_ROOT id=N type=T records=N children=N
                         the record root: the parent_id == -1 node with the
                         most records (a tree also roots the archive's
                         Metadata), and how many child fields it has
  FIELD_COUNT_GROUPS=N   how many distinct record counts its children read
  FIELD_COUNT_NOTE=...   the warning below, on the output itself
  FIELDS count=32614 share=72.8% fields=uuid,cwd,version subtree_nodes=0 [more=N]
                         one per record count, most records first: the fields
                         reading it, and how many nodes hang below them. At
                         most 12 fields are named on the line (more=N counts
                         the rest); the NDJSON file keeps them all

Why: for JSON logs the text dictionary can hold tens of thousands of templates
while the structural variety is small -- most records carry one block of fields
and the rest populate a different subtree. Fields that read a co-equal count
are one such block, carried by the same records, so they are reported on one
line rather than as one fact each.

These are per-field record counts and NOT a partition of the records: a record
that populates `type`, `sessionId` and `message` is counted on all three lines,
so the shares overlap, they do not sum to 100%, and they must never be summed.
The tree stores a count per node and nothing about which nodes appear together,
so co-occurrence cannot be read off it at all.

Why (the field rows): the log-insights bootstrap used to guess the schema from
the top-level keys of a sample's first record. The tree is exact and
whole-archive: every path, including the fields inside structured arrays that a
session record keeps its text in.
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import commands as C  # noqa: E402
from schema_tree import (  # noqa: E402
    CONTAINER_TYPES,
    DEFAULT_MAX_CHILDREN,
    field_counts,
    load_trees,
    node_fields,
    summarize,
    type_drift,
)


def read_trees(wrapper, archives_dir):
    proc = subprocess.run(
        [*wrapper, archives_dir, "stats.schema_tree"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    trees = load_trees(proc.stdout.splitlines())
    if not trees:
        sys.stderr.write(proc.stderr)
        print(f"error: stats.schema_tree returned no tree for {archives_dir}", file=sys.stderr)
        sys.exit(1)
    return trees


def field_value_counts(log_shapes_file, trees, max_children):
    """{display_path: [values, templates]} from the dump's node_counts, or None
    when no line carries them."""
    fields_by_archive = {
        t.get("archive_id"): node_fields(t, max_children) for t in trees
    }
    counts = {}
    seen = False
    with open(log_shapes_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            node_counts = obj.get("node_counts")
            if not isinstance(node_counts, dict):
                continue
            seen = True
            fields = fields_by_archive.get(obj.get("archive_id"), {})
            for node_id, n in node_counts.items():
                field = fields.get(int(node_id))
                if field is None:
                    continue
                entry = counts.setdefault(field[1], [0, 0])
                entry[0] += n
                entry[1] += 1
    return counts if seen else None


# Fields named on a FIELDS line. A root whose keys are data (one per file) can
# put thousands of one-record fields in a single group; the NDJSON keeps all.
MAX_GROUP_FIELDS = 12

FIELD_COUNT_NOTE = (
    "per-field record counts, not a partition: a record is counted on every "
    "line whose field it carries, so the shares overlap and must not be "
    "summed. Fields sharing a count are carried by the same records."
)


def write_ndjson(path, rows):
    """Write one JSON object per line, truncating the file when there are
    none: an empty file is the answer "nothing here", not a missing one."""
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_drift(trees, drift_file):
    rows, paths = type_drift(trees)
    print(f"DRIFT_PATHS={paths}")
    print(f"DRIFT_COUNT={len(rows)}")
    for row in rows:
        types = ",".join(f"{t['type']}:{t['count']}" for t in row["types"])
        extra = "" if row["kql_path"] == row["path"] else f" kql_path={row['kql_path']}"
        print(f"DRIFT path={row['path']} types={types} records={row['records']}{extra}")
    if drift_file:
        write_ndjson(drift_file, rows)


def print_field_counts(trees, field_counts_file):
    root, groups = field_counts(trees)
    ids = ",".join(str(i) for i in root["ids"])
    print(f"FIELD_COUNT_ROOT id={ids} type={root['type']} records={root['records']}"
          f" children={root['children']}")
    print(f"FIELD_COUNT_GROUPS={len(groups)}")
    print(f"FIELD_COUNT_NOTE={FIELD_COUNT_NOTE}")
    for group in groups:
        fields = [f["key"] for f in group["fields"]]
        extra = ""
        if len(fields) > MAX_GROUP_FIELDS:
            extra = f" more={len(fields) - MAX_GROUP_FIELDS}"
            fields = fields[:MAX_GROUP_FIELDS]
        print(f"FIELDS count={group['count']} share={group['share'] * 100:.1f}%"
              f" fields={','.join(fields)}"
              f" subtree_nodes={group['subtree_nodes']}{extra}")
    if field_counts_file:
        write_ndjson(field_counts_file, groups)



def main():
    parser = argparse.ArgumentParser(
        add_help=True, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("archives_dir")
    parser.add_argument("--log-shapes-file")
    parser.add_argument("--max-children", type=int, default=DEFAULT_MAX_CHILDREN)
    parser.add_argument("--tree-file")
    parser.add_argument("--top", type=int, default=0)
    parser.add_argument("--json-out")
    parser.add_argument("--drift", action="store_true")
    parser.add_argument("--drift-file")
    parser.add_argument("--field-counts", action="store_true")
    parser.add_argument("--field-counts-file")
    parser.add_argument("--search-wrapper")
    args = parser.parse_args()
    # One path, not an argv: a caller replacing the search with its own program gives one
    # executable. The default is this plugin's own `clp search`, which is two words.
    args.search_wrapper = [args.search_wrapper] if args.search_wrapper else C.search_argv()
    drift = args.drift or bool(args.drift_file)
    counts_mode = args.field_counts or bool(args.field_counts_file)

    if args.tree_file:
        with open(args.tree_file, "r", encoding="utf-8") as f:
            trees = load_trees(f)
        if not trees:
            print(f"error: no schema tree in {args.tree_file}", file=sys.stderr)
            return 1
    else:
        trees = read_trees(args.search_wrapper, args.archives_dir)

    # These are readings of the same tree, not extra field rows, so each prints
    # its own report and nothing else.
    if drift:
        print_drift(trees, args.drift_file)
    if counts_mode:
        print_field_counts(trees, args.field_counts_file)
    if drift or counts_mode:
        return 0

    rows = summarize(trees, args.max_children)
    counts = None
    if args.log_shapes_file:
        counts = field_value_counts(args.log_shapes_file, trees, args.max_children)

    out = []
    for row in rows.values():
        if row["type"] in CONTAINER_TYPES and not row["collapsed_keys"]:
            continue
        item = {
            "path": row["path"], "display": row["display"], "type": row["type"],
            "records": row["records"],
        }
        if counts is not None and row["type"] == "ClpString":
            values, templates = counts.get(row["display"], [0, 0])
            item["values"] = values
            item["templates"] = templates
        if row["collapsed_keys"]:
            item["collapsed_keys"] = len(row["collapsed_keys"])
        out.append(item)
    out.sort(key=lambda r: (-r["records"], r["display"]))

    print(f"TREE_ARCHIVES={len(trees)}")
    print(f"TREE_FIELDS={len(out)}")
    for item in out[:args.top] if args.top > 0 else out:
        extra = ""
        if "values" in item:
            extra += f" values={item['values']} templates={item['templates']}"
        if "collapsed_keys" in item:
            extra += f" collapsed_keys={item['collapsed_keys']}"
        if item["display"] != item["path"]:
            extra += f" shown={item['display']}"
        print(f"FIELD path={item['path']} type={item['type']} records={item['records']}{extra}")
    text = [r for r in out if r["type"] == "ClpString"]
    text.sort(key=lambda r: (-r.get("values", r["records"]), r["display"]))
    print("TEXT_FIELDS=" + ",".join(dict.fromkeys(r["path"] for r in text)))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
