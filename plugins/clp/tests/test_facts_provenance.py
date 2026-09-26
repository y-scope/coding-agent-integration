"""Provenance markers and check commands in the two facts files.

Run: python3 -m unittest discover -s plugins/clp/tests

What these cover: a facts file distinguishes four kinds of claim, and the two
that are checkable each carry the one command that reproduces them. The failure
these guard against is a check that is printed but does not work -- a command a
reader copies, runs, and gets a different number from, which makes them conclude
the figure is wrong. So the session-facts test runs every catalog check the file
emitted against the very catalog it measured, and the insights test runs the
template check against the store it names.
"""

import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin")
sys.path.insert(0, os.path.join(BIN, "lib"))
import bundle  # noqa: E402
import session_facts  # noqa: E402
import session_score  # noqa: E402

MARKERS = (session_facts.MEASURED, session_facts.DERIVED,
           session_facts.INFERENCE, session_facts.DOMAIN)

# A bullet that states no figure takes no marker: it says where a number came
# from, or that there is no number to have. Everything else must be marked.
ORIENTATION = ("UNAVAILABLE", "unavailable", "not applicable", "not derivable", "Not zero",
               "Main-log archive used", "no attempt or agent node", "no workflow phase has units")


def check_commands(text):
    """The commands the Verification section printed, in order."""
    return [line.split("- Check: `", 1)[1].rstrip("`")
            for line in text.splitlines() if line.strip().startswith("- Check: `")]


def figure_lines(text, heading):
    """The top-level bullets under one `## heading` section.

    A bullet that only introduces a table is left out: the table states the
    figures and marks them in its own column headings.
    """
    lines = text.splitlines()
    out, inside = [], False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.startswith(f"## {heading}")
            continue
        if not inside or not line.startswith("- "):
            continue
        following = [l for l in lines[i + 1:i + 3] if l.strip()]
        if following and following[0].startswith("|"):
            continue
        out.append(line)
    return out


