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
  --record-families     Report a partition of the records into families,
                        instead of the field rows. Unlike every other mode this
                        one runs counting queries against the archive, because
                        the tree cannot tell which fields co-occur.
  --record-families-file F
                        Also write the partition as NDJSON to F (implies
                        --record-families).
  --family-method M     How --record-families partitions: value (the values of
                        one universal low-cardinality field), existence (which
                        subtree a record populates), or auto, the default --
                        value when such a field exists, else existence, which
                        is coarser and hundreds of queries dearer. Asking for
                        existence while value applies also prints what value
                        would have found, so the approximation is visible.
  --max-partition-values N
                        Distinct values a field may have and still be used to
                        partition (default: 64).
  --min-partition-coverage F
                        Share of the records a field must carry to partition
                        them (default: 0.99). The records it misses become the
                        residual, so this widens which field may be chosen and
                        changes no arithmetic. 1.0 demands a field every record
                        carries.
  --max-family-queries N
                        Counting queries --record-families may spend (default:
                        1000). The value method takes about 15; existence about
                        520 and half a minute on a 44,818-record session
                        archive. It is a ceiling for pathological archives, not
                        a budget to tune down: stopping early reports a
                        residual larger than the truth.
  --max-discriminator-leaves N
                        Leaves a disjunction discriminator may OR together
                        (default: 12; existence method only).
  --partition-probe-budget S
                        Seconds of --unique probing after which a field that
                        already partitions the records is taken, rather than
                        tie-broken against the candidates left (default: 60).
                        Fields tying on coverage and depth are usually siblings
                        under one parent and only their value counts separate
                        them, which costs one --unique each -- the dearest
                        query the engine runs, ~22x a --count over the same
                        data. Below the budget every candidate is still
                        measured, so ordinary archives rank as they always did;
                        0 takes the first field that qualifies. A field with a
                        single distinct value never qualifies at any budget: it
                        puts every record in one family.
  --kinds               Also split each record family into kinds (implies
                        --record-families), by a field only that family
                        carries whose values change which fields a record
                        holds. A few more searches: about 50 to 70, 3 to 6 s,
                        on a session bundle. On a large archive a family whose
                        sample shows one value of a field costs a --unique,
                        which can take minutes; the bootstrap asks for kinds
                        only with --fields-only.
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
so co-occurrence cannot be read off it at all -- that is what --record-families
is for.

With --record-families, instead (this mode queries the archive):
  RECORD_FAMILY_METHOD=value field=type values=14 coverage=99.97% uncovered=36
  RECORD_FAMILY_METHOD=existence reason=...
                               which partition this is. For value, what share
                               of the records the field carries and how many it
                               does not -- always printed, 100.00% included, so
                               a partition of nearly all the records is never
                               read as one of all of them; the uncovered ones
                               are in the residual. For existence, why value
                               did not apply (or that it was asked for)
  RECORD_FAMILY_ROOT records=N the records the partition has to account for
  RECORD_FAMILY_COUNT=N        families in the partition
  RECORD_FAMILY_QUERIES=N      counting queries spent, of --max-family-queries
  RECORD_FAMILY_CANDIDATES=N tested=N untested=N cap=hit|no
                               candidates considered; untested ones are left
                               out of the partition, never silently
  RECORD_FAMILY_CAP_NOTE=...   printed only when cap=hit: the residual is then
                               an upper bound and real families may be inside
                               it. A truncated partition must not read like a
                               complete one
  RECORD_FAMILY name=message.role count=16604 share=37.1% kind=structural [also=P,...] [leaves=N]
                               one per family, most records first. `name` is
                               the discriminator's KQL path -- the query that
                               selects the family. `kind` is scalar,
                               structural or structural-disjunction, and for
                               the last one `leaves=N` is the width of the
                               disjunction, whose predicate is in the NDJSON,
                               since it has spaces in it. `also=` names other
                               fields a query proved to select the very same
                               records (not merely to read the same count --
                               three kinds of record in a session archive
                               number exactly 2,249 and are nothing alike)
  RECORD_FAMILY_RESIDUAL count=9819 share=21.9%
                               records no family's discriminator matches,
                               printed even when it is 0
  RECORD_FAMILY_SHARE_SUM=100.0%
                               the printed shares, which are one-decimal
                               percentages adjusted to sum to exactly 100.0 (no
                               value moves by as much as a tenth; the NDJSON
                               carries the exact fractions). The counts are
                               checked first and the run fails without printing
                               a partition if they do not cover the root
                               exactly: that invariant is the point of the mode
  RECORD_FAMILY_DISCARDED name=P count=N reason=... [also=P,...]
                               a candidate that is not disjoint from a family,
                               with the relation that ruled it out
  RECORD_FAMILY_UNDISCRIMINATED path=P records=N reason=...
                               a subtree no query selects exactly: not even a
                               disjunction of its leaves covers every one of
                               its records (an empty object among them will do
                               that), with the count it did reach
  RECORD_FAMILY_COMPARE method=value field=type families=14 residual=0 queries=15 against existence ...
                               printed when existence was asked for although
                               value applies, so the cost of the coarser answer
                               is on the page

