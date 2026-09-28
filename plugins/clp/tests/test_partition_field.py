"""clp schema --record-families: which field is allowed to partition the records.

A field carrying one distinct value puts every record in one family. That is the
archive restated, not a partition of it, and because the ranking prefers the
fewest values such a field beat every real candidate whenever it appeared -- on a
216M-record HDFS capture `labels.job` ("hdfs-nodes", the only value) won over
`labels.unit` (six services) and the stage reported one family after spending a
--unique probe on all four candidates.

A stub stands in for `clp search --unique` so these tests measure the choice and
the probes spent on it, not the engine.
"""

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, os.path.join(BIN, "lib"))
import schema_tree_cli as S  # noqa: E402
from schema_tree import load_trees  # noqa: E402

# Four sibling VarString fields under `labels`, every one on every record, which
# is the shape that ties the ranking and so probes each of them.
TREE = {
    "archive_id": "a",
    "nodes": [
        {"children": [1], "count": 1, "id": 0, "key": "", "parent_id": -1, "type": 10},
        {"children": [], "count": 1, "id": 1, "key": "log_event_idx", "parent_id": 0, "type": 11},
        {"children": [3], "count": 1000, "id": 2, "key": "", "parent_id": -1, "type": 5},
        {"children": [4, 5, 6, 7], "count": 1000, "id": 3, "key": "labels", "parent_id": 2, "type": 5},
        {"children": [], "count": 1000, "id": 4, "key": "host", "parent_id": 3, "type": 3},
        {"children": [], "count": 1000, "id": 5, "key": "job", "parent_id": 3, "type": 3},
        {"children": [], "count": 1000, "id": 6, "key": "service_name", "parent_id": 3, "type": 3},
        {"children": [], "count": 1000, "id": 7, "key": "unit", "parent_id": 3, "type": 3},
    ],
}

# `clp search --unique FIELD DIR QUERY`, answered from a table keyed by field.
STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    field="$2"
    case "$field" in
      labels.job)          vals=(hdfs-nodes) ;;
      labels.service_name) vals=(hdfs-nodes) ;;
      labels.unit)         vals=(namenode datanode regionserver loki journald useratsign) ;;
      labels.host)         vals=(n1 n2 n3 n4 n5 n6 n7 n8 n9 n10 n11 n12 n13 n14 n15 n16 n17 n18 n19 n20) ;;
      *)                   vals=() ;;
    esac
    for v in "${vals[@]}"; do
      printf '{"archive_id":"a","field":"%s","value":"%s"}\\n' "$field" "$v"
    done
""")

SINGLE_ONLY_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    printf '{"archive_id":"a","field":"%s","value":"only"}\\n' "$2"
""")


class PartitionFieldChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trees = load_trees([json.dumps(TREE)])

    def wrapper(self, body):
        path = os.path.join(self.tmp.name, "stub-search")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return [path]

    def choose(self, body=STUB, budget=S.DEFAULT_PARTITION_PROBE_BUDGET):
        return S.choose_partition_field(
            self.trees, self.wrapper(body), self.tmp.name,
            S.DEFAULT_MAX_PARTITION_VALUES, S.DEFAULT_MAX_CHILDREN,
            S.DEFAULT_MIN_PARTITION_COVERAGE, budget,
        )

    def test_single_valued_field_never_wins(self):
        """labels.job has one value, so it cannot be the partition however few."""
        _, chosen, reason, _ = self.choose()
        self.assertIsNotNone(chosen, reason)
        self.assertNotEqual(chosen["path"], "labels.job")
        self.assertNotEqual(chosen["path"], "labels.service_name")

    def test_fewest_values_still_wins_among_real_candidates(self):
        """Among fields that do split the records, the ranking is unchanged."""
        _, chosen, _, _ = self.choose()
        self.assertEqual(chosen["path"], "labels.unit")
        self.assertEqual(len(chosen["values"]), 6)

    def test_cheap_probes_still_measure_every_candidate(self):
        """The budget must not change ranking where probing costs nothing."""
        _, _, _, queries = self.choose()
        self.assertEqual(queries, 4)

    def test_budget_stops_probing_once_something_qualifies(self):
        """Spent budget takes the field in hand rather than tie-breaking on."""
        _, chosen, _, queries = self.choose(budget=0.0)
        self.assertEqual(queries, 2)  # labels.job rejected, labels.host qualifies
        self.assertEqual(chosen["path"], "labels.host")

    def test_budget_never_settles_for_a_single_valued_field(self):
        """A spent budget is not a reason to accept a one-family partition."""
        _, chosen, reason, _ = self.choose(body=SINGLE_ONLY_STUB, budget=0.0)
        self.assertIsNone(chosen)
        self.assertIn("one distinct value", reason)

    def test_reason_names_the_single_valued_fields(self):
        """The caller falls back to existence, so it has to say what failed."""
        _, chosen, reason, _ = self.choose(body=SINGLE_ONLY_STUB)
        self.assertIsNone(chosen)
        self.assertIn("labels.job", reason)
        self.assertIn("one family", reason)


if __name__ == "__main__":
    unittest.main()
