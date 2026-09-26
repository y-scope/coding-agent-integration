"""clp-bundle-review: ranking and error grouping on hand-made inputs, and the command end to end on a
synthetic session with a stub clp-s (see test_bundle_build)."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin", "lib"))
sys.path.insert(0, os.path.dirname(__file__))
import bundle as B  # noqa: E402
import bundle_review as R  # noqa: E402
from test_bundle_build import BuildTest, SID, make_session  # noqa: E402


def session(name, **values):
    base = {k: 0 for k in R.SQL}
    base.update({k: 0 for k in R.KQL})
    base.update(identical_retries=0, redundant_reads=0)
    base.update(session=name, project="p", examples={}, e2e_s=0, human_s=0, idle_s=0, hours=0)
    base.update(values)
    return base


class Rank(unittest.TestCase):
    def test_zero_tolerance_lists_every_hit_with_examples(self):
        s = [session("a", nul_bytes=10, examples={"nul_bytes": ["x.jsonl"]}), session("b")]
        z = {x["signal"]: x for x in R.rank(s)["zero"]}
        self.assertEqual((z["nul_bytes"]["sessions"], z["nul_bytes"]["total"]), (1, 10))
        self.assertEqual(z["nul_bytes"]["hits"][0]["examples"], ["x.jsonl"])
        self.assertEqual(z["launch_errors"]["sessions"], 0)

    def test_rates_need_enough_active_sessions_and_flag_above_p90(self):
        s = [session(f"s{i}", tool_calls=100, tool_errors=i) for i in range(10)]
        rate = next(r for r in R.rank(s)["rates"] if r["signal"] == "tool error rate")
        self.assertEqual(rate["rated"], 10)
        self.assertEqual([a["session"] for a in rate["above"]], ["s9"])
        few = next(r for r in R.rank(s[:3])["rates"] if r["signal"] == "tool error rate")
        self.assertIsNone(few["median"])                      # fewer than MIN_RATED sessions: no baseline


class CallPatterns(unittest.TestCase):
    """identical_retries and redundant_reads must not fire when anything could explain the repeat."""

    def catalog(self, calls):
        import sqlite3
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(B.SCHEMA)
        for i, (agent, ts, name, inp, fh, rh, failed) in enumerate(calls):
            cur = db.execute("INSERT INTO events(uuid, kind, pos, agent_id, ts, type) VALUES(?,?,?,?,?,?)",
                             (f"u{i}", "workflow-agent" if agent else "main", i, agent, f"2026-01-01T10:00:{i:02d}.000", "assistant"))
            db.execute("INSERT INTO event_tools VALUES(?,?,?,?,?,?,?,?)", (cur.lastrowid, f"t{i}", "use", name, None, inp, fh, rh))
            db.execute("INSERT INTO event_tools VALUES(?,?,?,?,?,?,?,?)", (cur.lastrowid, f"t{i}", "result", None, int(failed), None, None, None))
        return db

    def test_an_identical_retry_after_a_failure_counts_and_a_changed_one_does_not(self):
        db = self.catalog([(None, 0, "Bash", "h1", None, None, True), (None, 1, "Bash", "h1", None, None, False),
                           (None, 2, "Bash", "h2", None, None, True), (None, 3, "Bash", "h3", None, None, False)])
        self.assertEqual(R.call_patterns(db)["identical_retries"], (1, ["u1"]))

    def test_a_re_read_counts_only_when_nothing_could_have_changed_the_file(self):
        read = lambda agent: (agent, 0, "Read", "r", "f", "rf", False)
        db = self.catalog([read(None), read(None)])                                         # plain re-read
        self.assertEqual(R.call_patterns(db)["redundant_reads"][0], 1)
        db = self.catalog([read(None), ("a2", 0, "Bash", "b", None, None, False), read(None)])  # a parallel agent's command
        self.assertEqual(R.call_patterns(db)["redundant_reads"][0], 0)
        db = self.catalog([read(None), ("a2", 0, "Edit", "e", "f", None, False), read(None)])   # another agent edited it
        self.assertEqual(R.call_patterns(db)["redundant_reads"][0], 0)
        db = self.catalog([read(None), (None, 0, "Read", "r2", "f", "rg", False), read(None)])  # another range in between
        self.assertEqual(R.call_patterns(db)["redundant_reads"][0], 1)


class Trends(unittest.TestCase):
    def catalog(self, directory, calls):
        """calls: [(timestamp, failed)]"""
        import sqlite3
        os.makedirs(directory)
        db = sqlite3.connect(os.path.join(directory, "catalog.sqlite"))
        db.executescript(B.SCHEMA)
        db.execute("INSERT INTO bundle VALUES('layout', ?)", (str(B.LAYOUT),))
        for i, (ts, failed) in enumerate(calls):
            cur = db.execute("INSERT INTO events(uuid, kind, pos, ts, type) VALUES(?,?,?,?,?)", (f"u{i}", "main", i, ts, "user"))
            db.execute("INSERT INTO event_tools(event, tool_use_id, role, is_error) VALUES(?,?,?,?)",
                       (cur.lastrowid, f"t{i}", "result", int(failed)))
        db.commit()
        db.close()
        return directory

    def test_a_clear_rise_is_flagged_a_thin_period_is_not_and_weeks_are_iso(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 2026-08-24 is the Monday of ISO week 35; 2026-08-31 of week 36; 2026-09-07 of week 37
            a = self.catalog(os.path.join(tmp, "a"), [("2026-08-24T10:00:00.000", i < 5) for i in range(600)]
                             + [("2026-08-31T10:00:00.000", i < 60) for i in range(600)])
            b = self.catalog(os.path.join(tmp, "b"), [("2026-08-30T23:00:00.000", False)] * 100      # Sunday: still week 35
                             + [("2026-09-07T10:00:00.000", True)] * 10)
            rows = [r for r in R.trends([a, b], "week") if r["signal"] == "tool error rate"]
            got = {r["period"]: (r["volume"], r["sessions"], r["enough"], r["change"]) for r in rows}
        self.assertEqual(got["2026-W35"], (700, 2, True, None))
        self.assertEqual(got["2026-W36"], (600, 1, True, "up"))                  # 0.7% -> 10%
        self.assertEqual(got["2026-W37"], (10, 1, False, None))                  # 100% of 10 calls: too little to say


class ErrorGroups(unittest.TestCase):
    def groups(self, templates):
        return {t: {"n": 1, "sessions": {"s"}, "projects": {"p"}, "examples": ["s u"]} for t in templates}

    def test_command_failures_merge_by_shared_beginning_and_end_only(self):
        merged = R.merge_affix(["Error: Exit code <*> fatal: ambiguous argument 'a': unknown revision or path",
                                "Error: Exit code <*> fatal: ambiguous argument 'bb': unknown revision or path",
                                "Error: Exit code <*>"])
        self.assertEqual(sorted(len(m) for m in merged), [1, 2])    # the bare one is not absorbed

    def test_an_unreachable_endpoint_is_an_error(self):
        groups = self.groups(["Error: File has not been read yet. Read it first before writing to it."])

        def unreachable(templates, endpoint, threshold=0.80):   # what the embedding helper does: exit 2
            raise SystemExit(2)
        original, R.merge_embeddings = R.merge_embeddings, unreachable
        try:
            with self.assertRaises(B.BundleError) as caught:
                R.error_groups(groups, "http://127.0.0.1:9")
        finally:
            R.merge_embeddings = original
        self.assertIn("the semantic endpoint http://127.0.0.1:9 failed", str(caught.exception))

    def test_command_failures_alone_need_no_endpoint(self):
        groups = self.groups(["Error: Exit code <*>", "Error: Exit code <*> x"])
        self.assertEqual(sum(g["n"] for g in R.error_groups(groups, "http://127.0.0.1:9")), 2)

    def test_the_default_endpoint_is_the_plugin_s_built_in_one(self):
        self.assertTrue(R.default_endpoint().startswith("https://"))


class Command(BuildTest):
    def test_builds_then_reviews_a_directory(self):
        make_session(self.home)
        wrapper = os.path.join(os.path.dirname(__file__), "..", "bin", "clp-s-search-kql")
        env = {**os.environ, "CLP_S_BIN": self.stub}
        p = subprocess.run([os.path.join(os.path.dirname(__file__), "..", "bin", "clp-bundle-review"), self.out, "--build",
                            "--claude-home", self.home, "--search-wrapper", wrapper], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("REVIEW sessions=1", p.stdout)
        self.assertIn("BUILD built=1", p.stdout)
        self.assertIn("ZERO calls_without_result sessions=0", p.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.out, SID, "catalog.sqlite")))
        again = subprocess.run([os.path.join(os.path.dirname(__file__), "..", "bin", "clp-bundle-review"), self.out, "--build",
                                "--claude-home", self.home, "--search-wrapper", wrapper], capture_output=True, text=True, env=env)
        self.assertIn("BUILD kept=1", again.stdout)                 # unchanged session: not built again


if __name__ == "__main__":
    unittest.main()
