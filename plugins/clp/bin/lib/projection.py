"""projection - rewrite --projection columns into ones clp-s can return.

clp-s projects leaf columns only. An array is a leaf: it is stored and returned
whole, so a column inside one (`message.content.text` when `message.content` is
an array of blocks) projects nothing, although a KQL filter on that same column
matches. An object is not a leaf, so projecting it (`message`) also returns
nothing. Either way the search runs, matches, and prints `{}` for each record,
which reads as records without the field.

Given the archive's schema tree (`clp-s s --experimental ARCHIVE
stats.schema_tree`, one JSON line per archive) on stdin and the requested
columns as arguments, this prints one column per line to project instead:

  - a column inside an array becomes the outermost array that holds it;
  - an object becomes the leaf columns under it (arrays included), up to
    MAX_EXPANDED of them, leaving out any whose keys would need escaping;
  - anything else (a leaf column, a column this archive does not have, a
    shape()/decompose() projection) is kept as given.

Each rewrite is explained on stderr as a line starting "projection: ". When the
schema tree cannot be read, every column is kept as given.

Usage:
  clp-s s --experimental ARCHIVE stats.schema_tree | python3 lib/projection.py COLUMN...

Stdlib only, like the other log-shape-* helpers.
"""

import json
import re
import sys
from collections import defaultdict

# clp-s's NodeType (components/core/src/clp_s/SchemaTree.hpp).
OBJECT = 5
ARRAYS = {6, 9}  # UnstructuredArray, StructuredArray
METADATA = 10

MAX_EXPANDED = 64

# A key clp-s reads literally in a column descriptor: no escape, separator or
# wildcard character, no control character, and no leading namespace sigil.
PLAIN_KEY = re.compile(r'^[^.\\*?"@$!#\x00-\x1f][^.\\*?"\x00-\x1f]*$')


def split_column(column):
    """A column descriptor's keys, split at unescaped dots, with the text of
    the descriptor up to the end of each key (so a prefix keeps the caller's
    own escaping)."""
    keys, ends, cur, escaped = [], [], [], False
    for i, ch in enumerate(column):
        if escaped:
            cur.append(ch if ch == "." else "\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ".":
            keys.append("".join(cur))
            ends.append(i)
            cur = []
        else:
            cur.append(ch)
    keys.append("".join(cur))
    ends.append(len(column))
    return keys, [column[:e] for e in ends]


def column_types(tree_lines):
    """{key path: {node types}} over every archive's schema tree, and
    {key path: [child key paths]}, key paths being tuples of raw keys. The
    metadata subtree is left out."""
    types = defaultdict(set)
    children = defaultdict(list)
    for line in tree_lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            nodes = {n["id"]: n for n in json.loads(line).get("nodes", [])}
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            continue
        paths = {}

        def path_of(node_id):
            if node_id not in paths:
                node = nodes[node_id]
                parent = node.get("parent_id", -1)
                if parent == -1:
                    paths[node_id] = None if node.get("type") == METADATA else ()
                else:
                    base = path_of(parent) if parent in nodes else None
                    paths[node_id] = None if base is None else base + (str(node.get("key", "")),)
            return paths[node_id]

        for node_id, node in nodes.items():
            path = path_of(node_id)
            if not path:
                continue
            types[path].add(node.get("type"))
            if path not in children[path[:-1]]:
                children[path[:-1]].append(path)
    return types, children


def leaves_under(path, types, children):
    """The leaf key paths under an object, arrays included, in schema order."""
    out = []
    stack = list(reversed(children.get(path, [])))
    while stack:
        p = stack.pop()
        if types[p] - {OBJECT}:
            out.append(p)
        if OBJECT in types[p]:
            stack.extend(reversed(children.get(p, [])))
    return out


def resolve(column, types, children, notes):
    """The columns to project for one requested column."""
    if "(" in column or not types:
        return [column]
    keys, prefixes = split_column(column)
    path = tuple(keys)
    for i in range(1, len(path)):
        if types.get(path[:i], set()) & ARRAYS:
            prefix = prefixes[i - 1]
            notes.append(f"projection: {column} -> {prefix} ({prefix} is an array, which clp-s "
                         "returns whole; a column inside it projects nothing)")
            return [prefix]
    kinds = types.get(path)
    if kinds is None:
        notes.append(f"projection: {column} is not a column of this archive; it projects nothing")
        return [column]
    if OBJECT not in kinds:
        return [column]
    leaves = leaves_under(path, types, children)
    plain = [p for p in leaves if all(PLAIN_KEY.match(k) for k in p[len(path):])]
    out = ([column] if kinds - {OBJECT} else []) + [".".join((column,) + p[len(path):]) for p in plain]
    if not out:
        notes.append(f"projection: {column} is an object, which projects nothing, and every key under "
                     "it needs escaping; project those columns by name")
        return [column]
    shown = out[:MAX_EXPANDED]
    left = []
    if len(out) > MAX_EXPANDED:
        left.append(f"the first {MAX_EXPANDED} of {len(out)}")
    if len(plain) < len(leaves):
        left.append(f"{len(leaves) - len(plain)} whose keys need escaping left out")
    notes.append(f"projection: {column} -> {','.join(shown)} ({column} is an object, which projects "
                 "nothing; these are the leaf columns under it" + "".join(f"; {x}" for x in left) + ")")
    return shown


def resolve_all(columns, tree_lines):
    types, children = column_types(tree_lines)
    notes, out = [], []
    for column in columns:
        for c in resolve(column, types, children, notes):
            if c not in out:
                out.append(c)
    return out, notes


def main(argv=None) -> int:
    columns = sys.argv[1:] if argv is None else argv
    out, notes = resolve_all(columns, sys.stdin)
    for note in notes:
        print(note, file=sys.stderr)
    for c in out:
        print(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
