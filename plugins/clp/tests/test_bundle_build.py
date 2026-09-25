"""clp-bundle build on a small synthetic session, with a stub clp-s.

The stub is the only engine here: `c` keeps the records of the files it is given, and `s --count` counts
them, so the tests exercise the build and the catalog, not compression. They are the acceptance checks
written down for the day the engine changes (see the knowledge base), on data that is not real.
"""

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
sys.path.insert(0, os.path.join(BIN, "lib"))
import bundle  # noqa: E402

SID = "5e550000-0000-0000-0000-000000000001"

STUB = textwrap.dedent(r"""
    #!/usr/bin/env python3
    # A stand-in for clp-s: `c --files-from LIST DIR` keeps each file's records in the order listed and fails
    # like clp-s on a line that is not a JSON object; `s --count DIR *` counts per archive; `s ARCHIVE * file
    # --path OUT` writes each record with its position as msgpack, in reverse order (the builder must sort).
    import json, os, struct, sys, uuid
    a = sys.argv[1:]
    def records(d):
        return [l for l in open(f"{d}/records.jsonl") if l.strip()]
    if a[0] == "c":
        files_from = a[a.index("--files-from") + 1]
        out = a[-1]
        aid = str(uuid.uuid4())
        os.makedirs(f"{out}/{aid}")
        for n in ("header", "table_metadata"):
            open(f"{out}/{aid}/{n}", "w").close()
        with open(f"{out}/{aid}/records.jsonl", "w") as dst:
            for path in open(files_from).read().split():
                offset = 0
                lines = open(path, "rb").readlines()
                for number, line in enumerate(lines, 1):
                    if number == len(lines) and not line.endswith(b"\n"):
                        try:
                            json.loads(line)
                        except ValueError:
                            break               # like clp-s: a record cut off at the end is dropped silently
                    if line.strip():
                        try:
                            ok = isinstance(json.loads(line), dict)
                        except ValueError:
                            ok = False
                        if not ok:
                            end = offset - 1 if offset else 0      # like clp-s: the end of the last good record
                            sys.stderr.write(f"[error] Encountered non-json-object while trying to parse {path} after parsing {end} bytes\n")
                            sys.exit(1)
                        dst.write(line.decode().rstrip("\n") + "\n")
                    offset += len(line)
    elif a[0] == "s":
        pos = [x for x in a[1:] if not x.startswith("--")]
        target = pos[0]
        if "--count" in a:
            for aid in sorted(os.listdir(target)):
                n = len(records(f"{target}/{aid}")) + int(os.environ.get("STUB_COUNT_OFF_BY", "0"))
                print(json.dumps({"archive_id": aid, "count": n}))
        elif "file" in pos:
            rows = list(enumerate(records(target)))
            if os.environ.get("STUB_DROP_ONE"):
                rows = rows[1:]
            def s(text):
                b = text.encode()
                return b"\xdb" + struct.pack(">I", len(b)) + b
            with open(a[a.index("--path") + 1], "wb") as fh:
                for i, line in reversed(rows):
                    fh.write(b"\x95" + b"\xd3" + struct.pack(">q", 0) + s(line) + s("") + s(os.path.basename(target))
                             + b"\xce" + struct.pack(">I", i))
""").lstrip()


def line(**kw):
    return json.dumps(kw, separators=(",", ":"))


def T(h, m, s=0):
    return f"2026-01-01T{h:02d}:{m:02d}:{s:02d}.000Z"


