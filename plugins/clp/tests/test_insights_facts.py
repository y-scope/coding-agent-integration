"""Tests for lib/insights_facts.py's duplicate-input guards.

Run: python3 -m unittest discover -s plugins/clp/tests

The bug these cover: every value's count is summed once per result entry, so the
same entry arriving from both results files is added twice, and the severity split
then sums past the archive's record count. The old code explained that excess by
declaring the field multi-valued -- a confident, plausible, wrong finding, which
the report writer would then have quoted as established fact.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin")
FACTS = [os.path.join(BIN, "clp-insights"), "facts"]
SCHEMA = '{"severity":"severity","message":"msg"}'
TOTAL = 100


def count_entry(index, value, count, negated=False, kql=None):
    """One `count` result entry for severity=value, as the query pool records it."""
    match = ({"not": {"field": "severity", "eq": value}} if negated
             else {"field": "severity", "eq": value})
    return {"index": index, "label": f"Baseline: severity={value}", "method": "count",
            "origin": "baseline", "status": "ok", "total_records": TOTAL,
            "match": match, "kql": kql or f'severity:"{value}"', "count": count}


# 80 + 15 + a residual of 5 = the 100 records, exactly.
SEVERITY_SPLIT = [
    count_entry(1, "INFO", 80),
    count_entry(2, "WARN", 15),
    count_entry(3, "INFO", 5, negated=True, kql='NOT (severity:"INFO" OR severity:"WARN")'),
]
# A plan entry that shares nothing with the baseline's.
PLAN_ENTRY = [{"index": 1, "label": "plan", "method": "count", "origin": "classified",
               "status": "ok", "total_records": TOTAL, "match": {"field": "msg", "contains": "x"},
               "kql": 'msg:"*x*"', "count": 7}]


class FactsDuplicateInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "facts.md")

    def write(self, name, entries):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def run_facts(self, baseline, results):
        return subprocess.run(
            FACTS + ["--schema-json", SCHEMA, "--archive-dir", self.tmp.name,
                     "--baseline-results-file", baseline, "--results-file", results,
                     "--category-totals", "none", "--freqs-file", "none", "--out", self.out],
            capture_output=True, text=True)

    def facts_text(self):
        with open(self.out, "r", encoding="utf-8") as f:
            return f.read()

    def test_same_file_for_both_pools_is_refused_and_writes_nothing(self):
        """One file passed twice is never intentional, and a facts file that exists
        is quoted downstream as trustworthy -- so none is written."""
        path = self.write("baseline.ndjson", SEVERITY_SPLIT)
        p = self.run_facts(path, path)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--baseline-results-file", p.stderr)
        self.assertIn("--results-file", p.stderr)
        self.assertFalse(os.path.exists(self.out), "a facts file was written anyway")

    def test_same_file_spelled_differently_is_still_refused(self):
        """The check is on the resolved path, not the string."""
        path = self.write("baseline.ndjson", SEVERITY_SPLIT)
        odd = os.path.join(self.tmp.name, ".", "baseline.ndjson")
        self.assertNotEqual(path, odd)
        self.assertNotEqual(self.run_facts(path, odd).returncode, 0)

    def test_distinct_files_that_share_entries_report_the_overlap(self):
        """Two real files can still hold the same entry. The counts must come out
        right and the overlap must be named -- without blaming the data."""
        baseline = self.write("baseline.ndjson", SEVERITY_SPLIT)
        overlapping = self.write("plan.ndjson", SEVERITY_SPLIT)
        p = self.run_facts(baseline, overlapping)
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.facts_text()
        self.assertIn("the two results files overlap: 3 entries", text)
        self.assertIn("Pass distinct --baseline-results-file and --results-file.", text)
        # Each entry counted once, so the split still adds up exactly.
        self.assertIn("- check: the lines above sum to all 100 records", text)
        self.assertIn("- INFO: 80 ", text)
        self.assertNotIn("sum to 200", text)
        self.assertNotIn("multi-valued", text)

    def test_a_genuinely_multi_valued_field_is_still_reported(self):
        """Two distinct queries whose counts legitimately exceed the record total:
        the finding is real here, and the fix must not have deleted the check."""
        baseline = self.write("baseline.ndjson",
                              [count_entry(1, "INFO", 80), count_entry(2, "WARN", 40)])
        plan = self.write("plan.ndjson", PLAN_ENTRY)
        p = self.run_facts(baseline, plan)
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.facts_text()
        self.assertIn("more than the 100 records, so `severity` is multi-valued", text)
        self.assertNotIn("results files overlap", text)

    def test_overlap_does_not_hide_a_real_excess(self):
        """Overlapping inputs and a genuinely multi-valued field at once. Because
        every entry is counted once, an excess that remains is the data's, so the
        finding must still be reported -- with the overlap ruled out as its cause."""
        multi = [count_entry(1, "INFO", 80), count_entry(2, "WARN", 40)]
        baseline = self.write("baseline.ndjson", multi)
        overlapping = self.write("plan.ndjson", multi)
        p = self.run_facts(baseline, overlapping)
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.facts_text()
        self.assertIn("results files overlap: 2 entries", text)
        # 120, not 240: each entry counted once despite arriving twice.
        self.assertIn("sum to 120, more than the 100 records, so `severity` is multi-valued", text)
        self.assertIn("do not explain this excess", text)

    def test_the_correct_pair_is_unaffected(self):
        baseline = self.write("baseline.ndjson", SEVERITY_SPLIT)
        plan = self.write("plan.ndjson", PLAN_ENTRY)
        p = self.run_facts(baseline, plan)
        self.assertEqual(p.returncode, 0, p.stderr)
        text = self.facts_text()
        self.assertIn("- check: the lines above sum to all 100 records", text)
        self.assertNotIn("results files overlap", text)
        self.assertNotIn("multi-valued", text)

    def test_entries_without_kql_are_never_treated_as_duplicates(self):
        """An entry that failed before rendering has no identity; dropping one as a
        duplicate would lose a real count."""
        no_kql = dict(count_entry(4, "ERROR", 3), kql=None)
        baseline = self.write("baseline.ndjson", SEVERITY_SPLIT[:2] + [no_kql, dict(no_kql, index=5)])
        plan = self.write("plan.ndjson", PLAN_ENTRY)
        p = self.run_facts(baseline, plan)
        self.assertEqual(p.returncode, 0, p.stderr)
        # 80 + 15 + 3 + 3, both ERROR entries kept.
        self.assertIn("sum to 101", self.facts_text())


if __name__ == "__main__":
    unittest.main()
