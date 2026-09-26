"""clp: which application the records name, which route follows, and what is already there.

The classification is exercised against real report text and real metadata files rather than against
a mocked classifier, because the whole point of the design is that the answer comes from what the
other commands print.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, os.path.join(BIN, "lib"))
import analyze as A  # noqa: E402

# One clp detect report per shape the classifier has to read: a Claude Code session log, a
# bundled vLLM text format, a JSON application it does not know, and a file that is not logs.
SESSION_REPORT = """Input: /h/projects/p/s.jsonl is a file
Read the first 128.0 KiB of 1 file(s), 92.9 MiB in all. Nothing was written.

== /h/projects/p/s.jsonl (92.9 MiB; read 128.0 KiB)
format:     json: 82 object(s) parsed, one object per line
fields:     type: string · sessionId: string (79 of 82) · parentUuid: null|string · isSidechain: \
boolean · attachment.type: string · uuid: string · timestamp: string · userType: string · \
cwd: string · version: string · gitBranch: string · (+52 more)
timestamp:  no field holds a timestamp in every record; time-range search won't work
records:    {"type": "last-prompt"}

SUMMARY files=1 json=1
SUGGEST clp compress folder --path /h/projects/p/s.jsonl
"""

VLLM_REPORT = """Input: /logs/w3.log is a file
Read the first 128.0 KiB of 1 file(s), 56.8 MiB in all. Nothing was written.

== /logs/w3.log (56.8 MiB; read 128.0 KiB, 406 line(s))
format:     text: 406 of 406 lines match the bundled vllm-sflow format; --structurize converts it \
to timestamp, logger, level, worker, message
lines:      2026-06-09 10:02:41,887 - sflow.task.vllm_worker_3 - INFO - 0: hello

SUMMARY files=1 text=1
SUGGEST clp compress folder --path /logs/w3.log --structurize
"""

COCKROACH_REPORT = """Input: /logs/n1.log is a file

== /logs/n1.log (9.8 GiB; read 128.0 KiB)
format:     json: 52 object(s) parsed, one object per line
fields:     channel: string · file: string · goroutine: number · tag: string · timestamp: string
timestamp:  "timestamp": epoch seconds as a string, e.g. "1679711330.570420890"

SUMMARY files=1 json=1
SUGGEST clp compress folder --path /logs/n1.log --timestamp-key timestamp
"""

BINARY_REPORT = """Input: /logs/blob.log is a file

== /logs/blob.log (20.0 KiB; read all)
format:     binary: NUL bytes in the first 8 KiB
skip:       binary data