def use(u, ts, tid, name, inp=None, tokens=None, **extra):
    message = {"role": "assistant", "id": "m" + u, "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}]}
    if tokens:
        message["usage"] = {"input_tokens": tokens[0], "output_tokens": tokens[1], "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}
    return line(uuid=u, type="assistant", timestamp=ts, message=message, **extra)


def result(u, ts, tid, tur=None, error=False, **extra):
    return line(uuid=u, type="user", timestamp=ts, toolUseResult=tur, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok", "is_error": error}]}, **extra)


def note(u, ts, task, status="completed"):
    return line(uuid=u, type="attachment", timestamp=ts, attachment={
        "type": "queued_command", "prompt": f"<task-notification><task-id>{task}</task-id><status>{status}</status></task-notification>"})


def text(u, ts, who, body, **extra):
    return line(uuid=u, type=who, timestamp=ts, message={"role": who, "content": [{"type": "text", "text": body}]}, **extra)


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in rows:
            fh.write(r + "\n")


def put(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)


def make_session(home, with_agents=True):
    proj = f"{home}/projects/p"
    root = f"{proj}/{SID}"
    main = [
        text("u01", T(10, 0), "user", "hello"),
        line(type="mode", mode="plan"),                                              # no uuid: bookkeeping
        use("u02", T(10, 0, 5), "tu1", "Agent", tokens=(5000, 50)),
        result("u03", T(10, 0, 6), "tu1", {"agentId": "a1", "status": "async_launched"}),
        use("u04", T(10, 1, 0), "tw1", "Workflow", {"scriptPath": "x.js"}),
        result("u05", T(10, 1, 1), "tw1", {"runId": "wf_x", "taskId": "tk1", "status": "async_launched"}),
        note("u06", T(10, 10), "tk1"),
        note("u07", T(10, 10, 30), "a1"),
        use("u08", T(10, 20), "tw2", "Workflow", {"resumeFromRunId": "wf_x", "scriptPath": "x.js"}),
        result("u09", T(10, 20, 1), "tw2", {"runId": "wf_x", "taskId": "tk2", "status": "async_launched"}),
        note("u10", T(10, 30), "tk2"),
        line(uuid="u11", type="attachment", timestamp=T(10, 31), attachment={"type": "hook", "content": "x"}),  # unlisted
        text("u12", T(11, 0), "user", "second prompt"),
    ]
    write(f"{proj}/{SID}.jsonl", main)
    if with_agents:
        def meta(path, **kw):
            put(path, json.dumps(kw))
        meta(f"{root}/subagents/agent-a1.meta.json", agentType="general-purpose", description="explore", spawnDepth=1, toolUseId="tu1")
        meta(f"{root}/subagents/agent-a2.meta.json", agentType="general-purpose", description="nested", spawnDepth=2, parentAgentId="a1")
        write(f"{root}/subagents/agent-a1.jsonl", [
            use("a1u1", T(10, 0, 10), "b1", "Bash", agentId="a1"),
            result("a1u2", T(10, 0, 20), "b1", agentId="a1"),
            line(uuid="a1u3", type="attachment", timestamp=T(10, 0, 21), agentId="a1", attachment={"type": "hook"}),   # unlisted
            line(type="fork-context-ref", agentId="a1"),                                                            # no uuid
        ])
        def split(u, ts, usage):                         # one response (id mR) written as two records
            return line(uuid=u, type="assistant", timestamp=ts, agentId="a2",
                        message={"role": "assistant", "id": "mR", "content": [{"type": "text", "text": "."}], "usage": usage})
        write(f"{root}/subagents/agent-a2.jsonl", [
            split("a2r1", T(10, 0, 31), {"input_tokens": 999, "output_tokens": 0}),            # preliminary
            split("a2r2", T(10, 0, 32), {"input_tokens": 700, "output_tokens": 70, "cache_read_input_tokens": 5,
                                         "cache_creation_input_tokens": 0}),
            use("a2u1", T(10, 0, 30), "b2", "Read", agentId="a2"), result("a2u2", T(10, 0, 40), "b2", agentId="a2"),
            text("a2u3", T(10, 0, 50), "assistant", "done", agentId="a2")])
        wf = f"{root}/subagents/workflows/wf_x"
        for aid in ("w1", "w2", "w3"):
            meta(f"{wf}/agent-{aid}.meta.json", agentType="workflow-agent", spawnDepth=2)
        write(f"{wf}/agent-w1.jsonl", [use("w1u1", T(10, 2), "c1", "Bash", tokens=(100, 10), agentId="w1"), result("w1u2", T(10, 4), "c1", agentId="w1"),
                                       line(uuid="w1u3", type="system", timestamp=T(10, 4, 1), agentId="w1")])           # unlisted
        write(f"{wf}/agent-w2.jsonl", [use("w2u1", T(10, 2, 30), "c2", "Bash", tokens=(200, 20), agentId="w2"), result("w2u2", T(10, 2, 31), "c2", agentId="w2"),
                                       text("w2u3", T(10, 5, 31), "user", "[Request interrupted by user]", agentId="w2")])
        write(f"{wf}/agent-w3.jsonl", [use("w3u1", T(10, 21), "c3", "Read", tokens=(300, 30), agentId="w3"), result("w3u2", T(10, 23), "c3", agentId="w3")])
        write(f"{wf}/journal.jsonl", [line(type="started", key="v2:k1aaaaaaaa", agentId="w1"), line(type="started", key="v2:k2bbbbbbbb", agentId="w2"),
                                      line(type="started", key="v2:k2bbbbbbbb", agentId="w3"),
                                      line(type="result", key="v2:k1aaaaaaaa", agentId="w1", result={}), line(type="result", key="v2:k2bbbbbbbb", agentId="w3", result={})])
        os.makedirs(f"{root}/workflows/scripts", exist_ok=True)
        os.makedirs(f"{root}/workflows", exist_ok=True)
        put(f"{root}/workflows/wf_x.json", json.dumps({"runId": "wf_x", "taskId": "tk2", "workflowName": "check", "status": "completed", "agentCount": 2, "durationMs": 1,
                   "totalTokens": 1, "totalToolCalls": 1, "phases": [{"title": "Only"}],
                   "logs": ['[stall] agent "w2label" stalled (no progress) after 180s — retrying (1/5)'],
                   "workflowProgress": [{"type": "workflow_agent", "agentId": "w1", "label": "w1label", "state": "done", "phaseIndex": 1, "phaseTitle": "Only"},
                                        {"type": "workflow_agent", "agentId": "w3", "label": "w2label", "state": "done", "phaseIndex": 1, "phaseTitle": "Only"}]}))
        put(f"{root}/workflows/scripts/check-wf_x.js", "export const meta = {}\n")
        put(f"{root}/tool-results/t1.txt", "big output\n")
    put(f"{home}/tasks/{SID}/1.json", "{}")
    put(f"{home}/file-history/{SID}/abc@v1", "snapshot")


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.stub = os.path.join(self.tmp.name, "clp-s")
        with open(self.stub, "w") as fh:
            fh.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.out = os.path.join(self.tmp.name, "bundle")

    def cli(self, *args, env=None, out=None):
        e = {**os.environ, "CLP_S_BIN": self.stub, **(env or {})}
        e = {k: v for k, v in e.items() if v is not None}     # an env value of None removes the variable
        p = subprocess.run([os.path.join(BIN, "clp-bundle"), out or self.out, *args], capture_output=True, text=True, env=e)
        return p.returncode, p.stdout, p.stderr

    def build(self, *extra, **kw):
        return self.cli("build", "--session-file", f"{self.home}/projects/p/{SID}.jsonl", *extra, **kw)

    def db(self):
        db = sqlite3.connect(os.path.join(self.out, "catalog.sqlite"))
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        return db


class Full(BuildTest):
    def setUp(self):
        super().setUp()
        make_session(self.home)
        self.code, self.stdout, self.stderr = self.build()

    def test_builds_and_classifies_every_file(self):
        self.assertEqual(self.code, 0, self.stderr)
        self.assertIn("INVENTORY files=", self.stdout)
        n = self.db().execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        self.assertEqual(n, 1 + 2 + 2 + 3 + 3 + 1 + 1 + 1 + 1 + 1 + 1)  # main, agents+metas, wf agents+metas, journal, run, script, tool result, task, snapshot
        self.assertTrue(os.path.isfile(f"{self.out}/files/tool-results/t1.txt"))      # at its path in the session
        self.assertTrue(os.path.isfile(f"{self.out}/files/subagents/agent-a1.meta.json"))
        self.assertTrue(os.path.isfile(f"{self.out}/manifest.json"))
        self.assertTrue(os.path.isfile(f"{self.out}/files/file-history/abc@v1"))
        self.assertEqual(len(os.listdir(f"{self.out}/archives")), 5)   # one archive per kind of log

    def test_archives_hold_every_record(self):
        rows = dict(self.db().execute("SELECT kind, records FROM archives").fetchall())
        self.assertEqual(rows, {"main": 13, "agent": 9, "workflow-agent": 8, "workflow-journal": 5, "workflow-run": 1})

    def test_events_are_counted_and_the_leftovers_accounted_for(self):
        b = dict(self.db().execute("SELECT k, v FROM bundle").fetchall())
        self.assertEqual((b["events_unlisted_main"], b["events_skipped_main"]), ("1", "1"))
        self.assertEqual((b["events_unlisted_agent"], b["events_skipped_agent"]), ("1", "1"))
        self.assertEqual(b["events_unlisted_workflow-agent"], "1")
        kinds = dict(self.db().execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
        self.assertEqual(kinds, {"main": 11, "agent": 7, "workflow-agent": 7})
        self.assertEqual(b["layout"], str(bundle.LAYOUT))

    def test_interrupts_equal_attempts_without_an_outcome(self):
        db = self.db()
        interrupts = db.execute("SELECT COUNT(*) FROM events WHERE kind='workflow-agent' AND interrupt=1").fetchone()[0]
        silent = db.execute("SELECT COUNT(*) FROM nodes WHERE kind='attempt' AND status IN ('stalled-retried','unresolved')").fetchone()[0]
        self.assertEqual((interrupts, silent), (1, 1))

    def test_attempt_status_and_the_resume_boundary(self):
        rows = {r["agent_id"]: (r["status"], r["cause"], r["instance"]) for r in self.db().execute("SELECT * FROM nodes WHERE kind='attempt'")}
        self.assertEqual(rows["w1"], ("ok", None, "wf:wf_x"))
        self.assertEqual(rows["w2"], ("stalled-retried", "stall", "wf:wf_x"))
        self.assertEqual(rows["w3"], ("ok", None, "wf:wf_x~2"))     # started after the resume launch
        inst = {r["id"]: r["status"] for r in self.db().execute("SELECT * FROM nodes WHERE kind='workflow'")}
        self.assertEqual(inst, {"wf:wf_x": "completed", "wf:wf_x~2": "completed"})

    def test_a_main_thread_record_reaches_the_node_it_launched(self):
        code, out, _ = self.cli("who", "--uuid", "u08")
        self.assertIn("NODE wf:wf_x~2 kind=workflow relation=launched", out)
        _, out, _ = self.cli("who", "--uuid", "u06")
        self.assertIn("NODE wf:wf_x kind=workflow relation=refers_to", out)
        _, out, _ = self.cli("who", "--uuid", "u03")
        self.assertIn("NODE agent:a1 kind=agent relation=refers_to", out)

    def test_tokens_add_up_from_attempts_to_instances_and_runs(self):
        t = {r["id"]: (r["tokens_input"], r["tokens_output"]) for r in self.db().execute("SELECT * FROM nodes")}
        self.assertEqual(t["attempt:w1"], (100, 10))
        self.assertEqual(t["wf:wf_x"], (300, 30))           # w1 and w2 ran in the first instance
        self.assertEqual(t["wf:wf_x~2"], (300, 30))         # w3 ran in the resume
        self.assertEqual(t["run:wf_x"], (600, 60))
        self.assertEqual(t["main"], (5000, 50))

    def test_a_response_split_over_records_counts_once_on_one_event(self):
        db = self.db()
        rows = db.execute("SELECT uuid, tokens_input, tokens_output FROM events WHERE message_id = 'mR' ORDER BY pos").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("a2r1", None, None), ("a2r2", 700, 70)])
        agent = db.execute("SELECT tokens_input, tokens_output, tokens_cache_read FROM nodes WHERE id = 'agent:a2'").fetchone()
        self.assertEqual(tuple(agent), (700, 70, 5))
        mismatched = db.execute("SELECT COUNT(*) FROM nodes n WHERE n.kind IN ('agent','attempt') AND n.tokens_input != "
                                "(SELECT COALESCE(SUM(e.tokens_input), 0) FROM events e WHERE e.agent_id = n.agent_id)").fetchone()[0]
        self.assertEqual(mismatched, 0)

    def test_nested_agent_and_turns(self):
        rows = {r["id"]: r for r in self.db().execute("SELECT * FROM nodes WHERE kind='agent'")}
        self.assertEqual(rows["agent:a2"]["label"], "nested")
        edges = {(r["src"], r["dst"]) for r in self.db().execute("SELECT * FROM edges WHERE kind='launch'")}
        self.assertIn(("agent:a1", "agent:a2"), edges)
        self.assertIn(("main", "agent:a1"), edges)
        turns = dict(self.db().execute("SELECT uuid, turn FROM events WHERE kind='main'").fetchall())
        self.assertEqual((turns["u01"], turns["u08"], turns["u12"]), (1, 1, 2))

    def test_the_graph_facts_are_reported(self):
        self.assertIn('GRAPH resumed_instances=1', self.stdout)
        self.assertNotIn("WARNING", self.stdout)

    def test_refuses_an_existing_directory_and_replaces_only_a_bundle(self):
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(self.build("--force")[0], 0)
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        put(os.path.join(plain, "keep.txt"), "")
        code, _, err = self.build("--force", out=plain)
        self.assertEqual(code, 1)
        self.assertTrue(os.path.exists(os.path.join(plain, "keep.txt")))   # not a bundle: never deleted


class Failures(BuildTest):
    def test_an_unclassified_file_stops_the_build_and_leaves_nothing(self):
        make_session(self.home)
        put(f"{self.home}/projects/p/{SID}/subagents/notes.txt", "?")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("unclassified file", err)
        self.assertFalse(os.path.exists(self.out))

    def test_an_archive_that_disagrees_with_the_manifest_fails_the_build_and_cleans_up(self):
        make_session(self.home)
        code, _, err = self.build(env={"STUB_COUNT_OFF_BY": "1"})
        self.assertEqual(code, 1)
        self.assertIn("the manifest says", err)
        self.assertFalse(os.path.exists(self.out))

    def test_an_archive_read_with_a_gap_fails_the_build(self):
        make_session(self.home)
        code, _, err = self.build(env={"STUB_DROP_ONE": "1"})
        self.assertEqual(code, 1)
        self.assertIn("expected positions 0 to", err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_line_that_is_not_json_names_its_file_and_line(self):
        make_session(self.home)
        path = f"{self.home}/projects/p/{SID}.jsonl"
        lines = open(path).read().split("\n")
        lines.insert(4, "{broken")                                   # in the middle: clp-s reports it
        with open(path, "w") as fh:
            fh.write("\n".join(lines))
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn(f"{SID}.jsonl:5: not a JSON record", err)
        with open(path, "w") as fh:                                  # at the end, complete: named the same way
            fh.write("\n".join(l for l in lines if l != "{broken") + "{broken\n")
        code, _, err = self.build()
        self.assertIn(f"{SID}.jsonl:14: not a JSON record", err)

    def test_a_record_cut_off_at_the_end_is_named_not_dropped(self):
        make_session(self.home)
        with open(f"{self.home}/projects/p/{SID}.jsonl", "a") as fh:     # a write that stopped mid-record
            fh.write('{"uuid":"u99","type":"user","timestamp":"2026-01-01T11:30:00.000Z","mess')
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn(f"{SID}.jsonl:14: the last record is cut off", err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_meta_without_its_transcript_is_an_error(self):
        make_session(self.home)
        os.remove(f"{self.home}/projects/p/{SID}/subagents/agent-a1.jsonl")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("agents with a meta file but no transcript: a1", err)

    def test_a_missing_engine_is_a_plain_error(self):
        make_session(self.home)
        session = f"{self.home}/projects/p/{SID}.jsonl"
        code, _, err = self.cli("build", "--session-file", session, env={"CLP_S_BIN": "/nonexistent/clp-s"})
        self.assertEqual(code, 1)
        self.assertIn("CLP_S_BIN is set but is not an executable file", err)
        code, _, err = self.cli("build", "--session-file", session, "--clp-s", "/nonexistent/clp-s")
        self.assertIn("--clp-s is set but is not an executable file", err)
        only_python = os.path.join(self.tmp.name, "only-python")     # a PATH with python and no clp-s
        os.makedirs(only_python)
        os.symlink(os.path.realpath(sys.executable), os.path.join(only_python, "python3"))
        code, _, err = self.cli("build", "--session-file", session, env={"CLP_S_BIN": None, "PATH": only_python})
        self.assertEqual(code, 1)
        self.assertIn("clp-s is not available", err)


class Repairs(BuildTest):
    """What real Claude Code sessions contain that the first sessions did not."""

    def test_nul_bytes_from_lost_writes_are_removed_and_counted(self):
        make_session(self.home)
        main = f"{self.home}/projects/p/{SID}.jsonl"
        with open(main, "ab") as fh:                                  # a lost write at the end
            fh.write(b"\x00" * 50 + b"\n")
        transcript = f"{self.home}/projects/p/{SID}/subagents/agent-a2.jsonl"
        lines = open(transcript, "rb").read().split(b"\n")
        lines.insert(1, b"\x00" * 30 + use("a2u9", T(10, 0, 35), "b9", "Grep", agentId="a2").encode())  # NULs, then a record
        with open(transcript, "wb") as fh:
            fh.write(b"\n".join(lines))
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertIn("REPAIRED", out)
        rows = {r["path"].split("/")[-1]: (r["nul_bytes"], r["damaged_lines"]) for r in
                self.db().execute("SELECT * FROM sources WHERE nul_bytes > 0")}
        self.assertEqual(rows, {f"{SID}.jsonl": (50, 1), "agent-a2.jsonl": (30, 1)})
        self.assertEqual(self.db().execute("SELECT agent_id FROM events WHERE uuid = 'a2u9'").fetchone()[0], "a2")
        self.assertTrue(open(main, "rb").read().endswith(b"\x00" * 50 + b"\n"))       # the source is untouched

    def test_a_uuid_written_twice_is_kept_twice(self):
        make_session(self.home)
        with open(f"{self.home}/projects/p/{SID}.jsonl", "a") as fh:     # the rewrite a reopened session makes
            fh.write(text("u01", T(10, 0), "user", "hello", slug="some-slug") + "\n")
        code, _, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM events WHERE uuid = 'u01'").fetchone()[0], 2)
        _, out, _ = self.cli("who", "--uuid", "u01")
        self.assertIn("COPIES 2", out)

    def test_a_session_title_and_a_classifier_dump_are_kept_as_files(self):
        make_session(self.home)
        put(f"{self.home}/projects/p/{SID}/custom-title.json", '{"customTitle":"my session"}')
        put(f"{self.home}/projects/p/{SID}/auto-mode-classifier-error.txt", "=== ERROR ===\n")
        code, _, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.db().execute("SELECT v FROM bundle WHERE k = 'title'").fetchone()[0], "my session")
        self.assertTrue(os.path.isfile(f"{self.out}/files/auto-mode-classifier-error.txt"))


class Rebuild(BuildTest):
    def test_the_catalog_rebuilds_from_the_bundle_alone(self):
        make_session(self.home)
        self.assertEqual(self.build()[0], 0)
        before = self.snapshot()
        shutil.rmtree(self.home)                                         # the session's files are gone
        os.remove(f"{self.out}/catalog.sqlite")
        code, out, err = self.cli("rebuild")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.snapshot(), before)

    def test_a_catalog_of_another_layout_is_refused_with_the_fix(self):
        make_session(self.home)
        self.build()
        db = sqlite3.connect(f"{self.out}/catalog.sqlite")
        db.execute("UPDATE bundle SET v = '1' WHERE k = 'layout'")
        db.commit()
        db.close()
        code, _, err = self.cli("show", "main")
        self.assertEqual(code, 1)
        self.assertIn(f"has layout 1, not layout {bundle.LAYOUT}; rebuild it with", err)
        self.assertEqual(self.cli("rebuild")[0], 0)
        self.assertEqual(self.cli("show", "main")[0], 0)

    def snapshot(self):
        db = sqlite3.connect(f"{self.out}/catalog.sqlite")
        try:
            return {t: sorted(map(repr, db.execute(f"SELECT * FROM {t}")))
                    for t in ("nodes", "edges", "agents", "events", "event_tools", "sources", "archives")}
        finally:
            db.close()


class SmallSession(BuildTest):
    def test_a_session_with_no_agents_gets_a_catalog_with_only_its_main_thread(self):
        make_session(self.home, with_agents=False)
        code, stdout, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual([r[0] for r in self.db().execute("SELECT id FROM nodes")], ["main"])
        self.assertEqual(len(os.listdir(f"{self.out}/archives")), 1)


if __name__ == "__main__":
    unittest.main()