Why: the field counts above overlap, so they cannot answer "how do the records
divide".

The value method answers it exactly where it applies. A field nearly every
record carries, with few enough distinct values, already names the kinds of
record -- `type` on a Claude session archive has 14 values which sum to all
44,818 -- so its values are the families, one `--count FIELD:"<value>"` each.
The field is chosen the way the bootstrap's DIST line chooses its fields (the
same scalar types, outside arrays, no collapsed data-as-keys row), plus the two
conditions that make a partition possible: it carries at least
--min-partition-coverage of the records, and it has at most
--max-partition-values values. Best coverage first, then shallowest, then
fewest values -- a field the whole archive carries always wins over one that
nearly does, since what a field misses lands in the residual. On a bundle of
five session archives, 36 records of 111,223 lack `type`: partitioning by it
leaves those 36 in the residual for 19 queries, where refusing it over them
costs 716 and a residual of 3,726. A scalar holds one value per record, so the
families cannot overlap and the sum is the whole proof. Values are quoted and escaped by the plugin's own rule
(lib/kql_build), numbers and booleans written bare so they match as numbers;
a value no predicate can express is reported rather than guessed at.

Failing that, the existence method. It picks one discriminator per child subtree
of the record root
-- a leaf every record of that subtree carries, a scalar child being its own --
and proves the division with `--count` queries instead of inferring it: a
candidate joins the partition only after counting 0 records in common with
every family already accepted. So the families are pairwise disjoint by
construction and the records left over are exactly the root's count minus their
sum, reported as the residual.

When no single leaf covers a subtree, its discriminator is built as a
disjunction of its leaves instead of giving up on it -- one leaf per child
block, ORed until the count matches the subtree's own, terms that add no records
dropped. `toolUseResult` needs 10 of them, one per kind of tool result, and
without them a tenth of a session archive would sit in the residual looking
unexplainable.

A candidate is tried structural-subtree-first and largest-first, because which
subtree a record populates is what makes it a kind of record, while a scalar at
the root can belong to records of several kinds. That is how a common field is
kept out: `message.role:* AND uuid:*` counts all 16604 message records, so
`uuid` is a superset of that family, not a sibling of it, and it is discarded
with that reason. The test runs against families already accepted, and only in
that direction -- read the other way it would discard `message.role` itself for
containing the optional `effort` field that some message records carry.

With --kinds, after the families:
  RECORD_KIND_COUNT=N          kinds in all: one per value of each splitting
                               field, and one per family nothing splits
  RECORD_KIND_QUERIES=N        searches the split spent, within the same
                               --max-family-queries
  RECORD_KIND_SPLIT field=F kinds=N residual=N separation=S family=P
  RECORD_KIND_WHOLE count=N family=P
                               one per family, split or left whole. The family
                               goes last, since a value can hold a space; why a
                               family stayed whole, and every field refused on
                               the way, is in the NDJSON's kind_split and
                               kind_whole rows
  RECORD_KIND count=N share=X% name=P
                               one per kind, most records first; P selects it
  RECORD_KIND_RESIDUAL count=N share=X%
                               the families' residual plus what each split
                               missed within its family; with the kinds, every
                               record once, checked before anything is printed

Why: a family is often several kinds of record under one value.
`type:"attachment"` is a third of a session archive and holds some twenty kinds
-- hook results, skill listings, token reminders, queued commands -- each with
fields of its own, and a question about one of them is one the family cannot
answer.

