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
