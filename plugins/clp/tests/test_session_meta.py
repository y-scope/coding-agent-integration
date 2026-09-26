"""clp list-sessions: reading a session header's harness, cwd and title.

These lines were written by the harness, not by us, so the cases that matter are
the awkward ones: a field that is absent, null, empty, or only present in a later
record. The scan is bounded, because a session file can be tens of megabytes.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
HELPER = os.path.join(BIN, "lib", "session_meta.py")


class SessionMeta(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "session.jsonl")

    def write(self, *records):
        with open(self.path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return self.path

    def run_helper(self, *argv):
        return subprocess.run([sys.executable, HELPER, "--file", self.path, *argv],
                              capture_output=True, text=True)

    def test_the_first_record_that_has_the_field_wins(self):
        self.write({"slug": "first"}, {"slug": "second"})
        result = self.run_helper("--field", "slug", "--non-empty")
        self.assertEqual(result.stdout.strip(), "first")

    def test_an_empty_string_is_skipped_when_non_empty_is_asked_for(self):
        self.write({"customTitle": ""}, {"customTitle": "Titled"})
        result = self.run_helper("--field", "customTitle", "--non-empty")
        self.assertEqual(result.stdout.strip(), "Titled")

    def test_an_empty_string_is_kept_without_the_flag(self):
        self.write({"customTitle": ""})
        result = self.run_helper("--field", "customTitle")
        self.assertEqual(result.stdout.strip(), "")

    def test_a_missing_field_prints_nothing_and_succeeds(self):
        self.write({"other": 1})
        result = self.run_helper("--field", "slug", "--non-empty")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_null_and_false_are_absent(self):
        self.write({"slug": None}, {"slug": False}, {"slug": "real"})
        result = self.run_helper("--field", "slug", "--non-empty")
        self.assertEqual(result.stdout.strip(), "real")

    def test_a_where_condition_picks_the_record_to_read(self):
        # Codex names the harness inside the session_meta record, not at the top.
        self.write({"type": "response_item", "payload": {"source": "wrong"}},
                   {"type": "session_meta", "payload": {"source": "codex_cli"}})
        result = self.run_helper("--field", "payload.source",
                                 "--where-path", "type", "--where-equals", "session_meta",
                                 "--non-empty")
        self.assertEqual(result.stdout.strip(), "codex_cli")

    def test_the_scan_stops_after_the_header(self):
        filler = [{"type": "response_item"} for _ in range(250)]
        self.write(*filler, {"slug": "too-late"})
        result = self.run_helper("--field", "slug", "--non-empty")
        self.assertEqual(result.stdout, "")

    def test_a_nested_path_that_is_partly_missing_is_absent(self):
        self.write({"payload": {}}, {"payload": {"cwd": "/w"}})
        result = self.run_helper("--field", "payload.cwd", "--non-empty")
        self.assertEqual(result.stdout.strip(), "/w")

    def test_non_json_lines_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write(json.dumps({"slug": "ok"}) + "\n")
        result = self.run_helper("--field", "slug", "--non-empty")
        self.assertEqual(result.stdout.strip(), "ok")

    def test_an_unreadable_file_is_not_an_error(self):
        self.path = os.path.join(self.dir.name, "absent.jsonl")
        result = self.run_helper("--field", "slug")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