class SessionFactsProvenance(unittest.TestCase):
    """clp session facts against a hand-made catalog, checks included."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "bundle")
        os.makedirs(os.path.join(self.dir, "archives"))
        db = sqlite3.connect(os.path.join(self.dir, "catalog.sqlite"))
        db.executescript(bundle.SCHEMA)
        db.executemany("INSERT INTO bundle VALUES(?,?)",
                       [("layout", str(bundle.LAYOUT)), ("session_id", "s1"),
                        ("built_at", "2026-01-01T12:00:00Z"), ("cwd", "/w")])
        db.executemany("INSERT INTO archives VALUES(?,?,?)",
                       [("arch-main", "main", 3), ("arch-wf", "workflow-agent", 2)])
        db.execute("INSERT INTO sources(path, kind) VALUES('a.jsonl', 'main')")
        cols = ("id,kind,label,agent_id,run_id,instance,phase,start,end,status,cause,attempts,"
                "tokens_input,attrs")
        rows = [
            ("attempt:a1", "attempt", None, "a1", "wf_x", "wf:wf_x", None,
             "2026-01-01T10:00:00", "2026-01-01T10:02:00", "stalled-retried", "stall", None, 400, "{}"),
            ("attempt:a2", "attempt", None, "a2", "wf_x", "wf:wf_x", None,
             "2026-01-01T10:02:00", "2026-01-01T10:04:00", "ok", None, None, 600, "{}"),
            ("agent:g1", "agent", "helper", "g1", None, None, None,
             "2026-01-01T10:01:00", "2026-01-01T10:03:00", "completed", None, None, 200, "{}"),
            ("unit:wf_x:k1", "unit", "one", None, "wf_x", None, "build",
             "2026-01-01T10:00:00", "2026-01-01T10:04:00", None, None, 2, None, '{"state": "done"}'),
            ("unit:wf_x:k2", "unit", "two", None, "wf_x", None, "verify",
             "2026-01-01T10:01:00", "2026-01-01T10:05:00", None, None, 1, None, '{"state": "done"}'),
            ("wf:wf_x", "workflow", "check", None, "wf_x", "wf:wf_x", None,
             "2026-01-01T10:00:00", "2026-01-01T10:05:00", "completed", None, None, None, "{}"),
            ("run:wf_x", "run", "check", None, "wf_x", None, None,
             "2026-01-01T10:00:00", "2026-01-01T10:05:00", "completed", None, None, 1000, "{}"),
            ("launch-error:c1", "launch_error", "rejected", None, None, None, None,
             "2026-01-01T09:00:00", "2026-01-01T09:00:00", None, None, None, None,
             '{"error": "Invalid workflow script: Script parse error"}'),
        ]
        db.executemany(f"INSERT INTO nodes({cols}) VALUES({','.join('?' * 14)})", rows)
        db.executemany("INSERT INTO edges VALUES(?,?,?,?,?,?,?)", [
            ("unit:wf_x:k1", "attempt:a1", "contains", None, None, 0, None),
            ("unit:wf_x:k1", "attempt:a2", "contains", None, None, 0, None),
            ("main", "wf:wf_x", "launch", "2026-01-01T10:00:00", "Workflow", 0, "toolu_1"),
        ])
        events = [  # uuid, kind, agent_id, ts, type, turn, human, interrupt, message_id, tokens_*
            ("u1", "main", None, "2026-01-01T10:00:00.000", "user", 1, 1, 0, None, None, None, None),
            ("u2", "main", None, "2026-01-01T10:00:01.000", "assistant", 1, 0, 0, "m1", 500, 40, 0),
            ("u3", "main", None, "2026-01-01T10:04:00.000", "user", 1, 0, 1, None, None, None, None),
            ("u4", "workflow-agent", "a1", "2026-01-01T10:00:02.000", "assistant", None, 0, 0,
             "m1", 500, 40, 0),
            ("u5", "workflow-agent", "a1", "2026-01-01T10:00:03.000", "assistant", None, 0, 0,
             "m2", 700, 60, 0),
        ]
        db.executemany("INSERT INTO events(uuid,kind,agent_id,ts,type,turn,human,interrupt,"
                       "message_id,tokens_input,tokens_output,tokens_cache_read) "
                       f"VALUES({','.join('?' * 12)})", events)
        ids = {u: i for u, i in db.execute("SELECT uuid, id FROM events")}
        db.executemany("INSERT INTO event_tools(event, tool_use_id, role, name, is_error, file_hash) "
                       "VALUES(?,?,?,?,?,?)", [
            (ids["u2"], "t1", "use", "Bash", None, None),
            (ids["u3"], "t1", "result", None, 1, None),
            (ids["u4"], "t2", "use", "Write", None, "h1"),
            (ids["u4"], "t3", "use", "AskUserQuestion", None, None),
            (ids["u5"], "t2", "result", None, 0, None),
            (ids["u5"], "t3", "result", None, 0, None),
        ])
        db.executemany("INSERT INTO actions(uuid, action, failed, confirmed, tests_passed, tests_failed) "
                       "VALUES(?,?,?,?,?,?)", [
            ("v1", "commit", 0, 1, None, None),
            ("v2", "test", 0, None, 9, 1),
        ])
        db.execute("INSERT INTO file_versions(turn, path, version, backup) VALUES(1,'f.py',1,'b')")
        db.commit()
        db.close()
        self.text, self.stdout = self.run_facts("--axes")

    def run_facts(self, *extra):
        out = os.path.join(self.tmp.name, "facts.md")
        p = subprocess.run([os.path.join(BIN, "clp"), "session", "facts", "--bundle", self.dir,
                            "--out", out, *extra], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(out, "r", encoding="utf-8") as fh:
            return fh.read(), p.stdout

    # -- the markers themselves

    def test_the_file_explains_its_own_markers(self):
        """A reader who opens the file cold gets the legend, not a brief."""
        for marker in MARKERS:
            self.assertIn(f"`{marker}`", self.text, f"{marker} is not explained in the header")
        self.assertIn("do not overlap", self.text)
        self.assertIn("percentage in parentheses is always derived", self.text)

    def test_every_figure_line_of_the_measured_sections_carries_a_tier(self):
        """Not a sample: every top-level bullet that states a figure is marked."""
        checked = 0
        for heading in ("1. Session", "2. Reliability", "3. Cost", "4. Time", "5. Outcomes",
                        "6. Harness faults", "7. Human loop", "8. Rework"):
            for line in figure_lines(self.text, heading):
                body = line[2:]
                if body.startswith("**") or any(p in body for p in ORIENTATION):
                    continue    # a caveat or an absence, not a figure
                self.assertTrue(any(body.startswith(m) for m in MARKERS),
                                f"unmarked figure under {heading}: {line[:90]}")
                checked += 1
        self.assertGreater(checked, 20, "almost nothing was inspected")

    def test_all_four_tiers_are_used(self):
        """An inference or a domain claim dressed as a measurement is the whole
        failure this is here to prevent, so each tier has to actually appear."""
        for marker in MARKERS:
            self.assertIn(f"- {marker} ", self.text.replace("  - ", "- "),
                          f"{marker} is explained but never used")

    def test_a_table_puts_its_markers_in_the_column_headings(self):
        """A marker column would push the figures off a narrow screen, so a table
        marks its columns instead of its rows."""
        self.assertIn(f"| Input {session_facts.MEASURED} |", self.text)
        self.assertIn(f"| Share of units {session_facts.DERIVED} |", self.text)

    # -- the checks

    def test_every_check_command_is_a_well_formed_single_command(self):
        commands = check_commands(self.text)
        self.assertTrue(commands, "the Verification section printed no commands")
        for command in commands:
            words = shlex.split(command)          # raises on unbalanced quoting
            self.assertTrue(words, command)
            self.assertNotIn("/", words[0],
                             f"the wrapper is named by path, not basename: {command[:80]}")
            self.assertIn(words[0], ("clp", "python3", "grep"), command[:80])
            if words[0] == "clp":
                self.assertIn(words[1], ("bundle", "session", "search", "schema"), command[:80])

    def test_no_check_names_a_home_directory_or_a_clp_s_build(self):
        home = os.path.expanduser("~")
        self.assertNotIn(home, self.text)
        self.assertNotIn("clp-s ", self.text)

    def test_every_catalog_check_runs_and_prints_a_row(self):
        """The point of a check is that it works. Each one is run here against the
        catalog it was measured from."""
        ran = 0
        for command in dict.fromkeys(check_commands(self.text)):
            words = shlex.split(command)
            if words[:2] != ["clp", "bundle"] or "sql" not in words:
                continue
            words[0] = os.path.join(BIN, "clp")
            p = subprocess.run(words, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"{command[:120]}\n{p.stderr[:300]}")
            self.assertGreaterEqual(len(p.stdout.strip().splitlines()), 2,
                                    f"no data row from {command[:120]}")
            ran += 1
        self.assertGreater(ran, 10, "almost nothing was actually verified")

    def test_a_figure_no_single_command_reproduces_says_so(self):
        self.assertIn("**Not checkable in one command**", self.text)
        self.assertIn("figures above are reproduced by a single command", self.text)

    def test_no_figure_is_declared_unverifiable_without_a_reason(self):
        """Claiming a figure cannot be checked while giving no reason is worse than
        printing no check at all, so a placeholder must never reach a reader."""
        self.assertNotIn("unstated", self.text)
        self.assertNotIn("unstated", self.stdout)
        seen = 0
        for line in self.text.splitlines():
            if "**Not checkable in one command**:" in line:
                reason = line.split("**Not checkable in one command**:", 1)[1].strip()
                self.assertGreater(len(reason), 40, f"a reason too short to be one: {line[:90]}")
                self.assertTrue(reason.endswith("."), line[:90])
                seen += 1
        self.assertGreater(seen, 0, "no unverifiable figure was inspected")

    def test_the_headline_derived_figures_state_a_formula_and_why_it_answers_the_question(self):
        for name in ("Attempt ok rate", "Assertion pass rate"):
            self.assertIn(f"**{name}** `{session_facts.DERIVED}`", self.text)
        self.assertIn("It answers ", self.text)
        self.assertIn("  - Trap: ", self.text)

    def test_the_parallelism_trap_travels_with_the_summed_minutes(self):
        self.assertIn("summed attempt minutes are not elapsed time", self.text)

    def test_the_attribution_trap_travels_with_the_confirmed_commits(self):
        """clp bundle repo needs a repository, so the outcome section is driven
        here with an answer of the shape it returns."""
        repo = {"repo": "r", "commits_in_span": 4, "unattributed": {},
                "commits": [{"match": "exact"}, {"match": "time+subject"},
                            {"match": "time"}, {"match": "none"}],
                "prs": [{"prs": ["https://example.invalid/pr/1"]}]}
        out, F = [], {"checks": [], "bundle": self.dir}
        cat = session_facts.Catalog(os.path.join(self.dir, "catalog.sqlite"))
        session_facts.section_outcomes(cat, None, "not run", repo, "clp bundle repo --json",
                                      out.append, F, {}, [], 10)
        text = "\n".join(out)
        self.assertIn(f"- {session_facts.MEASURED} **Commits the repository confirms: 3 of 4", text)
        traps = [c["trap"] for c in F["checks"] if c["name"] == "Commits the repository confirms"]
        self.assertEqual(len(traps), 1, [c["name"] for c in F["checks"]])
        self.assertIn("attributed, not proven", traps[0])
        self.assertIn("1 of 3", traps[0])       # only the exact match is proven
        commands = {c["name"]: c["command"] for c in F["checks"]}
        self.assertEqual(commands["PRs GitHub confirms"], f"clp bundle {self.dir} repo --json")

    # -- the axes

    def test_every_axis_value_is_marked_derived(self):
        """An axis is always a ratio over measured counts, so the marker goes on
        the value itself rather than into a column of its own."""
        seen = 0
        for line in self.text.splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 6 or not re.match(r"^[A-D][1-5] ", cells[0]):
                continue
            if cells[3] == "n/a":
                continue
            self.assertTrue(cells[3].startswith(session_facts.DERIVED),
                            f"axis value is unmarked: {line[:80]}")
            seen += 1
        self.assertGreater(seen, 5, "no axis rows were inspected")

    def test_every_measured_axis_says_why_its_ratio_answers_its_question(self):
        rows = [l for l in self.stdout.splitlines() if l.startswith("AXIS ") and "value=n/a" not in l]
        self.assertTrue(rows)
        for line in rows:
            self.assertIn("the right ratio for ", line, line[:100])
            self.assertIn(" because ", line, line[:100])

    def test_every_measured_axis_has_a_check_or_says_why_it_cannot(self):
        measured = [l.split()[1] for l in self.stdout.splitlines()
                    if l.startswith("AXIS ") and "value=n/a" not in l]
        provenance = session_score.parse_axes_output(self.stdout)["checks"]
        self.assertTrue(measured)
        for axis in measured:
            self.assertIn(axis, provenance, f"{axis} has no provenance line")
            self.assertTrue(provenance[axis]["detail"].strip(), axis)

    def test_an_axis_check_line_is_not_mistaken_for_a_headline_figure(self):
        """The command holds `=` signs, so the key parser must never see it."""
        parsed = session_score.parse_axes_output(self.stdout)
        for key in parsed["keys"]:
            self.assertFalse(key.startswith("AXIS"), key)
        self.assertIn("A1", parsed["checks"])
        self.assertTrue(parsed["checks"]["A1"]["single_command"])

    def test_the_score_carries_each_axis_check_into_its_json(self):
        axes_file = os.path.join(self.tmp.name, "axes.txt")
        with open(axes_file, "w", encoding="utf-8") as fh:
            fh.write(self.stdout)
        out = os.path.join(self.tmp.name, "score.json")
        p = subprocess.run([os.path.join(BIN, "clp"), "session", "score",
                            "--axes-file", axes_file, "--out", out], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(out, "r", encoding="utf-8") as fh:
            scored = json.load(fh)
        self.assertTrue(scored["axes"], "nothing was scored")
        for axis in scored["axes"]:
            self.assertTrue(axis.get("check") or axis.get("check_unavailable"),
                            f"{axis['id']} was scored without its check")

    def test_without_axes_the_verification_section_is_still_written(self):
        text, _ = self.run_facts()
        self.assertIn("## 10. Verification", text)
        self.assertNotIn("Axis A1", text)


class InsightsFactsProvenance(unittest.TestCase):
    """clp facts: markers, and the KQL behind each count."""

    TOTAL = 100

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = self.tmp.name
        self.out = os.path.join(self.tmp.name, "facts.md")

    def entry(self, index, value, count, negated=False, samples=None, origin="baseline"):
        match = ({"not": {"field": "severity", "eq": value}} if negated
                 else {"field": "severity", "eq": value})
        kql = f'NOT severity:"{value}"' if negated else f'severity:"{value}"'
        row = {"index": index, "label": f"Baseline: severity={value}", "method": "count",
               "origin": origin, "status": "ok", "total_records": self.TOTAL, "match": match,
               "kql": kql, "count": count,
               # The pool records an absolute path; the facts must not repeat it.
               "command": f"/opt/somewhere/plugins/clp/bin/clp search --count "
                          f"{self.archive} '{kql}'"}
        if samples:
            row.update(origin="follow-up of 2", samples=[json.dumps(s) for s in samples],
                       method="project+grep",
                       command=f"/opt/somewhere/plugins/clp/bin/clp search "
                               f"--projection timestamp,severity,message {self.archive} '{kql}'")
        return row

    def write(self, name, entries):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def run_facts(self, entries, *extra):
        baseline = self.write("baseline.ndjson", entries)
        p = subprocess.run([os.path.join(BIN, "clp"), "facts",
                            "--schema-json", '{"severity":"severity","message":"msg"}',
                            "--archive-dir", self.archive,
                            "--baseline-results-file", baseline,
                            "--results-file", self.write("plan.ndjson", []),
                            "--category-totals", "none", "--top-templates-file", "none",
                            "--focus-file", "none", "--out", self.out, *extra],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.out, "r", encoding="utf-8") as fh:
            return fh.read()

    def split(self):
        """A severity split whose residual is fetched, as a real run produces."""
        return [
            self.entry(1, "INFO", 80),
            self.entry(2, "INFO", 20, negated=True),
            self.entry(3, "INFO", 20, negated=True,
                       samples=[{"severity": "WARNING", "msg": "slow thing 1"}] * 12
                               + [{"severity": "ERROR", "msg": "broke 2"}] * 8),
        ]

    def test_the_file_explains_its_own_markers_and_uses_them(self):
        text = self.run_facts(self.split())
        for marker in MARKERS:
            self.assertIn(f"`{marker}`", text, f"{marker} is not explained")
        self.assertIn(f"- {session_facts.MEASURED} INFO: 80", text)
        self.assertIn(f"- {session_facts.MEASURED} everything other than INFO: 20", text)
        self.assertIn(f"- {session_facts.DERIVED} check: the lines above sum to", text)

    def test_each_count_cites_the_query_that_produced_it(self):
        text = self.run_facts(self.split())
        self.assertIn("[baseline #1]", text)
        self.assertIn("[baseline #2]", text)

    def test_every_check_drops_the_wrappers_install_path(self):
        text = self.run_facts(self.split())
        commands = check_commands(text)
        self.assertTrue(commands)
        self.assertNotIn("/opt/somewhere", text)
        for command in commands:
            words = shlex.split(command)
            self.assertNotIn("/", words[0], command[:80])

    def test_the_check_for_a_count_is_the_query_that_ran(self):
        text = self.run_facts(self.split())
        self.assertIn("clp search --count %s 'severity:\"INFO\"'" % self.archive, text)

    def test_the_per_value_split_of_the_fetched_records_is_checked_by_filtering_them(self):
        text = self.run_facts(self.split())
        self.assertIn("WARNING among the fetched records** `[M]` = 12", text)
        self.assertIn("""| grep -c '"severity":"WARNING"'""", text)

    def test_a_count_whose_command_was_not_recorded_is_rebuilt_from_its_kql(self):
        """An older pool recorded the KQL but not the command. That is the same
        command spelled out, so the check is rebuilt rather than left absent -- the
        alternative was a figure that claimed to be uncheckable for no reason."""
        entries = [dict(self.entry(1, "INFO", 80)), dict(self.entry(2, "INFO", 20, negated=True))]
        for entry in entries:
            del entry["command"]
        text = self.run_facts(entries)
        self.assertIn(f"clp search --count {self.archive} 'severity:\"INFO\"'", text)
        self.assertNotIn("Not checkable in one command", text)

    def test_a_count_with_neither_a_command_nor_a_kql_says_which_is_missing(self):
        entry = dict(self.entry(1, "INFO", 80))
        del entry["command"]
        entry["kql"] = ""
        text = self.run_facts([entry, self.entry(2, "INFO", 20, negated=True)])
        self.assertIn("recorded neither a command nor the KQL", text)
        self.assertNotIn("unstated", text)

    def test_no_figure_is_declared_unverifiable_without_a_reason(self):
        text = self.run_facts(self.split())
        self.assertNotIn("unstated", text)
        for line in text.splitlines():
            if "**Not checkable in one command**:" in line:
                reason = line.split("**Not checkable in one command**:", 1)[1].strip()
                self.assertGreater(len(reason), 40, f"a reason too short to be one: {line[:90]}")

    def test_every_section_marks_its_figures(self):
        """Not only the sections near the top: a category table, a template list, a
        semantic entry and a query flag each have to carry their tier too."""
        cats = os.path.join(self.tmp.name, "cats.json")
        with open(cats, "w", encoding="utf-8") as fh:
            json.dump({"errors": {"templates": 2, "records": 60, "priority": "high",
                                  "why": "these are failures"}}, fh)
        freqs = os.path.join(self.tmp.name, "freqs.ndjson")
        with open(freqs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"log_shape": "connection reset", "count": 50}) + "\n")
        semantic = dict(self.entry(4, "INFO", 7, negated=True))
        semantic.update(method="semantic", label="semantic scan", non_selective=True)
        text = self.run_facts(self.split() + [semantic],
                             "--category-totals", cats, "--freqs-file", freqs)
        section, unmarked = None, []
        for line in text.splitlines():
            if line.startswith("## "):
                section = line[3:]
            if line.startswith("- ") and section and not section.startswith("Verification"):
                if not any(line[2:].startswith(m) for m in MARKERS):
                    unmarked.append(f"{section[:24]} :: {line[:70]}")
        self.assertEqual(unmarked, [], "unmarked figures: " + "; ".join(unmarked))
        for heading in ("Categories", "Top 1 templates", "Semantic entries", "Query flags"):
            self.assertIn(f"## {heading}", text)

    def test_a_figure_computed_here_rather_than_by_a_query_says_so(self):
        """The shape count is masking plus grouping done in this script, and the
        records-with-no-severity figure is a subtraction. Neither is one query."""
        text = self.run_facts(self.split())
        self.assertIn("**Not checkable in one command**", text)
        self.assertIn("the masking and the grouping happen in this script", text)

    def test_a_stored_template_count_is_checked_against_the_store_that_holds_it(self):
        """A template is not a KQL filter, so the check reads the store -- and the
        command has to actually find the line it claims to."""
        freqs = os.path.join(self.tmp.name, "freqs.ndjson")
        with open(freqs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"log_shape": "connection reset by peer", "count": 41}) + "\n")
            fh.write(json.dumps({"log_shape": "slow query", "count": 7}) + "\n")
        text = self.run_facts(self.split(), "--freqs-file", freqs)
        self.assertIn("The single most frequent template** `[M]` = 41", text)
        commands = [c for c in check_commands(text) if c.startswith("grep ")]
        self.assertEqual(len(commands), 1, commands)
        p = subprocess.run(shlex.split(commands[0]), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["count"], 41)

    def test_the_time_span_is_derived_and_its_check_prints_the_two_timestamps(self):
        with open(os.path.join(self.archive, ".yscope-clp-archive.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"timestampKey": "timestamp",
                       "timeRange": {"beginMs": 1700000000000, "endMs": 1700086400000}}, fh)
        text = self.run_facts(self.split())
        self.assertIn(f"- {session_facts.DERIVED} Time span:", text)
        command = [c for c in check_commands(text) if c.startswith("python3 ")][0]
        p = subprocess.run(shlex.split(command), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("2023-11-14", p.stdout)
        self.assertIn("2023-11-15", p.stdout)


if __name__ == "__main__":
    unittest.main()
