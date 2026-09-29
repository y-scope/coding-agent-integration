"""clp schema --kinds: each record family split once more, by a field whose values are kinds of record.

A stub stands in for `clp search`: it answers --count, --unique and --limit from a list of records
the test writes, and logs every query, so these tests check the split and what it spent. The schema
tree is built from the same records.

The fixture has one family of each case the split has to tell apart:
- event: 600 records, a `kind` field whose two values carry different fields, and a `call_id`
  that is different on every record and is tried first. The first 500 are all one kind, so the family sample shows a
  single value of `kind` and the values have to be listed and sampled one by one.
- audit: 30 records, split by `action` into two kinds with fields of their own. The family sample
  holds all of them, so the split costs no query beyond the sample.
- metric: 50 records whose `name` has two values and the same fields either way: data, not kinds.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
SCHEMA = [sys.executable, os.path.join(BIN, "lib", "schema_tree_cli.py")]

RECORDS = (
    [{"type": "event", "kind": "start", "call_id": f"r{i}", "pid": i} for i in range(500)]
    + [{"type": "event", "kind": "stop", "call_id": f"r{500 + i}", "exit_code": 0}
       for i in range(100)]
    + [{"type": "audit", "action": "login", "user": f"u{i}"} for i in range(15)]
    + [{"type": "audit", "action": "delete", "path": f"/p{i}"} for i in range(15)]
    + [{"type": "metric", "name": "cpu" if i % 2 else "mem", "value": i} for i in range(50)]
)

TYPE_IDS = {bool: 4, int: 0, str: 3}

# `clp search` over RECORDS_FILE, for the three query forms the split uses: `*`, and terms joined
# by AND, each `path:*`, `path:"string"` or `path:number`.
STUB = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["STUB_LOG"], "a") as log:
        log.write(" ".join(args) + "\\n")
    records = [json.loads(l) for l in open(os.environ["RECORDS_FILE"])]
    def matches(record, kql):
        if kql == "*":
            return True
        for term in kql.split(" AND "):
            path, want = term.split(":", 1)
            if path not in record:
                return False
            if want == "*":
                continue
            have = record[path]
            if want.startswith('"'):
                if have != json.loads(want):
                    return False
            elif have != json.loads(want):
                return False
        return True
    mode = args[0]
    if mode == "--count":
        kql = args[2]
        print(json.dumps({"count": sum(1 for r in records if matches(r, kql))}))
    elif mode == "--unique":
        field, kql = args[1], args[3]
        seen = []
        for r in records:
            if matches(r, kql) and field in r and r[field] not in seen:
                seen.append(r[field])
        for v in seen:
            print(json.dumps({"field": field, "value": v}))
    elif mode == "--limit":
        limit, kql = int(args[1]), args[3]
        for r in [r for r in records if matches(r, kql)][:limit]:
            print(json.dumps(r))
""")


def schema_tree(records):
    """A one-archive stats.schema_tree for flat records: a root object and one node per field and
    type, each counting the records that carry it."""
    counts = {}
    for record in records:
        for key, value in record.items():
            node = (key, TYPE_IDS[type(value)])
            counts[node] = counts.get(node, 0) + 1
    nodes = [{"children": list(range(1, len(counts) + 1)), "count": len(records), "id": 0,
              "key": "", "parent_id": -1, "type": 5}]
    for i, ((key, type_id), count) in enumerate(sorted(counts.items()), start=1):
        nodes.append({"children": [], "count": count, "id": i, "key": key, "parent_id": 0,
                      "type": type_id})
    return {"archive_id": "a", "nodes": nodes}


class RecordKinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = cls.tmp.name
        records_file = os.path.join(tmp, "records.ndjson")
        with open(records_file, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in RECORDS))
        tree_file = os.path.join(tmp, "tree.ndjson")
        with open(tree_file, "w") as fh:
            fh.write(json.dumps(schema_tree(RECORDS)) + "\n")
        stub = os.path.join(tmp, "stub-search")
        with open(stub, "w") as fh:
            fh.write(STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        cls.log = os.path.join(tmp, "queries.log")
        families_file = os.path.join(tmp, "families.ndjson")
        env = dict(os.environ, STUB_LOG=cls.log, RECORDS_FILE=records_file)
        cls.proc = subprocess.run(
            [*SCHEMA, "--tree-file", tree_file, "--search-wrapper", stub, "--kinds",
             "--record-families-file", families_file, tmp],
            capture_output=True, text=True, env=env)
        with open(families_file) as fh:
            cls.rows = [json.loads(line) for line in fh]
        with open(cls.log) as fh:
            cls.queries = fh.read().splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def splits(self):
        return {r["family"]: r for r in self.rows if r["row"] in ("kind_split", "kind_whole")}

    def kinds(self):
        return {r["predicate"]: r["count"] for r in self.rows if r["row"] == "kind"}

    def test_the_kinds_are_a_partition_of_every_record(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertIn("RECORD_KIND_COUNT=5", self.proc.stdout)
        self.assertIn("RECORD_KIND_RESIDUAL count=0 share=0.0%", self.proc.stdout)
        self.assertEqual(sum(self.kinds().values()), len(RECORDS))

    def test_a_field_whose_values_carry_their_own_fields_splits_its_family(self):
        kinds = self.kinds()
        self.assertEqual(kinds['type:"event" AND kind:"start"'], 500)
        self.assertEqual(kinds['type:"event" AND kind:"stop"'], 100)
        self.assertEqual(kinds['type:"audit" AND action:"login"'], 15)
        self.assertEqual(kinds['type:"audit" AND action:"delete"'], 15)

    def test_an_identifier_is_refused_on_the_sample_without_a_query_of_its_own(self):
        event = self.splits()['type:"event"']
        refused = {r["field"]: r["reason"] for r in event["rejected"]}
        self.assertIn("do not change which fields", refused["call_id"])
        self.assertFalse(any("--unique call_id" in q for q in self.queries))

    def test_values_that_look_alike_are_data_and_the_family_stays_whole(self):
        metric = self.splits()['type:"metric"']
        self.assertEqual(metric["row"], "kind_whole")
        self.assertEqual(self.kinds()['type:"metric"'], 50)
        self.assertIn("RECORD_KIND_WHOLE count=50 family=type:\"metric\"", self.proc.stdout)

    def test_a_family_the_sample_holds_whole_costs_only_its_sample(self):
        splits = self.splits()
        self.assertEqual(splits['type:"audit"']["queries"], 1)
        self.assertEqual(splits['type:"metric"']["queries"], 1)
        self.assertFalse(any('type:"audit" AND' in q for q in self.queries))

    def test_a_sample_showing_one_value_falls_back_to_sampling_each_value(self):
        self.assertTrue(any(q.startswith("--unique kind ") for q in self.queries))
        self.assertTrue(any('--limit' in q and 'kind:"stop"' in q for q in self.queries))


if __name__ == "__main__":
    unittest.main()
