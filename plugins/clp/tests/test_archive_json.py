"""clp: the archive JSON documents, the time range reduction, and compress status.

These replaced jq pipelines in the shell wrappers, so the behaviour they must keep
is jq's: the same keys, the same null-vs-empty rules, and the same floor division
in the progress percentage.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, os.path.join(BIN, "lib"))
import archive_json as A  # noqa: E402
import compress_status as S  # noqa: E402


def run(module, argv, stdin=None):
    env = dict(os.environ, PYTHONPATH=os.path.join(BIN, "lib"))
    return subprocess.run([sys.executable, os.path.join(BIN, "lib", module), *argv],
                          capture_output=True, text=True, input=stdin, env=env)


class TimeRange(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.stats = os.path.join(self.dir.name, "stats.json")

    def write(self, *records):
        with open(self.stats, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def test_the_earliest_begin_and_latest_end_win(self):
        self.write({"begin_timestamp": 2000, "end_timestamp": 3000},
                   {"begin_timestamp": 1000, "end_timestamp": 4000})
        result = run("archive_json.py", ["time-range", "--stats", self.stats])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout),
                         {"beginMs": 1000, "endMs": 4000,
                          "begin": "1970-01-01T00:00:01Z", "end": "1970-01-01T00:00:04Z"})

    def test_an_archive_that_saw_no_timestamp_is_left_out(self):
        self.write({"begin_timestamp": 0, "end_timestamp": 0},
                   {"begin_timestamp": 5000, "end_timestamp": 6000})
        result = run("archive_json.py", ["time-range", "--stats", self.stats])
        self.assertEqual(json.loads(result.stdout)["beginMs"], 5000)

    def test_no_timestamp_anywhere_is_null(self):
        self.write({"begin_timestamp": 0, "end_timestamp": 0})
        result = run("archive_json.py", ["time-range", "--stats", self.stats])
        self.assertEqual(result.stdout.strip(), "null")

    def test_a_stats_file_that_is_not_json_fails(self):
        with open(self.stats, "w", encoding="utf-8") as handle:
            handle.write("clp-s: not json\n")
        result = run("archive_json.py", ["time-range", "--stats", self.stats])
        self.assertEqual(result.returncode, 1)
        self.assertIn("error:", result.stderr)

    def test_the_line_says_none_when_there_is_no_range(self):
        result = run("archive_json.py",
                     ["time-range-text", "--key", "t.$date", "--range", "null"])
        self.assertEqual(result.stdout.strip(),
                         "Time range: none (no record has the timestamp key t.$date)")

    def test_the_line_prints_both_ends(self):
        result = run("archive_json.py", ["time-range-text", "--key", "ts", "--range",
                                         '{"begin": "2026-01-01T00:00:00Z",'
                                         ' "end": "2026-01-02T00:00:00Z"}'])
        self.assertEqual(result.stdout.strip(),
                         "Time range: 2026-01-01T00:00:00Z to 2026-01-02T00:00:00Z")


class FolderMetadata(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = os.path.join(self.dir.name, ".yscope-clp-archive.json")

    def write(self, *extra):
        result = run("archive_json.py", [
            "folder-metadata", "--out", self.out, "--created-at", "2026-09-26T00:00:00Z",
            "--source-type", "folder", "--source-path", "/logs", "--source-name", "logs",
            "--source-file-count", "2", "--archive-dir", "/arch", "--time-range", "null",
            "--input-bytes", "100", "--archive-bytes", "10", "--reduction-bytes", "90",
            "--compression-ratio", "10.00x", "--reduction-percent", "90.00%",
            "--path", "/logs/a.log", *extra])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.out, encoding="utf-8") as handle:
            return json.load(handle)

    def test_a_path_list_input_has_no_recursive_or_extensions(self):
        document = self.write()
        self.assertIsNone(document["source"]["recursive"])
        self.assertIsNone(document["source"]["extensions"])
        self.assertFalse(document["structurize"])
        self.assertEqual(document["schemaVersion"], 1)
        self.assertEqual(document["source"]["paths"], ["/logs/a.log"])

    def test_the_wildcard_filter_is_recorded_as_the_string(self):
        document = self.write("--source-recursive", "1", "--source-extensions-all")
        self.assertEqual(document["source"]["extensions"], "*")
        self.assertEqual(document["source"]["recursive"], 1)

    def test_a_named_filter_is_a_list(self):
        document = self.write("--source-recursive", "0",
                              "--source-extension", "log", "--source-extension", "txt")
        self.assertEqual(document["source"]["extensions"], ["log", "txt"])
        self.assertEqual(document["source"]["recursive"], 0)

    def test_empty_strings_become_null(self):
        document = self.write()
        self.assertIsNone(document["archiveRoot"])
        self.assertIsNone(document["clpArchiveDir"])
        self.assertIsNone(document["timestampKey"])
        self.assertIsNone(document["parser"])


class CompressStatus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        # `compress status` is given the archive directory and derives the status
        # file's name from it.
        self.archive_dir = os.path.join(self.dir.name, "archive")
        self.status = self.archive_dir + ".compress-status.json"

    def write(self, **over):
        status = {"state": "running", "pid": os.getpid(), "startedAt": 1000, "updatedAt": 1000,
                  "inputBytes": 1000, "readBytes": 250, "archiveBytes": 90, "exitCode": None}
        status.update(over)
        with open(self.status, "w", encoding="utf-8") as handle:
            json.dump(status, handle)
        return status

    def keys(self, result):
        return dict(line.split("=", 1) for line in result.stdout.strip().splitlines())

    def test_a_live_pid_is_running(self):
        self.write()
        result = run("compress_status.py", [self.archive_dir])
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.keys(result)["STATE"], "running")

    def test_a_dead_pid_died_rather_than_running(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.write(pid=dead.pid)
        result = run("compress_status.py", [self.archive_dir])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.keys(result)["STATE"], "died")

    def test_done_prints_the_progress_and_exits_zero(self):
        self.write(state="done", exitCode=0)
        result = run("compress_status.py", [self.archive_dir])
        self.assertEqual(result.returncode, 0)
        keys = self.keys(result)
        self.assertEqual(keys["PROGRESS_PCT"], "25")
        self.assertEqual(keys["EXIT_CODE"], "0")
        self.assertEqual(keys["ELAPSED_S"], "0")

    def test_a_failed_run_exits_one_and_reports_its_code(self):
        self.write(state="failed", updatedAt=1005, exitCode=137)
        result = run("compress_status.py", [self.archive_dir])
        self.assertEqual(result.returncode, 1)
        keys = self.keys(result)
        self.assertEqual(keys["EXIT_CODE"], "137")
        self.assertEqual(keys["ELAPSED_S"], "5")

    def test_no_input_bytes_does_not_divide(self):
        self.write(inputBytes=0, readBytes=0)
        result = run("compress_status.py", [self.archive_dir])
        self.assertEqual(self.keys(result)["PROGRESS_PCT"], "0")

    def test_a_missing_status_file_is_usage(self):
        result = run("compress_status.py", [os.path.join(self.dir.name, "nothing")])
        self.assertEqual(result.returncode, 2)
        self.assertIn("error: no status file", result.stderr)

    def test_no_argument_is_usage(self):
        result = run("compress_status.py", [])
        self.assertEqual(result.returncode, 2)


class MetadataSummary(unittest.TestCase):
    def test_the_three_lines_name_the_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".yscope-clp-archive.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"agent": "claude", "session": {"file": "/s.jsonl"},
                           "sourceRoot": "/root"}, handle)
            result = run("archive_json.py", ["metadata-summary", "--file", path])
        self.assertEqual(result.stdout.strip().splitlines(),
                         ["Archive source agent: claude",
                          "Archive source session: /s.jsonl",
                          "Archive source root: /root"])

    def test_missing_fields_read_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".yscope-clp-archive.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({}, handle)
            result = run("archive_json.py", ["metadata-summary", "--file", path])
        self.assertIn("Archive source agent: unknown", result.stdout)


class SessionMetadata(unittest.TestCase):
    def test_the_document_keeps_its_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "metadata.json")
            result = run("archive_json.py", [
                "session-metadata", "--out", out, "--created-at", "2026-09-26T00:00:00Z",
                "--agent", "claude", "--source-root", "/root", "--archive-dir", "/arch",
                "--timestamp-key", "timestamp", "--time-range", "null",
                "--selection-file", "/sel.tsv", "--session-index", "3",
                "--session-file", "/s.jsonl", "--session-id", "abc", "--session-sha256", "dead",
                "--input-bytes", "50", "--archive-bytes", "5", "--reduction-bytes", "45",
                "--compression-ratio", "10.00x", "--reduction-percent", "90.00%",
                "--command", "clp", "--command", "compress"])
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(out, encoding="utf-8") as handle:
                document = json.load(handle)
        self.assertEqual(document["session"], {"file": "/s.jsonl", "id": "abc",
                                               "sha256": "dead", "bytes": 50})
        self.assertEqual(document["selection"], {"file": "/sel.tsv", "index": 3})
        self.assertEqual(document["command"], ["clp", "compress"])
        self.assertIsNone(document["archiveRoot"])


if __name__ == "__main__":
    unittest.main()