A kind is a value of a field that only the family carries, and whose values
change which fields a record holds. The second condition is what separates
`attachment.type` from `promptId`: both are carried by nearly every record of
their family, but an attachment's type decides its fields and a prompt id
decides nothing. It is judged on a sample of the family's records, where the
share of a field's values whose records all have a shape no other value's
records have is 1.00 for `attachment.type` and 0.00 for every identifier tried.
The split's counts are the archive's own, one per value, except in a family the
sample holds whole, where they are read off the sample exactly.

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
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import commands as C  # noqa: E402
from schema_tree import (  # noqa: E402
    CONTAINER_TYPES,
    DEFAULT_MAX_CHILDREN,
    DEFAULT_MAX_DISCRIMINATOR_LEAVES,
    DEFAULT_MAX_FAMILY_QUERIES,
    DEFAULT_MAX_PARTITION_VALUES,
    DEFAULT_MIN_PARTITION_COVERAGE,
    DEFAULT_PARTITION_PROBE_BUDGET,
    FAMILY_SAMPLE_RECORDS,
    VALUE_SAMPLE_RECORDS,
    MIN_KIND_SEPARATION,
    family_candidates,
    field_counts,
    kind_field_candidates,
    load_trees,
    node_fields,
    partition_by_value,
    partition_families,
    shape_separation,
    summarize,
    type_drift,
    value_partition_fields,
    value_predicate,
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
MAX_FAMILY_FIELDS = 12

FIELD_COUNT_NOTE = (
    "per-field record counts, not a partition: a record is counted on every "
    "line whose field it carries, so the shares overlap and must not be "
    "summed. Fields sharing a count are carried by the same records. For a "
    "partition of the records, run --record-families"
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
        if len(fields) > MAX_FAMILY_FIELDS:
            extra = f" more={len(fields) - MAX_FAMILY_FIELDS}"
            fields = fields[:MAX_FAMILY_FIELDS]
        print(f"FIELDS count={group['count']} share={group['share'] * 100:.1f}%"
              f" fields={','.join(fields)}"
              f" subtree_nodes={group['subtree_nodes']}{extra}")
    if field_counts_file:
        write_ndjson(field_counts_file, groups)


def display_shares(counts, total):
    """One-decimal percentages of total that sum to exactly 100.0, by giving the
    tenths lost to rounding to the largest remainders. Rounding each share on
    its own leaves the printed column at 99.9% or 100.1%, and a partition whose
    column does not add up is exactly what nobody should have to second-guess.
    Each value still differs from the true share by less than a tenth."""
    if total <= 0:
        return [0.0 for _ in counts]
    tenths = [c * 1000 // total for c in counts]
    order = sorted(range(len(counts)), key=lambda i: (-((counts[i] * 1000) % total),
                                                      -counts[i]))
    for i in order[:1000 - sum(tenths)]:
        tenths[i] += 1
    return [t / 10 for t in tenths]


def record_counter(wrapper, archives_dir):
    """count_records(kql) for the partition functions: the archive's own count
    aggregation, summed over its archives. A query that matches nothing prints an
    explicit "count":0 row per archive, and summing them gives the same 0 as the
    no-row-at-all this used to get."""
    def count_records(kql):
        proc = subprocess.run(
            [*wrapper, "--count", archives_dir, kql],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            raise RuntimeError(f"counting query failed: {kql}")
        total = 0
        for line in proc.stdout.splitlines():
            if not line.startswith("{"):
                continue
            try:
                total += json.loads(line).get("count", 0)
            except json.JSONDecodeError:
                continue
        return total
    return count_records


def field_values(wrapper, archives_dir, field, kql="*"):
    """A field's distinct values over the records matching kql, in the
    archive's own types, deduplicated across archives. None when the query
    fails."""
    proc = subprocess.run(
        [*wrapper, "--unique", field, archives_dir, kql],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if proc.returncode != 0:
        return None
    values, seen = [], set()
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "value" not in row:
            continue
        value = row["value"]
        key = (type(value).__name__, value if isinstance(value, (str, int, float, bool)) else repr(value))
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def record_sample(wrapper, archives_dir, kql, limit):
    """Up to `limit` records matching kql, as dicts. Empty when the query
    fails, which the shape test then counts against the field."""
    proc = subprocess.run(
        [*wrapper, "--limit", str(limit), archives_dir, kql],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    records = []
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            record.pop("archive_id", None)
            records.append(record)
    return records


def grouped(predicate):
    """A family predicate safe to AND with another term: an existence family's
    disjunction needs the parentheses, a value family's single term does not."""
    return f"({predicate})" if " OR " in predicate else predicate


def split_into_kinds(family, candidates, sample, wrapper, archives_dir, count_records, args,
                     max_queries):
    """One family split into kinds by a field only it carries, or the reason
    it stays whole.

    `candidates` are the family's kind_field_candidates, and `sample` is up to
    FAMILY_SAMPLE_RECORDS of its records, which the caller fetched. The field is
    the first candidate that passes three tests. A count of the family and the
    field together proves the field is the family's own. Then the shapes of its
    records have to tell the field's values apart (shape_separation). That test
    is the one that matters: an id, a number or a name passes the count as
    readily as a kind does -- on a session archive most fields that pass it are
    data. Last, the field has to have between 2 and --max-partition-values
    values within the family.

    The sample carries most of the cost. It refuses a field more of its records
    lack than the whole family may, which is how families of equal size turn
    away each other's fields. It judges the shapes, so an identifier is refused
    without a query of its own; only a field it shows a single value of costs a
    --unique and a few records per value. And when it holds the whole family,
    as it does for every family no larger than it, the count, the values and
    the kinds' counts are all read off it, exactly, with no query at all.

    Otherwise the kinds are counted the way partition_by_value counts families,
    one query per value, within the family; what they miss is the family's own
    residual. Every query counts against `max_queries`, and a family the budget
    cannot finish stays whole with that reason, never half split.
    """
    fc = family["count"]
    within = grouped(family["predicate"])
    whole = len(sample) == fc
    result = {"family": family["predicate"], "count": fc, "field": None, "kinds": [],
              "residual": 0, "queries": 0, "rejected": [], "reason": None}

    def spend(n=1):
        if result["queries"] + n > max_queries:
            raise _OutOfQueries()
        result["queries"] += n

    def reject(path, reason):
        result["rejected"].append({"field": path, "reason": reason})

    may_miss = fc - args.min_partition_coverage * fc
    try:
        for candidate in candidates:
            path = candidate["path"]
            missing = sum(1 for record in sample if value_at(record, path) is None)
            if missing > may_miss:
                reject(path, f"{missing} of the {len(sample)} sampled records lack it")
                continue
            if not whole:
                spend()
                together = count_records(f"{within} AND {path}:*")
                if together < args.min_partition_coverage * fc:
                    reject(path, f"only {together} of the family's {fc} records carry it")
                    continue
            by_value, typed = {}, {}
            for record in sample:
                value = value_at(record, path)
                if value is not None and not isinstance(value, (dict, list)):
                    by_value.setdefault(repr(value), []).append(record)
                    typed.setdefault(repr(value), value)
            values = list(typed.values()) if whole else None
            if len(by_value) < 2 and not whole:
                # A sample from the start of a log can hold one kind only (the first
                # 500 attachments of a session are all hook results), so the values
                # are listed and each is sampled on its own.
                spend()
                values = field_values(wrapper, archives_dir, path, within)
                if values is None or not 2 <= len(values) <= args.max_partition_values:
                    reject(path, out_of_range(values, args.max_partition_values))
                    continue
                predicates = {repr(v): value_predicate(path, v) for v in values}
                spend(sum(1 for pred in predicates.values() if pred))
                by_value = dict(zip(predicates, parallel(
                    lambda pred: record_sample(wrapper, archives_dir, f"{within} AND {pred}",
                                               VALUE_SAMPLE_RECORDS) if pred else [],
                    predicates.values())))
            if values is not None and not 2 <= len(values) <= args.max_partition_values:
                reject(path, out_of_range(values, args.max_partition_values))
                continue
            separation = shape_separation(by_value)
            if separation < MIN_KIND_SEPARATION:
                reject(path, f"its values do not change which fields a record carries"
                             f" (separation {separation:.2f}, below {MIN_KIND_SEPARATION})")
                continue
            if values is None:
                spend()
                values = field_values(wrapper, archives_dir, path, within)
                if values is None or not 2 <= len(values) <= args.max_partition_values:
                    reject(path, out_of_range(values, args.max_partition_values))
                    continue
            if whole:
                counted = {f"{within} AND {value_predicate(path, v)}": len(by_value[repr(v)])
                           for v in values if value_predicate(path, v)}
            else:
                terms = [f"{within} AND {pred}" for pred in
                         (value_predicate(path, v) for v in values) if pred]
                spend(len(terms))
                counted = dict(zip(terms, parallel(count_records, terms)))
            split = partition_by_value(path, values, fc,
                                       lambda kql: counted[f"{within} AND {kql}"])
            for kind in split["families"]:
                kind["family"] = family["predicate"]
                kind["field"] = path
                kind["predicate"] = f"{within} AND {kind['predicate']}"
                kind["path"] = kind["predicate"]
            result.update(field=path, separation=round(separation, 2),
                          kinds=split["families"], residual=split["residual"]["count"])
            return result
    except _OutOfQueries:
        result["reason"] = f"the query cap ({args.max_family_queries}) was reached first"
        return result
    result["reason"] = "no field it alone carries has values that are kinds of record"
    return result


# Searches run at once while splitting a family: its per-value samples and
# counts are independent, and one at a time they are most of the run's time.
KIND_WORKERS = 8


def parallel(fn, items):
    """fn over items on KIND_WORKERS threads, results in the items' order."""
    with ThreadPoolExecutor(KIND_WORKERS) as pool:
        return list(pool.map(fn, items))


def out_of_range(values, max_values):
    n = "no" if values is None else len(values)
    return f"{n} distinct values within the family, not 2 to {max_values}"


def value_at(record, path):
    """The value at a dotted path of a record, or None."""
    for key in path.split("."):
        if not isinstance(record, dict) or key not in record:
            return None
        record = record[key]
    return record


class _OutOfQueries(Exception):
    pass


def choose_partition_field(trees, wrapper, archives_dir, max_values, max_children,
                           min_coverage, probe_budget=DEFAULT_PARTITION_PROBE_BUDGET):
    """(root_records, chosen, reason, queries): the field whose values partition
    the records -- {"path", "values", "records"} -- or the reason there is none.

    Most records covered first, then shallowest, then fewest values: a field the
    whole archive carries wins over one that nearly does however few values the
    latter has, because what a field misses lands in the residual. Each candidate
    costs one --unique query, and there are rarely more than a handful, a field
    having to carry nearly every record to be a candidate at all.

    Two things bound that cost, because --unique is the most expensive query the
    engine runs: it scans and deduplicates every matching record, measured at
    ~22x a --count over the same data (165s against 7.4s on one 786 MB archive
    of a 216M-record capture).

    A field with one distinct value is not a candidate. Its values put every
    record in one family, which is the archive back again rather than a
    partition of it, and because the ranking prefers the fewest values such a
    field wins over every real candidate whenever it appears -- spending a probe
    on each of them and then returning the one answer that carries no
    information.

    And once some field has qualified, probing stops as soon as `probe_budget`
    seconds have gone on probes. Where probes are quick the budget never trips
    and every candidate is measured, so small archives rank exactly as they did;
    where each one costs minutes it buys a partition that works rather than the
    tidiest one.
    """
    root_records, candidates, closest = value_partition_fields(
        trees, max_children, min_coverage)
    if not candidates:
        near = ""
        if closest and root_records:
            near = (f" (the best is {closest['path']} at"
                    f" {100.0 * closest['records'] / root_records:.2f}%,"
                    f" {closest['records']} of {root_records})")
        return root_records, None, (
            f"no scalar field carries {100.0 * min_coverage:.2f}% of the records,"
            f" so no field's values can account for them{near}"), 0
    measured = []
    queries = 0
    single_valued = []
    started = time.monotonic()
    for candidate in candidates:
        # Candidates arrive best-coverage-first, then shallowest, so once one
        # qualifies nothing covering less (or covering the same from deeper) can
        # beat it, and its --unique query is not worth spending. Equal on both
        # still gets measured, since only the value count separates them.
        prefix = (-candidate["records"], candidate["depth"])
        if measured and prefix > (measured[0][0], measured[0][1]):
            break
        # Something already qualifies and the probes have cost more than the
        # budget: take it rather than spend another --unique breaking a tie.
        if measured and time.monotonic() - started >= probe_budget:
            break
        queries += 1
        values = field_values(wrapper, archives_dir, candidate["path"])
        if values is None:
            continue
        if len(values) == 1:
            single_valued.append(candidate["path"])
            continue
        if 2 <= len(values) <= max_values:
            measured.append((-candidate["records"], candidate["depth"], len(values),
                             len(candidate["path"]), candidate["path"], values,
                             candidate["records"]))
            measured.sort()
    if not measured:
        names = ", ".join(c["path"] for c in candidates[:5])
        why = (f"every scalar field covering the records has more than {max_values}"
               f" distinct values (tried {len(candidates)}: {names})")
        if single_valued:
            only = ", ".join(single_valued)
            carries = "carry" if len(single_valued) > 1 else "carries"
            rest = (f"; the rest have more than {max_values}"
                    if len(single_valued) < queries else "")
            why = (f"no scalar field covering the records splits them: {only}"
                   f" {carries} one distinct value, so every record would land in"
                   f" one family{rest}")
        return root_records, None, why, queries
    best = measured[0]
    return root_records, {"path": best[4], "values": best[5], "records": best[6]}, \
        None, queries


def print_record_families(trees, wrapper, archives_dir, families_file, args):
    count_records = record_counter(wrapper, archives_dir)
    # The value method is tried first whatever the choice: on --family-method
    # existence its numbers are reported beside the partition, so that the
    # coarser answer is never read without the exact one next to it.
    try:
        root_records, chosen, no_value_field, field_queries = choose_partition_field(
            trees, wrapper, archives_dir, args.max_partition_values, args.max_children,
            args.min_partition_coverage, args.partition_probe_budget,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if chosen is None and args.family_method == "value":
        print(f"error: --family-method value: {no_value_field}", file=sys.stderr)
        return 1
    method = "value" if chosen is not None and args.family_method != "existence" else "existence"

    candidates = []
    try:
        if method == "value":
            result = partition_by_value(chosen["path"], chosen["values"], root_records,
                                        count_records, args.max_family_queries)
            result["queries"] += field_queries
        else:
            root_records, candidates = family_candidates(trees)
            if root_records <= 0:
                print("error: the schema tree has no record root to partition",
                      file=sys.stderr)
                return 1
            result = partition_families(
                root_records, candidates, count_records,
                args.max_family_queries, args.max_discriminator_leaves,
            )
    except (RuntimeError, ValueError) as exc:
        # A partition nobody could verify is worse than no partition, so this
        # reports rather than printing families it cannot stand behind.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    families, residual = result["families"], result["residual"]

    # The invariant this mode exists for, checked on the counts before a single
    # share is printed: they are integers from the archive's own count
    # aggregation, and the families were accepted only after counting 0 records
    # in common, so a mismatch means the partition is not one.
    covered = sum(f["count"] for f in families) + residual["count"]
    if covered != root_records:
        print(f"error: the families and the residual cover {covered} records, not the"
              f" root's {root_records}: this is not a partition", file=sys.stderr)
        return 1
    shares = display_shares([f["count"] for f in families] + [residual["count"]],
                            root_records)
    share_sum = sum(shares)
    if abs(share_sum - 100.0) > 0.05:
        print(f"error: the shares sum to {share_sum:.1f}%, not 100%", file=sys.stderr)
        return 1

    if method == "value":
        # Coverage on the line whatever it is: a partition of 99.97% of the
        # records must never read as one of all of them, and 100.00% is worth
        # saying rather than leaving to be inferred from a missing key.
        coverage = 100.0 * chosen["records"] / root_records if root_records else 0.0
        print(f"RECORD_FAMILY_METHOD=value field={chosen['path']}"
              f" values={len(chosen['values'])} coverage={coverage:.2f}%"
              f" uncovered={root_records - chosen['records']}")
    else:
        reason = ("asked for with --family-method existence" if args.family_method
                  == "existence" else no_value_field)
        print(f"RECORD_FAMILY_METHOD=existence reason={reason}")
    print(f"RECORD_FAMILY_ROOT records={root_records}")
    print(f"RECORD_FAMILY_COUNT={len(families)}")
    print(f"RECORD_FAMILY_QUERIES={result['queries']}")
    print(f"RECORD_FAMILY_CANDIDATES="
          f"{len(chosen['values']) if method == 'value' else len(candidates)}"
          f" tested={result['tested']} untested={result['untested']}"
          f" cap={'hit' if result['cap_hit'] else 'no'}")
    if result["cap_hit"]:
        print(f"RECORD_FAMILY_CAP_NOTE=the cap stopped the search with"
              f" {result['untested']} candidates untested; the residual is an upper"
              " bound and real families may be inside it. Raise"
              " --max-family-queries to finish the search")
    for family, share in zip(families, shares):
        extra = ""
        if family["co_equal"]:
            extra += " also=" + ",".join(family["co_equal"])
        if family["kind"] == "structural-disjunction":
            extra += f" leaves={len(family['leaves'])}"
        # A value can hold a space, so for those the name goes last on the line
        # and runs to its end, the way a reason= does.
        if family["kind"] == "value":
            print(f"RECORD_FAMILY count={family['count']} share={share:.1f}%"
                  f" kind=value name={family['path']}")
        else:
            print(f"RECORD_FAMILY name={family['path']} count={family['count']}"
                  f" share={share:.1f}% kind={family['kind']}{extra}")
    print(f"RECORD_FAMILY_RESIDUAL count={residual['count']} share={shares[-1]:.1f}%")
    print(f"RECORD_FAMILY_SHARE_SUM={share_sum:.1f}%")

    # Asked for the coarse method while the exact one applies: show what it
    # costs, so the approximation is never read on its own.
    if method == "existence" and chosen is not None:
        try:
            exact = partition_by_value(chosen["path"], chosen["values"], root_records,
                                       count_records, args.max_family_queries)
            print(f"RECORD_FAMILY_COMPARE method=value field={chosen['path']}"
                  f" families={len(exact['families'])}"
                  f" residual={exact['residual']['count']}"
                  f" queries={exact['queries'] + field_queries}"
                  f" against existence families={len(families)}"
                  f" residual={residual['count']} queries={result['queries']}")
        except (RuntimeError, ValueError) as exc:
            print(f"RECORD_FAMILY_COMPARE=unavailable reason={exc}")

    for row in result["discarded"]:
        also = ",".join(row["co_equal"])
        print(f"RECORD_FAMILY_DISCARDED name={row['path']} count={row['count']}"
              + (f" also={also}" if also else "") + f" reason={row['reason']}")
    for row in result["undiscriminated"]:
        print(f"RECORD_FAMILY_UNDISCRIMINATED path={row['path']}"
              f" records={row['records']} reason={row['reason']}")

    kind_rows = []
    if args.kinds:
        kind_rows = print_record_kinds(
            families, residual, root_records, trees, wrapper, archives_dir, count_records,
            {chosen["path"]} if method == "value" else set(), args,
            args.max_family_queries - result["queries"])
        if kind_rows is None:
            return 1

    if families_file:
        rows = [dict(row="family", **f) for f in families]
        rows.append(dict(row="residual", **residual))
        rows += [dict(row="discarded", **r) for r in result["discarded"]]
        rows += [dict(row="undiscriminated", **r) for r in result["undiscriminated"]]
        rows += kind_rows
        write_ndjson(families_file, rows)
    return 0


def print_record_kinds(families, residual, root_records, trees, wrapper, archives_dir,
                       count_records, exclude, args, max_queries):
    """Split each family into kinds where a field of its own allows it, print
    the result, and return its NDJSON rows -- or None when the kinds fail to
    account for every record, which is reported rather than printed.

    A family no field splits is one kind, itself, so the kinds are still a
    partition of the records: the kinds of the families that split, the
    families that did not, and one residual holding what the families missed
    plus what each split missed within its family.
    """
    kinds, splits = [], []
    candidates = [kind_field_candidates(trees, f["count"], exclude, args.max_children,
                                        args.min_partition_coverage) for f in families]
    # One sample per family with a candidate, fetched together: each is one
    # search, and most families need nothing more.
    sampled = [f for f, c in zip(families, candidates) if c][:max(0, max_queries)]
    spent = len(sampled)
    samples = dict(zip((f["predicate"] for f in sampled), parallel(
        lambda f: record_sample(wrapper, archives_dir, grouped(f["predicate"]),
                                FAMILY_SAMPLE_RECORDS), sampled)))
    for family, family_candidates in zip(families, candidates):
        if family["predicate"] not in samples:
            reason = ("no field is carried by this family alone" if not family_candidates
                      else f"the query cap ({args.max_family_queries}) was reached first")
            split = {"family": family["predicate"], "count": family["count"], "field": None,
                     "kinds": [], "residual": 0, "queries": 0, "rejected": [],
                     "reason": reason}
        else:
            split = split_into_kinds(family, family_candidates, samples[family["predicate"]],
                                     wrapper, archives_dir, count_records, args,
                                     max(0, max_queries - spent))
            spent += split["queries"]
            split["queries"] += 1  # its sample
        splits.append(split)
        if split["field"]:
            kinds += split["kinds"]
        else:
            kinds.append(dict(family, family=family["predicate"], field=None, value=None))
    kind_residual = residual["count"] + sum(s["residual"] for s in splits)
    covered = sum(k["count"] for k in kinds) + kind_residual
    if covered != root_records:
        print(f"error: the kinds and the residual cover {covered} records, not the"
              f" root's {root_records}: this is not a partition", file=sys.stderr)
        return None
    kinds.sort(key=lambda k: (-k["count"], k["predicate"]))
    shares = display_shares([k["count"] for k in kinds] + [kind_residual], root_records)

    print(f"RECORD_KIND_COUNT={len(kinds)}")
    print(f"RECORD_KIND_QUERIES={spent}")
    # The family goes last on these lines, as a value family's name does above:
    # its value can hold a space.
    for split in splits:
        if split["field"]:
            print(f"RECORD_KIND_SPLIT field={split['field']} kinds={len(split['kinds'])}"
                  f" residual={split['residual']} separation={split['separation']:.2f}"
                  f" family={split['family']}")
        else:
            print(f"RECORD_KIND_WHOLE count={split['count']} family={split['family']}")
    for kind, share in zip(kinds, shares):
        print(f"RECORD_KIND count={kind['count']} share={share:.1f}% name={kind['predicate']}")
    print(f"RECORD_KIND_RESIDUAL count={kind_residual} share={shares[-1]:.1f}%")

    rows = [dict(row="kind", family=k["family"], field=k["field"], value=k.get("value"),
                 predicate=k["predicate"], count=k["count"],
                 share=round(k["count"] / root_records, 6) if root_records else 0.0)
            for k in kinds]
    rows.append(dict(row="kind_residual", count=kind_residual,
                     share=round(kind_residual / root_records, 6) if root_records else 0.0))
    for split in splits:
        rows.append(dict(
            row="kind_split" if split["field"] else "kind_whole", family=split["family"],
            count=split["count"], field=split["field"], kinds=len(split["kinds"]),
            residual=split["residual"], separation=split.get("separation"),
            reason=split["reason"], rejected=split["rejected"], queries=split["queries"]))
    return rows


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
    parser.add_argument("--record-families", action="store_true")
    parser.add_argument("--record-families-file")
    parser.add_argument("--max-family-queries", type=int,
                        default=DEFAULT_MAX_FAMILY_QUERIES)
    parser.add_argument("--max-discriminator-leaves", type=int,
                        default=DEFAULT_MAX_DISCRIMINATOR_LEAVES)
    parser.add_argument("--family-method", choices=("auto", "value", "existence"),
                        default="auto")
    parser.add_argument("--max-partition-values", type=int,
                        default=DEFAULT_MAX_PARTITION_VALUES)
    parser.add_argument("--min-partition-coverage", type=float,
                        default=DEFAULT_MIN_PARTITION_COVERAGE)
    parser.add_argument("--partition-probe-budget", type=float,
                        default=DEFAULT_PARTITION_PROBE_BUDGET)
    parser.add_argument("--search-wrapper")
    parser.add_argument("--kinds", action="store_true")
    args = parser.parse_args()
    # One path, not an argv: a caller replacing the search with its own program gives one
    # executable. The default is this plugin's own `clp search`, which is two words.
    args.search_wrapper = [args.search_wrapper] if args.search_wrapper else C.search_argv()
    drift = args.drift or bool(args.drift_file)
    counts_mode = args.field_counts or bool(args.field_counts_file)
    families = args.record_families or bool(args.record_families_file) or args.kinds
    if families and args.max_family_queries < 1:
        print("error: --max-family-queries must be at least 1", file=sys.stderr)
        return 2
    if families and args.max_discriminator_leaves < 1:
        print("error: --max-discriminator-leaves must be at least 1", file=sys.stderr)
        return 2
    if families and args.max_partition_values < 1:
        print("error: --max-partition-values must be at least 1", file=sys.stderr)
        return 2
    if families and not 0 < args.min_partition_coverage <= 1:
        print("error: --min-partition-coverage must be in (0, 1]", file=sys.stderr)
        return 2

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
    if families:
        rc = print_record_families(
            trees, args.search_wrapper, args.archives_dir,
            args.record_families_file, args,
        )
        if rc:
            return rc
    if drift or counts_mode or families:
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
