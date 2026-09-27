"""clp bootstrap: the four readings it makes of the archive stats, tree and sample.

Each replaced a jq program, so the cases here pin the jq semantics that mattered:
missing keys count as zero, array and collapsed-key fields are not candidates for a
value distribution, and `// empty` treats both null and false as absent.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
HELPER = os.path.join(BIN, "lib", "bootstrap_json.py")


def run(*argv):
    return subprocess.run([sys.executable, HELPER, *argv], capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write_lines(self, name, *objects):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            for obj in objects:
                handle.write(obj if isinstance(obj, str) else json.dumps(obj))
                handle.write("\n")
        return path


class SumField(Fixture):
    def test_the_field_is_summed_across_the_archives(self):
        stats = self.write_lines("stats.json", {"num_log_shapes": 12}, {"num_log_shapes": 30})
        result = run("sum-field", "--file", stats, "--field", "num_log_shapes")
        self.assertEqual(result.stdout.strip(), "42")

    def test_a_line_without_the_field_counts_as_zero(self):
        stats = self.write_lines("stats.json", {"num_log_shapes": 5}, {"archive_id": 1})
        result = run("sum-field", "--file", stats, "--field", "num_log_shapes")
        self.assertEqual(result.stdout.strip(), "5")

    def test_no_archives_sums_to_zero(self):
        stats = self.write_lines("stats.json")
        result = run("sum-field", "--file", stats, "--field", "num_vars")
        self.assertEqual(result.stdout.strip(), "0")


class ArchiveLines(Fixture):
    def test_one_line_per_archive(self):
        stats = self.write_lines("stats.json",
                                 {"archive_id": 1, "num_log_shapes": 7, "num_vars": 3})
        result = run("archive-lines", "--file", stats)
        self.assertEqual(result.stdout.strip(),
                         "ARCHIVE_STAT archive_id=1 log_shapes=7 vars=3")


class ScalarFields(Fixture):
    def rows(self):
        return [
            {"path": "message", "display": "message", "type": "ClpString", "records": 100},
            {"path": "level", "display": "level", "type": "VarString", "records": 50},
            {"path": "attr.count", "display": "attr.count", "type": "Integer", "records": 90},
            {"path": "items", "display": "items[]", "type": "VarString", "records": 80},
            {"path": "blob", "display": "blob", "type": "VarString", "records": 70,
             "collapsed_keys": 2},
            {"path": "ok", "display": "ok", "type": "Boolean", "records": 10},
        ]

    def test_only_scalars_outside_arrays_are_candidates_in_record_order(self):
        path = os.path.join(self.dir.name, "tree.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.rows(), handle)
        result = run("scalar-fields", "--tree-file", path)
        self.assertEqual(result.stdout.split(), ["attr.count", "level", "ok"])

    def test_the_limit_is_honoured(self):
        path = os.path.join(self.dir.name, "tree.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.rows(), handle)
        result = run("scalar-fields", "--tree-file", path, "--limit", "1")
        self.assertEqual(result.stdout.split(), ["attr.count"])


class FieldValues(Fixture):
    def write_records(self, *records):
        return self.write_lines("sample.ndjson", *records)

    def test_a_dotted_path_is_followed(self):
        sample = self.write_records({"a": {"b": "first"}}, {"a": {"b": "second"}})
        result = run("field-values", "--record-file", sample, "--field", "a.b")
        self.assertEqual(result.stdout.split(), ["first", "second"])

    def test_a_record_without_the_path_is_skipped(self):
        sample = self.write_records({"a": {"b": "kept"}}, {"a": {}}, {"other": 1})
        result = run("field-values", "--record-file", sample, "--field", "a.b")
        self.assertEqual(result.stdout.split(), ["kept"])

    def test_null_and_false_are_both_absent(self):
        sample = self.write_records({"a": None}, {"a": False}, {"a": "real"})
        result = run("field-values", "--record-file", sample, "--field", "a")
        self.assertEqual(result.stdout.split(), ["real"])

    def test_zero_is_a_value(self):
        sample = self.write_records({"a": 0})
        result = run("field-values", "--record-file", sample, "--field", "a")
        self.assertEqual(result.stdout.strip(), "0")

    def test_non_json_lines_are_ignored(self):
        sample = self.write_records("clp-s: header line", {"a": "v"})
        result = run("field-values", "--record-file", sample, "--field", "a")
        self.assertEqual(result.stdout.strip(), "v")


if __name__ == "__main__":
    unittest.main()
