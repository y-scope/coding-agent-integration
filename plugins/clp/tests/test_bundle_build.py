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
    import json, os, sys, uuid
    a = sys.argv[1:]
    if a[0] == "c":
        files_from = a[a.index("--files-from") + 1]
        out = a[-1]
        aid = str(uuid.uuid4())
        os.makedirs(f"{out}/{aid}")
        for n in ("header", "table_metadata"):
            open(f"{out}/{aid}/{n}", "w").close()
        with open(f"{out}/{aid}/records.jsonl", "w") as dst:
            for path in open(files_from).read().split():
                for line in open(path):
                    if line.strip():
                        dst.write(line if line.endswith("\n") else line + "\n")
    elif a[0] == "s":
        pos = [x for x in a[1:] if not x.startswith("--")]
        target, query = pos[0], pos[1]
        def count(d, field=None):
            n = 0
            for line in open(f"{d}/records.jsonl"):
                n += (field is None) or (field in json.loads(line))
            return n
        extra = int(os.environ.get("STUB_UUID_OFF_BY", "0"))
        if os.path.exists(f"{target}/records.jsonl"):
            print(json.dumps({"archive_id": os.path.basename(target), "count": count(target, "uuid") + extra}))
        else:
            for aid in sorted(os.listdir(target)):
                print(json.dumps({"archive_id": aid, "count": count(f"{target}/{aid}")}))
""").lstrip()


def line(**kw):
    return json.dumps(kw, separators=(",", ":"))


def T(h, m, s=0):
    return f"2026-01-01T{h:02d}:{m:02d}:{s:02d}.000Z"


def use(u, ts, tid, name, inp=None, **extra):
    return line(uuid=u, type="assistant", timestamp=ts, message={"role": "assistant", "id": "m" + u, "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp or {}}]}, **extra)


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
        use("u02", T(10, 0, 5), "tu1", "Agent"),
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
        write(f"{root}/subagents/agent-a2.jsonl", [
            use("a2u1", T(10, 0, 30), "b2", "Read", agentId="a2"), result("a2u2", T(10, 0, 40), "b2", agentId="a2"),
            text("a2u3", T(10, 0, 50), "assistant", "done", agentId="a2")])
        wf = f"{root}/subagents/workflows/wf_x"
        for aid in ("w1", "w2", "w3"):
            meta(f"{wf}/agent-{aid}.meta.json", agentType="workflow-agent", spawnDepth=2)
        write(f"{wf}/agent-w1.jsonl", [use("w1u1", T(10, 2), "c1", "Bash", agentId="w1"), result("w1u2", T(10, 4), "c1", agentId="w1"),
                                       line(uuid="w1u3", type="system", timestamp=T(10, 4, 1), agentId="w1")])           # unlisted
        write(f"{wf}/agent-w2.jsonl", [use("w2u1", T(10, 2, 30), "c2", "Bash", agentId="w2"), result("w2u2", T(10, 2, 31), "c2", agentId="w2"),
                                       text("w2u3", T(10, 5, 31), "user", "[Request interrupted by user]", agentId="w2")])
        write(f"{wf}/agent-w3.jsonl", [use("w3u1", T(10, 21), "c3", "Read", agentId="w3"), result("w3u2", T(10, 23), "c3", agentId="w3")])
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
        self.assertTrue(os.path.isfile(f"{self.out}/files/tool-result/t1.txt"))
        self.assertTrue(os.path.isfile(f"{self.out}/files/file-history/abc@v1"))
        self.assertEqual(len(os.listdir(f"{self.out}/archives")), 5)   # one archive per kind of log

    def test_archives_hold_every_record(self):
        rows = dict(self.db().execute("SELECT kind, records FROM archives").fetchall())
        self.assertEqual(rows, {"main": 13, "agent": 7, "workflow-agent": 8, "workflow-journal": 5, "workflow-run": 1})

    def test_events_are_counted_and_the_leftovers_accounted_for(self):
        b = dict(self.db().execute("SELECT k, v FROM bundle").fetchall())
        self.assertEqual((b["events_unlisted_main"], b["events_skipped_main"]), ("1", "1"))
        self.assertEqual((b["events_unlisted_agent"], b["events_skipped_agent"]), ("1", "1"))
        self.assertEqual(b["events_unlisted_workflow-agent"], "1")
        kinds = dict(self.db().execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
        self.assertEqual(kinds, {"main": 11, "agent": 5, "workflow-agent": 7})
        self.assertEqual(b["layout"], "1")

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

    def test_events_that_disagree_with_the_archive_fail_the_build_and_clean_up(self):
        make_session(self.home)
        code, _, err = self.build(env={"STUB_UUID_OFF_BY": "1"})
        self.assertEqual(code, 1)
        self.assertIn("records with a uuid", err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_line_that_is_not_json_names_its_file_and_line(self):
        make_session(self.home)
        with open(f"{self.home}/projects/p/{SID}.jsonl", "a") as fh:
            fh.write("{broken\n")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn(f"{SID}.jsonl:14: not JSON", err)

    def test_a_meta_without_its_transcript_is_an_error(self):
        make_session(self.home)
        os.remove(f"{self.home}/projects/p/{SID}/subagents/agent-a1.jsonl")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("has no transcript", err)

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


class SmallSession(BuildTest):
    def test_a_session_with_no_agents_gets_a_catalog_with_only_its_main_thread(self):
        make_session(self.home, with_agents=False)
        code, stdout, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual([r[0] for r in self.db().execute("SELECT id FROM nodes")], ["main"])
        self.assertEqual(len(os.listdir(f"{self.out}/archives")), 1)


if __name__ == "__main__":
    unittest.main()
