"""clp-bundle against a tiny hand-made catalog and a stub search wrapper."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
sys.path.insert(0, os.path.join(BIN, "lib"))
import bundle  # noqa: E402

RECORDS = [
    {"agentId": "a1111111", "type": "user", "timestamp": "2026-01-01T10:00:05.000Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "listing"}]}},
    {"agentId": "a1111111", "type": "assistant", "timestamp": "2026-01-01T10:00:01.000Z",
     "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "id": "t"}]}},
    {"agentId": "a1111111", "type": "user", "timestamp": "2026-01-01T10:03:05.000Z",
     "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
]
MAIN_RECORD = {"uuid": "11111111-0000-0000-0000-000000000001", "type": "assistant", "timestamp": "2026-01-01T10:00:00.100Z",
               "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Workflow", "id": "toolu_9"}]}}
RUN = {"runId": "wf_x", "status": "completed", "agentCount": 1, "durationMs": 5, "logs": ["[stall] agent slow"]}

# The wrapper is called with: --archive-id ID ARCHIVES_DIR KQL. Rows go out unordered.
STUB = f"""#!/usr/bin/env bash
id="$2"; query="$4"
case "$query" in
  'agentId:"a1111111"') [[ "$id" == "arch-wfagent" ]] && printf '%s\\n' '{json.dumps(RECORDS[0])}' '{json.dumps(RECORDS[1])}' '{json.dumps(RECORDS[2])}';;
  'uuid:"11111111-0000-0000-0000-000000000001"') [[ "$id" == "arch-main" ]] && printf '%s\\n' '{json.dumps(MAIN_RECORD)}';;
  'runId:"wf_x"') [[ "$id" == "arch-run" ]] && printf '%s\\n' '{json.dumps(RUN)}';;
