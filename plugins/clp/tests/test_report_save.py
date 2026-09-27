"""clp report save: what the saver lets through, and what it must keep escaping.

A report quotes log text and command output, so anything that is not the collapsed
reference's own tags has to arrive escaped. The passthrough exists for one thing:
a `<details>` section that a reader opens by choice, and the script that opens it
when a claim's link lands inside.
"""

import os
import sys
import unittest

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, os.path.join(BIN, "lib"))
import report_save as R  # noqa: E402


class DetailsPassthrough(unittest.TestCase):
    def test_a_details_block_survives_as_markup(self):
        md = "## Reference\n\n<details>\n<summary>Open to check a figure</summary>\n\n### R1 Attempts\n\n18 of 18 ok.\n\n</details>\n"
        html = R.build_page(md, [])
        self.assertIn("<details>", html)
        self.assertIn("</details>", html)
        self.assertIn("<summary>Open to check a figure</summary>", html)
        self.assertIn('<h3 id="r1-attempts">', html)

    def test_html_anywhere_else_is_escaped(self):
        md = "The log holds <script>alert(1)</script> and an inline <details> tag.\n"
        html = R.build_page(md, [])
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertEqual(html.count("<details>"), 0)
        self.assertIn("&lt;details&gt;", html)

    def test_a_link_into_the_reference_opens_it(self):
        html = R.build_page("See [R1](#r1-attempts).\n", [])
        self.assertIn('href="#r1-attempts"', html)
        self.assertIn("closest('details')", html)

    def test_the_page_and_the_fragment_carry_the_script(self):
        md = "# Report\n\nText.\n"
        self.assertIn("closest('details')", R.build_page(md, []))
        self.assertIn("closest('details')", R.build_page(md, [], fragment=True))
        self.assertNotIn("<!doctype html>", R.build_page(md, [], fragment=True))


class NamesFromTheTitle(unittest.TestCase):
    """The saved file and the browser tab are named after what the report is about."""

    def test_a_log_report_is_named_after_its_subject(self):
        md = "# Log insights: cockroach.node1.log\n\nText.\n"
        self.assertRegex(R.default_name(md, None), r"^log-insights-cockroach\.node1\.log-\d{8}-\d{4}$")
        self.assertIn("<title>cockroach.node1.log log insights</title>", R.build_page(md, []))

    def test_a_session_report_is_named_after_its_session(self):
        md = "# Session 61e8bbf7, 2026-09-01 to 2026-09-16\n\nText.\n"
        self.assertRegex(R.default_name(md, None), r"^log-insights-session-61e8bbf7-\d{8}-\d{4}$")
        self.assertIn("<title>Session 61e8bbf7, 2026-09-01 to 2026-09-16</title>", R.build_page(md, []))

    def test_an_explicit_name_wins(self):
        self.assertRegex(R.default_name("# Session x, y\n", "mine"), r"^log-insights-mine-")


if __name__ == "__main__":
    unittest.main()
