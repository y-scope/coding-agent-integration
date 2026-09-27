"""clp: the query plan's methods, and what happens to an entry that still carries jq.

The `project+jq` method and the per-entry `jq` program were removed when jq stopped
being a dependency: a filter that needs a per-record predicate now says so with
`match` (whose grammar already has gt/gte/lt/lte). A plan that still carries the
old field must be refused with a message that names it, rather than run.
"""

import os
import sys
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, os.path.join(BIN, "lib"))
import kql_build as K  # noqa: E402


def entry(**over):
    base = {"label": "All", "match": {"field": "level", "exists": True}, "method": "count"}
    base.update(over)
    return base


class RemovedJqMethod(unittest.TestCase):
    def test_jq_is_not_a_method(self):
        self.assertNotIn("project+jq", K.METHODS)

    def test_the_old_method_is_refused_by_name(self):
        with self.assertRaises(K.FilterError) as caught:
            K.check_entry(entry(method="project+jq"))
        self.assertIn("project+jq", str(caught.exception))

    def test_an_entry_carrying_a_jq_program_is_refused(self):
        with self.assertRaises(K.FilterError) as caught:
            K.check_entry(entry(jq="select((.attr.durationMillis//0)>100)"))
        self.assertIn("'jq'", str(caught.exception))

    def test_the_reason_points_at_match(self):
        with self.assertRaises(K.FilterError) as caught:
            K.check_entry(entry(jq="."))
        self.assertIn("match", str(caught.exception))


class TheReplacement(unittest.TestCase):
    def test_a_numeric_threshold_is_a_match(self):
        kql = K.check_entry(entry(method="count", match={"field": "attr.durationMillis", "gt": 100}))
        self.assertIn("attr.durationMillis", kql)
        self.assertIn(">", kql)

    def test_the_remaining_methods_are_unchanged(self):
        self.assertEqual(tuple(K.METHODS), ("count", "project+grep", "semantic"))

    def test_grep_is_still_a_string(self):
        with self.assertRaises(K.FilterError):
            K.check_entry(entry(method="project+grep", grep=[]))

    def test_a_plain_entry_still_passes(self):
        self.assertTrue(K.check_entry(entry()))


if __name__ == "__main__":
    unittest.main()