esac
"""


class BundleCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        os.makedirs(os.path.join(self.dir, "archives"))
        self.wrapper = os.path.join(self.dir, "wrapper")
        with open(self.wrapper, "w") as fh:
            fh.write(STUB)
        os.chmod(self.wrapper, os.stat(self.wrapper).st_mode | stat.S_IXUSR)
        db = sqlite3.connect(os.path.join(self.dir, "catalog.sqlite"))
        db.executescript(bundle.SCHEMA)
        db.execute("INSERT INTO bundle VALUES('layout', ?)", (str(bundle.LAYOUT),))
        db.executemany("INSERT INTO archives VALUES(?,?,?)", [("arch-wfagent", "workflow-agent", 3), ("arch-run", "workflow-run", 1), ("arch-main", "main", 2)])
        cols = "id,kind,label,agent_id,run_id,task_id,instance,start,end,status,cause,attrs"
        rows = [
            ("attempt:a1111111", "attempt", None, "a1111111", "wf_x", None, "wf:wf_x", "2026-01-01T10:00:00", "2026-01-01T10:03:05", "stalled-retried", "stall", "{}"),
            ("attempt:a2222222", "attempt", None, "a2222222", "wf_x", None, "wf:wf_x", "2026-01-01T10:03:05", "2026-01-01T10:05:00", "ok", None, "{}"),
            ("unit:wf_x:k1", "unit", "check one", None, "wf_x", None, None, "2026-01-01T10:00:00", "2026-01-01T10:05:00", None, None, "{}"),
            ("wf:wf_x", "workflow", "check", None, "wf_x", "tk1", "wf:wf_x", "2026-01-01T10:00:00", "2026-01-01T10:05:00", "completed", None, "{}"),
            ("run:wf_x", "run", "check", None, "wf_x", None, None, "2026-01-01T10:00:00", "2026-01-01T10:05:00", "completed", None, "{}"),
            ("launch-error:c1", "launch_error", "rejected", None, None, None, None, "2026-01-01T09:00:00", "2026-01-01T09:00:00", None, None, '{"error": "parse error"}'),
            ("main", "main", None, None, None, None, None, "2026-01-01T09:00:00", "2026-01-01T11:00:00", None, None, "{}"),
        ]
        db.executemany(f"INSERT INTO nodes({cols}) VALUES({','.join('?' * 12)})", rows)
        db.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?,?)", [
            ("unit:wf_x:k1", "attempt:a1111111", "contains", None, None, 0, None),
            ("unit:wf_x:k1", "attempt:a2222222", "contains", None, None, 0, None),
            ("wf:wf_x", "attempt:a1111111", "ran", None, None, 0, None),
            ("wf:wf_x", "run:wf_x", "executes", None, None, 0, None),
            ("main", "wf:wf_x", "launch", "2026-01-01T10:00:00", "Workflow result runId", 0, "toolu_9"),
        ])
        events = [  # uuid, kind, agent_id, ts, type, turn, human, interrupt, is_error, ref_agent_id, ref_task_id
            ("11111111-0000-0000-0000-000000000001", "main", None, "2026-01-01T10:00:00.100", "assistant", 1, 0, 0, 0, None, None),
            ("11111111-0000-0000-0000-000000000002", "main", None, "2026-01-01T10:20:00.000", "attachment", 1, 0, 0, 0, None, "tk1"),
            ("22222222-0000-0000-0000-000000000001", "workflow-agent", "a1111111", "2026-01-01T10:00:01.000", "assistant", None, 0, 0, 0, None, None),
            ("22222222-0000-0000-0000-000000000002", "workflow-agent", "a1111111", "2026-01-01T10:00:05.000", "user", None, 0, 0, 1, None, None),
            ("22222222-0000-0000-0000-000000000003", "workflow-agent", "a1111111", "2026-01-01T10:03:05.000", "user", None, 0, 1, 0, None, None),
        ]
        db.executemany("INSERT INTO events(uuid,kind,agent_id,ts,type,turn,human,interrupt,is_error,ref_agent_id,ref_task_id) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", events)
        ids = {u: i for u, i in db.execute("SELECT uuid, id FROM events")}
        db.executemany("INSERT INTO event_tools(event, tool_use_id, role, name, is_error) VALUES(?,?,?,?,?)", [
            (ids["11111111-0000-0000-0000-000000000001"], "toolu_9", "use", "Workflow", None),
            (ids["22222222-0000-0000-0000-000000000001"], "t", "use", "Bash", None),
            (ids["22222222-0000-0000-0000-000000000002"], "t", "result", None, 1),
        ])
        db.commit()
        db.close()

    def run_cli(self, *args):
        p = subprocess.run([os.path.join(BIN, "clp-bundle"), self.dir, "--search-wrapper", self.wrapper, *args],
                           capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def test_show_resolves_a_prefix_and_names_the_unit_and_archive(self):
        code, out, _ = self.run_cli("show", "a11111")
        self.assertEqual(code, 0)
        self.assertIn("NODE attempt:a1111111 kind=attempt label=\"check one\"", out)
        self.assertIn("STATUS stalled-retried cause=stall", out)
        self.assertIn("unit=unit:wf_x:k1", out)
        self.assertIn('ARCHIVE arch-wfagent query=agentId:"a1111111"', out)

    def test_evidence_is_sorted_by_time_and_tails(self):
        code, out, _ = self.run_cli("evidence", "attempt:a1111111", "--tail", "2")
        self.assertEqual(code, 0)
        lines = [l for l in out.splitlines() if l.startswith("  ")]
        self.assertEqual(len(lines), 2)
        self.assertIn("tool_result listing", lines[0])          # 10:00:05 sorts before the 10:03:05 interrupt
        self.assertIn("[Request interrupted by user]", lines[1])
        self.assertIn("records=3 shown=2", out)

    def test_evidence_raw_is_ndjson_in_time_order(self):
        _, out, _ = self.run_cli("evidence", "a1111111", "--raw")
        stamps = [json.loads(l)["timestamp"] for l in out.splitlines()]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(stamps), 3)

    def test_evidence_of_a_run_prints_its_log_lines(self):
        _, out, _ = self.run_cli("evidence", "run:wf_x")
        self.assertIn("RUN wf_x status=completed", out)
        self.assertIn("LOG [stall] agent slow", out)

    def test_evidence_of_a_unit_lists_each_attempt(self):
        _, out, _ = self.run_cli("evidence", "unit:wf_x:k1")
        self.assertIn("UNIT unit:wf_x:k1", out)
        self.assertIn("ATTEMPT attempt:a1111111 stalled-retried", out)
        self.assertIn("ATTEMPT attempt:a2222222 ok", out)

    def test_evidence_of_a_rejected_launch_is_its_error(self):
        _, out, _ = self.run_cli("evidence", "launch-error:c1")
        self.assertEqual(out.strip(), "parse error")

    def test_who_finds_a_node_from_a_tool_use_id_and_from_a_moment(self):
        _, out, _ = self.run_cli("who", "--tool-use-id", "toolu_9")
        self.assertIn("NODE wf:wf_x kind=workflow", out)
        self.assertIn("launched_by=main", out)
        _, out, _ = self.run_cli("who", "--at", "2026-01-01T10:01")
        self.assertIn("RUNNING attempt=1 workflow=1", out)
        self.assertIn("NODE wf:wf_x", out)
        code, _, err = self.run_cli("who", "--at", "2030-01-01T00:00")
        self.assertEqual(code, 1)

    def test_who_uuid_goes_from_a_main_log_record_to_the_node_it_launched(self):
        _, out, _ = self.run_cli("who", "--uuid", "11111111-0000-0000-0000-000000000001")
        self.assertIn("EVENT 2026-01-01T10:00:00.100 main agent=- assistant turn=1 tools=Workflow", out)
        self.assertIn("NODE wf:wf_x kind=workflow relation=launched", out)

    def test_who_uuid_prefix_and_notification_reference(self):
        _, out, _ = self.run_cli("who", "--uuid", "11111111-0000-0000-0000-000000000002")
        self.assertIn("ref_task_id=tk1", out)
        self.assertIn("NODE wf:wf_x kind=workflow relation=refers_to", out)
        _, out, _ = self.run_cli("who", "--uuid", "22222222-0000-0000-0000-000000000002")
        self.assertIn("NODE attempt:a1111111 kind=attempt relation=in", out)
        code, _, err = self.run_cli("who", "--uuid", "22222222-0000")      # shared by three events
        self.assertEqual(code, 1)
        self.assertIn("prefix of several records", err)

    def test_events_filters(self):
        _, out, _ = self.run_cli("events", "--agent", "a1111111")
        self.assertEqual(len(out.splitlines()), 3)
        _, out, _ = self.run_cli("events", "--tool", "Bash")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("tools=Bash", out)
        _, out, _ = self.run_cli("events", "--errors")
        self.assertIn("results=1", out)
        _, out, _ = self.run_cli("events", "--interrupts")
        self.assertEqual(len(out.splitlines()), 1)
        _, out, _ = self.run_cli("events", "--after", "2026-01-01T10:01", "--before", "2026-01-01T10:10", "--limit", "5")
        self.assertEqual(len(out.splitlines()), 1)
        code, _, _ = self.run_cli("events", "--tool", "Nothing")
        self.assertEqual(code, 1)

    def test_record_reads_the_full_record_by_uuid(self):
        _, out, _ = self.run_cli("record", "11111111-0000-0000-0000-000000000001", "--raw")
        self.assertEqual(json.loads(out)["message"]["content"][0]["name"], "Workflow")
        code, _, err = self.run_cli("record", "99999999-0000-0000-0000-000000000000")
        self.assertEqual(code, 1)
        self.assertIn("no event with uuid", err)

    def test_sql_joins_nodes_and_events(self):
        _, out, _ = self.run_cli("sql", "select n.id, count(*) c from nodes n join events e on e.agent_id = n.agent_id "
                                        "where n.kind = 'attempt' group by 1")
        self.assertEqual(out.splitlines()[1:], ["attempt:a1111111\t3"])

    def test_sql_is_read_only(self):
        code, out, _ = self.run_cli("sql", "select count(*) n from nodes where kind='attempt'")
        self.assertEqual(out.splitlines(), ["n", "2"])
        code, _, err = self.run_cli("sql", "delete from nodes")
        self.assertEqual(code, 1)
        self.assertIn("readonly", err)

    def test_errors_are_plain(self):
        code, _, err = self.run_cli("show", "nothing-like-this")
        self.assertEqual((code, err.strip()), (1, "error: no node matches: nothing-like-this"))
        code, _, err = self.run_cli("evidence", "main")
        self.assertIn("no records of its own", err)


if __name__ == "__main__":
    unittest.main()