SUMMARY files=1 binary=1
"""


class ReadingTheDetectorsReport(unittest.TestCase):
    def test_a_session_log_is_claude_code_from_its_root_fields(self):
        report = A.parse_detect(SESSION_REPORT)
        self.assertEqual(report["files"][0]["format"], "json")
        self.assertIn("sessionId", report["files"][0]["roots"])
        # Only the root of each path is a marker: attachment.type contributes "attachment".
        self.assertIn("attachment", report["files"][0]["roots"])
        app, why = A.app_of_detected(report)
        self.assertIs(app, A.CLAUDE_CODE)
        self.assertIn("sessionId, uuid", why)

    def test_the_suggested_command_is_carried_through_unchanged(self):
        report = A.parse_detect(VLLM_REPORT)
        self.assertEqual(report["suggest"], [["clp", "compress", "folder", "--path",
                                              "/logs/w3.log", "--structurize"]])
        argv = A.compress_folder_argv(report["suggest"][0], archives_root_value="/tmp/ar")
        self.assertEqual(argv[1:], ["compress", "folder", "--path", "/logs/w3.log",
                                    "--structurize", "--archives-root", "/tmp/ar"])
        self.assertEqual(os.path.basename(argv[0]), "clp")

    def test_a_bundled_text_format_names_the_application(self):
        app, why = A.app_of_detected(A.parse_detect(VLLM_REPORT))
        self.assertIs(app, A.VLLM)
        self.assertIn("vllm-sflow", why)

    def test_an_unregistered_application_is_not_an_error(self):
        app, why = A.app_of_detected(A.parse_detect(COCKROACH_REPORT))
        self.assertIsNone(app)
        self.assertIn("no registered application", why)
        self.assertIn("channel", why)

    def test_a_file_that_is_not_logs_is_an_error_naming_what_it_saw(self):
        with self.assertRaises(A.AnalyzeError) as caught:
            A.app_of_detected(A.parse_detect(BINARY_REPORT))
        self.assertIn("not logs", str(caught.exception))
        self.assertIn("binary", str(caught.exception))

    def test_files_from_different_applications_take_the_general_route_together(self):
        mixed = A.parse_detect(VLLM_REPORT + COCKROACH_REPORT)
        app, why = A.app_of_detected(mixed)
        self.assertIsNone(app)
        self.assertIn("not all one application", why)

    def test_a_folder_of_one_application_keeps_its_name(self):
        app, _ = A.app_of_detected(A.parse_detect(VLLM_REPORT + VLLM_REPORT))
        self.assertIs(app, A.VLLM)

    def test_a_refusal_the_detector_prints_a_command_this_wrapper_does_not_run(self):
        with self.assertRaises(A.AnalyzeError):
            A.compress_folder_argv(["rm", "-rf", "/"])


class ReadingAnArchivesRecords(unittest.TestCase):
    """The same rule, applied to the root fields of a merged schema tree."""

    def test_a_session_archives_root_fields_are_claude_code(self):
        roots = {"type": 44818, "sessionId": 44494, "timestamp": 33508, "uuid": 32614,
                 "parentUuid": 32553, "cwd": 32614, "gitBranch": 32614, "isSidechain": 32614,
                 "userType": 32614, "version": 32614, "entrypoint": 32614, "message": 16604,
                 "toolUseResult": 4525, "slug": 32500}
        app, why = A.identify_from_records(roots)
        self.assertIs(app, A.CLAUDE_CODE)
        self.assertIn("13 of this application's 14", why)

    def test_a_vllm_archives_root_fields_are_vllm(self):
        app, why = A.identify_from_records({"timestamp": 1, "logger": 1, "level": 1, "worker": 1,
                                            "message": 1})
        self.assertIs(app, A.VLLM)
        self.assertIn("exactly the field set", why)

    def test_one_extra_field_is_not_vllm_because_the_set_is_exact(self):
        app, _ = A.identify_from_records({"timestamp": 1, "logger": 1, "level": 1, "worker": 1,
                                          "message": 1, "trace_id": 1})
        self.assertIsNone(app)

    def test_a_session_id_alone_is_not_claude_code(self):
        # The requires pair keeps an application that merely names a session out of the route.
        app, _ = A.identify_from_records({"sessionId": 1, "message": 1, "timestamp": 1})
        self.assertIsNone(app)

    def test_a_codex_rollouts_root_fields_are_codex(self):
        app, why = A.identify_from_records({"timestamp": 14, "ordinal": 14, "type": 14,
                                            "payload": 14})
        self.assertIs(app, A.CODEX)
        self.assertIn("exactly the field set", why)

    def test_routes_are_registered_against_applications(self):
        self.assertEqual(A.CLAUDE_CODE.route, A.SPECIALISED)
        self.assertEqual(A.VLLM.route, A.GENERAL)
        self.assertEqual(A.application("claude-code"), A.CLAUDE_CODE)
        with self.assertRaises(A.AnalyzeError):
            A.application("nginx")

    def test_being_recognised_can_buy_only_the_right_acquire(self):
        # Codex is recognised and still general: the registration changes how its logs are acquired
        # (as a session, so its payload arrays become columns) and nothing about the analysis.
        self.assertEqual(A.CODEX.route, A.GENERAL)
        self.assertEqual(A.CODEX.acquire, "session")
        argv = A.compress_session_argv("/c/sessions/2026/r.jsonl", None, agent=A.CODEX.agent)
        self.assertEqual(argv[argv.index("--agent") + 1], "codex")
        self.assertNotIn("--claude-root", argv)


class WhatIsAlreadyPrepared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def archive(self, name, metadata, when=None):
        """An archive directory with the header and table_metadata that make it one."""
        path = os.path.join(self.root, name)
        inner = os.path.join(path, "0000")
        os.makedirs(inner)
        for leaf in ("header", "table_metadata"):
            open(os.path.join(inner, leaf), "w").close()
        with open(os.path.join(path, ".yscope-clp-archive.json"), "w") as handle:
            json.dump(metadata, handle)
        return path

    def test_a_session_archive_is_reused_only_when_the_log_is_the_same_bytes(self):
        log = os.path.join(self.root, "s.jsonl")
        with open(log, "w") as handle:
            handle.write('{"sessionId":"s"}\n')
        sha = A.sha256_file(log)
        self.archive("a1", {"createdAt": "2026-01-01T00:00:00Z",
                            "session": {"file": log, "id": "s", "sha256": sha}})
        found = A.existing_session_archive(self.root, log, sha)
        self.assertEqual(os.path.basename(found["dir"]), "a1")
        self.assertIn("sha256", found["why"])
        # The log changes: the archive no longer describes it, so nothing is reused.
        with open(log, "a") as handle:
            handle.write('{"sessionId":"s"}\n')
        self.assertEqual(A.existing_session_archive(self.root, log, A.sha256_file(log)), {})

    def test_the_newest_of_several_matching_session_archives_is_taken(self):
        log = os.path.join(self.root, "s.jsonl")
        open(log, "w").close()
        sha = A.sha256_file(log)
        for name, when in (("old", "2026-01-01T00:00:00Z"), ("new", "2026-06-01T00:00:00Z")):
            self.archive(name, {"createdAt": when,
                                "session": {"file": log, "id": "s", "sha256": sha}})
        found = A.existing_session_archive(self.root, log, sha)
        self.assertEqual(os.path.basename(found["dir"]), "new")
        self.assertEqual(found["matches"], 2)

    def test_a_log_archive_is_reused_when_nothing_it_holds_was_touched_since(self):
        log = os.path.join(self.root, "w3.log")
        with open(log, "w") as handle:
            handle.write("line\n")
        os.utime(log, (1000, 1000))     # long before the archive was written
        self.archive("a1", {"createdAt": "2026-01-01T00:00:00Z",
                            "source": {"paths": [log], "type": "file"}})
        found = A.existing_log_archive(self.root, [log])
        self.assertEqual(os.path.basename(found["dir"]), "a1")
        # Appending to the log makes it newer than the archive, so the archive is stale.
        os.utime(log, (time.time(), time.time()))
        self.assertEqual(A.existing_log_archive(self.root, [log]), {})

    def test_a_log_archive_of_other_files_is_not_reused(self):
        log = os.path.join(self.root, "w3.log")
        open(log, "w").close()
        os.utime(log, (1000, 1000))
        self.archive("a1", {"createdAt": "2026-01-01T00:00:00Z",
                            "source": {"paths": [log, os.path.join(self.root, "w4.log")]}})
        self.assertEqual(A.existing_log_archive(self.root, [log]), {})

    def test_a_folders_own_mtime_is_not_what_decides(self):
        folder = os.path.join(self.root, "logs")
        os.makedirs(folder)
        inner = os.path.join(folder, "a.log")
        open(inner, "w").close()
        self.archive("a1", {"createdAt": "2026-01-01T00:00:00Z", "source": {"paths": [folder]}})
        os.utime(folder, (1000, 1000))              # the folder looks old ...
        os.utime(inner, (time.time(), time.time()))  # ... but a log inside it was just written
        self.assertEqual(A.existing_log_archive(self.root, [folder]), {})

    def test_a_directory_with_no_archive_in_it_is_not_one(self):
        empty = os.path.join(self.root, "not-an-archive")
        os.makedirs(empty)
        self.assertEqual(A.archive_dirs_under(empty), [])
        self.assertFalse(A.looks_like_archive(empty))


class WhereThingsGo(unittest.TestCase):
    def test_the_bundles_root_default_names_no_home_and_no_install(self):
        env = dict(os.environ)
        try:
            os.environ.pop("CLP_S_BUNDLES_ROOT", None)
            os.environ["TMPDIR"] = "/tmp"
            self.assertEqual(A.bundles_root(), "/tmp/yscope-clp-bundles")
            os.environ["CLP_S_BUNDLES_ROOT"] = "/tmp/elsewhere"
            self.assertEqual(A.bundles_root(), "/tmp/elsewhere")
            self.assertEqual(A.bundles_root("/tmp/flag"), "/tmp/flag")
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_a_broad_bundles_root_is_refused(self):
        for path in ("/", "/home", os.path.expanduser("~"), "/home/someone/.claude",
                     "/home/someone/.claude/projects", "/Users/someone/.codex/sessions"):
            with self.assertRaises(A.AnalyzeError, msg=path):
                A.refuse_broad_dir(path, "--bundles-root")
        self.assertEqual(A.refuse_broad_dir("/tmp/ok", "--bundles-root"), "/tmp/ok")

    def test_the_session_compressor_is_told_the_layout_and_nothing_else(self):
        argv = A.compress_session_argv("/h/projects/p/s.jsonl", "/h", archives_root_value="/tmp/ar",
                                       clp_s="/opt/clp-s")
        self.assertEqual([os.path.basename(argv[0]), *argv[1:3]], ["clp", "compress", "session"])
        self.assertIn("--claude-root", argv)
        self.assertEqual(argv[argv.index("--claude-root") + 1], "/h/projects")
        self.assertEqual(argv[argv.index("--archives-root") + 1], "/tmp/ar")
        self.assertEqual(argv[argv.index("--clp-s-bin") + 1], "/opt/clp-s")
        # --structurize-arrays and the timestamp key are the wrapper's own doing, not re-decided.
        self.assertNotIn("--structurize-arrays", argv)
        self.assertEqual(argv[argv.index("--timestamp-key") + 1], "timestamp")


class TheCommandItself(unittest.TestCase):
    def run_it(self, *args):
        proc = subprocess.run([os.path.join(BIN, "clp"), *args], capture_output=True,
                              text=True)
        return proc

    def test_help_explains_the_one_question_it_asks(self):
        proc = self.run_it("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("which application produced these logs", proc.stdout)
        self.assertIn("NEXT=", proc.stdout)

    def test_an_unknown_flag_is_refused_rather_than_passed_through(self):
        proc = self.run_it("--target-encoded-size", "1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown argument", proc.stderr)

    def test_a_missing_target_is_not_guessed_at(self):
        proc = self.run_it("/no/such/thing/at/all.log", "--quiet")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no such target", proc.stderr)
        self.assertIn("session id", proc.stderr)

    def test_a_binary_file_exits_non_zero_naming_what_it_saw(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "blob.log")
            with open(path, "wb") as handle:
                handle.write(b"\x00\x01\x02" * 4000)
            proc = self.run_it(path, "--quiet")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not logs", proc.stderr)
        self.assertIn("binary", proc.stderr)

    def test_an_empty_directory_exits_non_zero_saying_what_would_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.run_it(tmp, "--quiet")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not logs", proc.stderr)
        self.assertIn("--extensions", proc.stderr)

    def test_force_and_dry_run_contradict_each_other(self):
        proc = self.run_it("--force", "--dry-run")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
