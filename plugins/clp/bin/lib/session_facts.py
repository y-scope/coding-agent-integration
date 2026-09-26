"""
clp session facts - compute every number of a session-trajectory report in
code, so the report writer only has to put them into words.

This is the trajectory twin of clp facts, and it exists for the
same reason: a writer asked to do arithmetic gets it wrong. In a trial the
analyst put waste at 19% of the input tokens where the measured figure was
9.1%, because it eyeballed a few big numbers instead of dividing one sum by
another. Nothing here needs judgement, so nothing here is left to it. Every
figure a trajectory report can quote is computed below, carries its own
denominator, and is written out as a fact the writer only has to phrase.

Every figure also carries its provenance, because a reader cannot otherwise
tell four different kinds of claim apart: [M] a number counted or summed off
the records, [D] arithmetic over such numbers, [I] a claim about what caused
these records, and [K] a claim about how this kind of system behaves. The first
two are checkable and each one says how; the last two are arguments and are
labelled as arguments. Section 10 lists every headline figure and every axis
with the one command that reproduces it, so a reader who doubts a number does
not have to reconstruct a query to settle it. See PROVENANCE below.

Inputs:
  --bundle DIR      A clp bundle bundle directory: catalog.sqlite, manifest.json
                    and archives/. This is the primary input and the only
                    required one. The catalog is opened read-only.
  --archive DIR     The main log's CLP archive, for clp session turns. When it
                    is omitted it is derived from the catalog's `archives` table:
                    the row with kind='main', at <bundle>/archives/<archive_id>.
  --turns-file F    A clp session turns run captured earlier, parsed instead of
                    running it again. The skill runs turns once and reuses it.
  --axes            Also compute section 9, the scoring inputs: the raw value
                    behind each of the 20 axes, with the components it came from.
                    Off by default, because a report that only needs the facts
                    should not pay for it.
  --check-scale     Validate a scale file instead of trusting it: every axis id
                    this script can emit must be in it, and every ladder must be
                    well-formed and exhaustive. Prints SCALE_OK or one
                    SCALE_PROBLEM= line per defect. On its own it needs no
                    --bundle and measures nothing, because validation reads only
                    the scale and this script's own axis metadata. Alongside
                    --bundle it validates and measures in the same run.
  --scale F         The scale file --check-scale validates
                    (default: ../scoring-scale.json next to this script's plugin).
  --waits N         How many WAIT lines to ask clp session turns for (default 60).
  --top N           How many rows to list in the "top N" tables (default 10).
  --out F           Where to write the facts (default /tmp/clp-session-facts.md).

This script does not score anything. It measures, and stops. Scoring is a
judgement about what a measurement is worth, it differs between customers, and
it belongs in a scale file (scoring-scale.json) that an agent applies - not
buried in Python where nobody can see or override it. So section 9 prints raw
values and the numerator and denominator behind each one, and never a 0-10
number, a group mean or a composite.

Where the numbers come from:
  * The catalog, read with sqlite3 directly. Attempts, agents, workflow runs and
    instances, phases, units, events, tool calls, token usage, actions and file
    changes all live there, so they need no subprocess.
  * clp session turns, for the per-turn time split. Only wall-clock timestamps
    can be added up; the harness's own turn_duration records nest and cannot.
  * clp bundle outcomes and clp bundle repo, for what the session produced and
    which of it the git repository and GitHub actually confirm.

Every subprocess runs under a timeout inside try/except. A section whose input
could not be produced is written as UNAVAILABLE with the reason; the run does
not abort, because the catalog-only sections are still worth having. A bundle
with only a main thread prints "not applicable - single-threaded session" for
the agent and workflow categories rather than a wall of zeros, which reads as a
finding when it is not one.

stdout is KEY=VALUE lines only:
  * the headline figures, one per line, including BUNDLE= and ARCHIVE= so that a
    captured stdout records what it was measured from and can be re-scored later
    without losing its provenance;
  * with --axes, one `AXIS <id> group=<G> value=<bare number|n/a> ...` line per
    axis. `value=` is a bare number with no percent sign and no separators, so a
    scale can be applied to it without parsing prose; the readable form of the
    same measurement is in `components=`;
  * one `ALERT=<category>:<slug> value=<n> threshold=<test>` line per category
    whose headline metric crosses a bad threshold. An alert carries the value and
    the test that fired it, so it is a visible rule and not a hidden judgement.
    Alerts exist only so the skill can order the focus options. They are not
    scores, and the thresholds behind them are in ALERTS below.

Exit codes: 0 ok, 1 the bundle could not be read, 2 the scale file is invalid.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# The sibling tools this module runs live one level up, in bin/.
BIN_DIR = Path(__file__).resolve().parent.parent
SUBPROCESS_TIMEOUT = 3600  # clp session turns scans a whole session archive


# ---------------------------------------------------------------------------
# The axes, and nothing about what a value on them is worth.
#
# This dict says what each axis measures, which group owns it, and in what unit.
# It holds no thresholds: the ladders live in scoring-scale.json, where a
# customer can see and change them, and an agent applies them. Keeping them out
# of here is the point - a score is a judgement, and a judgement that nobody can
# find is a judgement nobody can argue with.
#
# `cohort` marks a measurement that only means something against comparable
# sessions, never as an absolute.
# ---------------------------------------------------------------------------

GROUP_NAMES = {"A": "Platform", "B": "Behavior", "C": "Outcome", "D": "Cost"}

AXES = {
    # -- Group A: the platform's own behaviour. Owner: infra / gateway.
    "A1": {"group": "A", "label": "execution reliability", "unit": "share",
           "measures": "share of attempts and agents reaching a good terminal state"},
    "A2": {"group": "A", "label": "provider stability", "unit": "errors_per_1k_responses",
           "measures": "API and timeout failures per 1,000 model responses"},
    "A3": {"group": "A", "label": "cache efficiency", "unit": "share",
           "measures": "cache_read tokens as a share of input tokens"},
    "A4": {"group": "A", "label": "config correctness", "unit": "share",
           "measures": "share of launches the runtime rejected for a config or syntax fault"},
    "A5": {"group": "A", "label": "runtime honesty", "unit": "share",
           "measures": "share of workflow instances whose reported status matches what their attempts did"},

    # -- Group B: how the agent behaved. Owner: model / harness.
    "B1": {"group": "B", "label": "tool proficiency", "unit": "share",
           "measures": "share of completed tool calls that returned an error"},
    "B2": {"group": "B", "label": "tool selection fitness", "unit": "share",
           "measures": "Bash share of tool calls; the components say how often a dedicated search "
                       "tool was used instead, which a scorer may penalise separately"},
    "B3": {"group": "B", "label": "rework rate", "unit": "share",
           "measures": "share of attempts that reached no kept result, including work discarded "
                       "by a cancellation"},
    "B4": {"group": "B", "label": "context discipline", "unit": "compactions_per_hour",
           "measures": "context compactions per active hour"},
    "B5": {"group": "B", "label": "orchestration efficiency", "unit": "share",
           "measures": "share of agent-minutes spent on work that survived"},

    # -- Group C: what came out. Owner: shared.
    "C1": {"group": "C", "label": "delivery throughput", "unit": "artifacts_per_model_hour",
           "measures": "repository-confirmed artifacts per model-hour", "cohort": True},
    "C2": {"group": "C", "label": "delivery integrity", "unit": "share",
           "measures": "share of repository-confirmed commits matched exactly, by a sha the "
                       "command itself printed"},
    "C3": {"group": "C", "label": "verification rigor", "unit": "share",
           "measures": "assertion pass rate; the components say how many test commands ran against "
                       "how many artifacts, which a scorer may penalise separately"},
    "C4": {"group": "C", "label": "autonomy", "unit": "share",
           "measures": "share of end-to-end time not spent waiting on a person or on nothing"},
    "C5": {"group": "C", "label": "self-recovery", "unit": "share",
           "measures": "share of units that hit a bad attempt and still finished, with no person involved"},

    # -- Group D: what it cost. Owner: infra + orchestration.
    "D1": {"group": "D", "label": "cost per confirmed outcome", "unit": "tokens_per_artifact",
           "measures": "input tokens per repository-confirmed artifact"},
    "D2": {"group": "D", "label": "waste ratio", "unit": "share",
           "measures": "wasted input tokens as a share of all input tokens"},
    # D3 is deliberately the same measurement as A3. Cache misses are both a
    # platform fault (nobody asked for them) and a cost lever (they are most of
    # the bill), and those are different owners, so the duplication is the point:
    # it has to appear on the platform's card and on the bill payer's card.
    "D3": {"group": "D", "label": "cache recovery", "unit": "share",
           "measures": "cache_read tokens as a share of input tokens (the same measurement as A3, "
                       "deliberately: it is both a platform fault and a cost lever)"},
    "D4": {"group": "D", "label": "model-mix fitness", "unit": "share",
           "measures": "share of input tokens on the cheapest model, weighted by one minus its stall rate"},
    "D5": {"group": "D", "label": "cost concentration", "unit": "share",
           "measures": "the largest single turn's or single run's share of input tokens, "
                       "whichever is larger"},
}

# ---------------------------------------------------------------------------
# Alert thresholds.
#
# An alert is not a score. It is one visible rule that says "this category is
# worth looking at first", and the skill uses the set of them to order the focus
# options it offers. Each alert prints the value and the test that fired it, so
# the reader can disagree with the rule instead of having to trust it.
# `test` is the comparison, as text, exactly as it is printed.
# ---------------------------------------------------------------------------

ALERTS = {
    "reliability:attempt-ok-rate-low": "<0.80",
    "reliability:stall-rate-high": ">0.20",
    "reliability:agent-failure-rate-high": ">0.20",
    "reliability:agents-never-notified": ">0",
    "reliability:tool-calls-without-results": ">0",
    "cost:cache-hit-rate-zero": "<0.05",
    "cost:cache-hit-rate-low": "<0.30",
    "cost:waste-share-high": ">0.10",
    "cost:token-concentration-high": ">0.15",
    "time:human-and-idle-over-a-third": ">0.35",
    "time:phases-overlap": ">0",
    "outcomes:repo-unconfirmed": "==0",
    "outcomes:failing-assertions": ">0",
    "outcomes:test-commands-errored": ">0",
    "outcomes:command-output-undercounts-commits": ">2",
    "harness:api-errors": ">0",
    "harness:status-misreported": ">0",
    "harness:launches-rejected": ">0",
    "harness:tool-error-rate-high": ">0.03",
    "human:manual-retries": ">0",
    "human:session-interrupted": ">0",
    "rework:unit-retry-rate-high": ">0.10",
}


# ---------------------------------------------------------------------------
# Provenance.
#
# Four kinds of claim, chosen so that nothing belongs to two of them. The first
# two can be checked and so they must be; the last two cannot, and saying so is
# the whole point of marking them.
#
# The markers are three characters at the front of the line rather than a column
# in a table, because a column would push the figures off a narrow screen and a
# reader scanning for "is this measured" wants it where the eye already is.
# ---------------------------------------------------------------------------

MEASURED = "[M]"
DERIVED = "[D]"
INFERENCE = "[I]"
DOMAIN = "[K]"

TIERS = {
    MEASURED: "measured - read straight off the records: a count, a sum, a field value. One "
              "query reproduces it.",
    DERIVED: "derived - arithmetic over measured values: a share, a rate, a per-unit figure. The "
             "line names its inputs; section 10 gives the formula, the reason that formula answers "
             "the question, and the trap where the derivation has one.",
    INFERENCE: "inference - a claim about what caused these records or what they mean. Not "
               "reproducible: it is an argument from the figures, and a reader can reject it "
               "without disputing a number.",
    DOMAIN: "domain knowledge - a claim about how this kind of system behaves, not taken from "
            "these records at all.",
}

# How the checks in section 10 are spelled. `clp` is named with no path, so the
# file never records one machine's install layout; the header says to put the
# plugin's bin/ on $PATH before running them.
SQL_TOOL = "clp bundle"
CATALOG_NOTE = ("`clp bundle <bundle> sql` opens catalog.sqlite read-only, so running a check can "
                "never change what it is checking.")

# The message-id dedup rule, spelled for a check command: see DEDUP_KEY.
DEDUP_SQL = "CASE WHEN message_id IS NULL THEN 'row#' || id ELSE 'msg:' || message_id END"

# What section 10 prints if a figure reaches it with no command and no reason. It
# names the defect rather than implying the figure is unverifiable: a bare
# "unstated" would claim a figure cannot be checked while giving no reason at all.
NO_REASON = ("no reason was recorded for this figure. That is a defect in clp session facts, not a "
             "property of the figure: report it rather than trusting the figure or discarding it.")


def sql_check(bundle, query):
    """One `clp bundle <bundle> sql "<query>"` command, as a reader would type it."""
    return f'{SQL_TOOL} {bundle} sql "{" ".join(str(query).split())}"'


def add_check(F, name, tier, value, command=None, derivation="", trap="", note=""):
    """Register one headline figure's provenance for section 10.

    `command` is the single command that reproduces the figure. It is left out
    only where no single command can, and then `note` says what a reader has to
    run instead - a figure whose check is a command that does not actually
    produce the number is worse than a figure with no check, because it invites
    a reader to conclude the number is wrong.
    """
    F.setdefault("checks", []).append({
        "name": name, "tier": tier, "value": value, "command": command,
        "derivation": derivation, "trap": trap, "note": note})


# Tool names that do a dedicated search, for B2. Reaching for Bash instead of
# these is the behaviour the axis is looking for.
SEARCH_TOOLS = ("Grep", "Glob", "Search", "SearchFiles", "CodeSearch")

# Section 7's manual-retry heuristic. It is a heuristic and is labelled as one.
RETRY_PROMPT = re.compile(r"try again|retry|continue|keep going", re.I)

# Section 6 / axis A4: which launch rejections are the caller's own fault. The
# terms are listed once and both the regex and the check's SQL are built from
# them, so the check can never drift from the measurement it claims to check.
CONFIG_TERMS = ("invalid", "parse error", "syntax", "unexpected token", "unterminated",
                "schema", "config")
CONFIG_REJECTION = re.compile("|".join(CONFIG_TERMS), re.I)
# SQLite's LIKE is case-insensitive over ASCII, which is what the regex's re.I does here.
CONFIG_LIKE = " OR ".join(f"json_extract(attrs,'$.error') LIKE '%{term}%'" for term in CONFIG_TERMS)


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def num(value):
    """An integer with thousands separators; '-' for a missing one."""
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def pct(n, d, digits=1):
    """n as a percentage of d, or 'n/a' when there is no denominator."""
    if not d:
        return "n/a"
    return f"{100.0 * n / d:.{digits}f}%"


def frac(n, d, unit=""):
    """"593 of 1,380 attempts (43.0%)" - a count always carries its denominator."""
    tail = f" {unit}" if unit else ""
    return f"{num(n)} of {num(d)}{tail} ({pct(n, d)})"


def rate(n, d, digits=3):
    if not d:
        return "n/a"
    return f"{n / d:.{digits}f}"


def bare(value):
    """A value as a bare number for a machine to read: no percent sign, no
    thousands separator, no unit. `n/a` when it could not be measured."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def fire(alerts, slug, value):
    """Record an alert together with the value and the test that fired it.

    An alert is a rule, not a score. Printing the value and the threshold beside
    the slug is what keeps it arguable: a reader who thinks the threshold is
    wrong can see it, instead of having to guess at it.
    """
    alerts.append((slug, bare(value), ALERTS[slug]))


def quoted(text):
    """Text for a `key="..."` field: one line, with no embedded double quote."""
    return " ".join(str(text).replace('"', "'").split())


def parse_ts(text):
    """A catalog timestamp (ISO 8601, optional fractional seconds) as a datetime."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_between(start, end):
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 60.0


def wrap(text, width=96):
    return "\n".join(textwrap.wrap(text, width)) if text else ""


# ---------------------------------------------------------------------------
# subprocesses
# ---------------------------------------------------------------------------

def run_tool(argv, env, timeout=SUBPROCESS_TIMEOUT):
    """Run a helper next to this script. Returns (stdout, None) or (None, reason).

    Never raises: a helper that is missing, that fails, or that hangs makes one
    section UNAVAILABLE, and the other eight are still worth writing.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return None, f"`{argv[0]}` is not present next to this script"
    except subprocess.TimeoutExpired:
        return None, f"`{Path(argv[0]).name}` did not finish within {timeout}s"
    except OSError as exc:
        return None, f"`{Path(argv[0]).name}` could not be run: {exc}"
    if proc.returncode != 0:
        detail = " ".join((proc.stderr or proc.stdout or "").split())[:300] or "no output"
        return None, f"`{Path(argv[0]).name}` exited {proc.returncode}: {detail}"
    return proc.stdout, None


def tool_env(catalog_clp_s):
    """The environment the helpers run in.

    `clp search` resolves clp-s from $CLP_S_BIN, then from the installed
    plugin, then from $PATH. A stale clp-s on $PATH cannot open an archive
    written by a newer one, and the whole turns section is then lost. The
    catalog records the clp-s that built the bundle, so use it when the caller
    has not named one - the caller's own $CLP_S_BIN always wins.
    """
    env = dict(os.environ)
    if not env.get("CLP_S_BIN") and catalog_clp_s and os.access(catalog_clp_s, os.X_OK):
        env["CLP_S_BIN"] = catalog_clp_s
    return env


# ---------------------------------------------------------------------------
# clp session turns output
# ---------------------------------------------------------------------------

TURNS_HEAD = re.compile(r"^TURNS=(\d+)\s+PROMPTS=(\d+)\s+TOOL_CALLS=(\d+)\s+WITH_TOOL_DURATION=(\d+)")
WAIT_LINE = re.compile(r"^WAIT\s+(\S+)\s+([\d.]+)\s+at=(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)(\s+human)?\s*$")
TURN_LINE = re.compile(r"^TURN\s+(\d+)\s+(.*)$")
PROMPT_TAIL = re.compile(r'\s+prompt="(.*)"\s*$', re.S)
CLOCK = re.compile(r"^\d\d:\d\d(:\d\d)?$")


def kv_pairs(text):
    """The `k=v` fields of a TURN or TOTAL line.

    Two fields do not survive a plain split: `start=` is a date and a time with
    a space between them, and `longest=NAME VALUE` is a tool name followed by
    its minutes. Both are stitched back together here.
    """
    fields, out = text.split(), {}
    i = 0
    while i < len(fields):
        field = fields[i]
        if "=" not in field:
            i += 1
            continue
        key, value = field.split("=", 1)
        if key == "start" and i + 1 < len(fields) and CLOCK.match(fields[i + 1]):
            value = f"{value} {fields[i + 1]}"
            i += 1
        elif key == "longest" and i + 1 < len(fields) and "=" not in fields[i + 1]:
            out["longest_minutes"] = to_float(fields[i + 1])
            i += 1
        out[key] = value
        i += 1
    return out


def to_float(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def to_int(text, default=None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def parse_turns(text):
    """clp session turns stdout as {totals, turns, waits}."""
    out = {"turns_count": None, "prompts": None, "tool_calls": None,
           "with_tool_duration": None, "minutes": {}, "tokens": {},
           "turns": [], "waits": []}
    for line in text.splitlines():
        line = line.rstrip()
        head = TURNS_HEAD.match(line)
        if head:
            out["turns_count"] = int(head.group(1))
            out["prompts"] = int(head.group(2))
            out["tool_calls"] = int(head.group(3))
            out["with_tool_duration"] = int(head.group(4))
            continue
        if line.startswith("TOTAL_MIN "):
            out["minutes"] = {k: to_float(v) for k, v in kv_pairs(line[10:]).items()}
            continue
        if line.startswith("TOTAL_TOKENS "):
            out["tokens"] = {k: to_int(v) for k, v in kv_pairs(line[13:]).items()}
            continue
        wait = WAIT_LINE.match(line)
        if wait:
            out["waits"].append({"tool": wait.group(1), "minutes": to_float(wait.group(2), 0.0),
                                 "at": f"{wait.group(3)} {wait.group(4)}",
                                 "human": bool(wait.group(5))})
            continue
        turn = TURN_LINE.match(line)
        if turn:
            rest = turn.group(2)
            prompt = ""
            tail = PROMPT_TAIL.search(rest)
            if tail:
                prompt, rest = tail.group(1), rest[: tail.start()]
            row = kv_pairs(rest)
            out["turns"].append({
                "turn": int(turn.group(1)),
                "start": row.get("start", ""),
                "e2e": to_float(row.get("e2e"), 0.0),
                "human": to_float(row.get("human"), 0.0),
                "tool": to_float(row.get("tool"), 0.0),
                "model": to_float(row.get("model"), 0.0),
                "idle": to_float(row.get("idle"), 0.0),
                "other": to_float(row.get("other"), 0.0),
                "calls": to_int(row.get("calls"), 0),
                "errors": to_int(row.get("errors"), 0),
                "tokens_in": to_int(row.get("tokens_in"), 0),
                "tokens_out": to_int(row.get("tokens_out"), 0),
                "longest": row.get("longest", "-"),
                "longest_minutes": row.get("longest_minutes"),
                "prompt": prompt,
            })
    return out


BUCKETS = ("human", "tool", "model", "idle", "other")


def dominant_bucket(turn):
    best = max(BUCKETS, key=lambda b: turn.get(b) or 0.0)
    return best if (turn.get(best) or 0.0) > 0 else "-"


# ---------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------

class Catalog:
    """The bundle's catalog.sqlite, opened read-only."""

    def __init__(self, path):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def rows(self, sql, *params):
        try:
            return self.db.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

    def one(self, sql, *params, default=None):
        rows = self.rows(sql, *params)
        if not rows or rows[0][0] is None:
            return default
        return rows[0][0]

    def meta(self, key, default=None):
        return self.one("SELECT v FROM bundle WHERE k = ?", key, default=default)


# One API response can be recorded in more than one log, so a bundle-wide token
# total has to count it once. See bundle_totals().
#
# Grouping key: the message id, or the row itself when the log carried no id. A
# row with no message id cannot be deduped against anything, so it must stay its
# own group - grouping every null together would silently collapse a whole log
# that lacks message ids into one response.
DEDUP_KEY = "CASE WHEN message_id IS NULL THEN 'row#' || id ELSE 'msg:' || message_id END"

# Where the copies of one response disagree, this is the copy that is kept.
TIE_BREAK = ("the copy reporting the largest tokens_input, because a response logged with more "
             "context is the later and more complete record of it; the whole row is taken from that "
             "one copy, and values are never averaged across copies")


def bundle_totals(cat):
    """The bundle-wide token totals, counting each API response once.

    The same response is written to more than one log: a subagent's transcript
    and the main log both hold it, and a fork inherits its parent's transcript,
    so one response can appear in several agent logs too. Each per-kind sum is
    therefore that log's own honest accounting and is left alone - it is only the
    cross-kind total that would double-count, and only that is deduped here.

    The copies do not always agree: on the reference bundle one id carries four
    token rows with two different input values, so this cannot assume the copies
    are identical. It keeps one whole row per response, chosen by TIE_BREAK, and
    never averages. Returns both the deduped and the naive figures, because the
    facts file has to show the per-kind sums and the total side by side and say
    why they do not add up.
    """
    naive = cat.rows("""SELECT COUNT(*), COALESCE(SUM(tokens_input), 0), COALESCE(SUM(tokens_output), 0),
                               COALESCE(SUM(tokens_cache_read), 0), COALESCE(SUM(tokens_cache_write), 0)
                          FROM events WHERE tokens_input IS NOT NULL""")
    # SQLite takes the bare columns from the row that produced MAX(), so each
    # group yields one coherent record rather than a mix of fields from copies.
    dedup = cat.rows(f"""
        SELECT COUNT(*), COALESCE(SUM(ti), 0), COALESCE(SUM(t_out), 0),
               COALESCE(SUM(cr), 0), COALESCE(SUM(cw), 0)
          FROM (SELECT MAX(tokens_input) AS ti, tokens_output AS t_out,
                       tokens_cache_read AS cr, tokens_cache_write AS cw
                  FROM events WHERE tokens_input IS NOT NULL
                 GROUP BY {DEDUP_KEY})""")
    if not naive or not dedup:
        return None
    rows, n_in, n_out, n_cr, n_cw = naive[0]
    resp, d_in, d_out, d_cr, d_cw = dedup[0]
    return {
        "responses": resp, "input": d_in, "output": d_out, "cache_read": d_cr, "cache_write": d_cw,
        "rows": rows, "naive_input": n_in, "naive_output": n_out,
        "naive_cache_read": n_cr, "naive_cache_write": n_cw,
        "collisions": cat.one("""SELECT COUNT(*) FROM (SELECT message_id FROM events
                                  WHERE tokens_input IS NOT NULL AND message_id IS NOT NULL
                                  GROUP BY message_id HAVING COUNT(*) > 1)""", default=0),
        "disagreeing": cat.one("""SELECT COUNT(*) FROM (SELECT message_id FROM events
                                   WHERE tokens_input IS NOT NULL AND message_id IS NOT NULL
                                   GROUP BY message_id HAVING COUNT(DISTINCT tokens_input) > 1)""",
                               default=0),
        "unkeyed": cat.one("""SELECT COUNT(*) FROM events
                               WHERE tokens_input IS NOT NULL AND message_id IS NULL""", default=0),
    }


def attrs_of(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# the facts
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Compute a session-trajectory report's numbers in code.")
    ap.add_argument("--bundle", help="a clp bundle bundle directory "
                                     "(not needed for --check-scale on its own)")
    ap.add_argument("--archive", default=None,
                    help="the main log's CLP archive (default: derived from the catalog)")
    ap.add_argument("--turns-file", default=None,
                    help="a clp session turns run captured earlier, parsed instead of re-running it")
    ap.add_argument("--axes", action="store_true",
                    help="also compute section 9: each axis's raw value and the components behind it")
    ap.add_argument("--check-scale", action="store_true",
                    help="validate the scale file against the axes this script emits, "
                         "printing SCALE_OK or one SCALE_PROBLEM= line per defect")
    ap.add_argument("--scale", default=str(BIN_DIR.parent / "scoring-scale.json"),
                    help="the scale file --check-scale validates (default: the plugin's own)")
    ap.add_argument("--waits", type=int, default=60, help="WAIT lines to ask for (default 60)")
    ap.add_argument("--top", type=int, default=10, help="rows per top-N table (default 10)")
    ap.add_argument("--out", default="/tmp/clp-session-facts.md",
                    help="where to write the markdown facts file")
    args = ap.parse_args(argv)

    # Validating a scale reads the scale and this script's own axis metadata, and
    # nothing else. So --check-scale on its own measures nothing and never opens
    # a catalog: a caller with a scale to check should not have to find a bundle,
    # or fake one, to get an answer.
    if args.check_scale and not args.bundle:
        problems = check_scale(args.scale)
        for slug, detail in problems:
            print(f"SCALE_PROBLEM={slug} {detail}")
        if problems:
            return 2
        print(f"SCALE_OK file={args.scale} axes={len(AXES)}")
        return 0
    if not args.bundle:
        print("error: --bundle is required to measure a session; --check-scale alone needs no bundle",
              file=sys.stderr)
        return 1

    bundle = Path(args.bundle).expanduser().resolve()
    catalog_path = bundle / "catalog.sqlite"
    if not catalog_path.is_file():
        print(f"error: no catalog at {catalog_path}; --bundle wants a clp bundle bundle directory",
              file=sys.stderr)
        return 1
    try:
        cat = Catalog(catalog_path)
        cat.rows("SELECT 1 FROM bundle LIMIT 1")
    except sqlite3.Error as exc:
        print(f"error: cannot read {catalog_path}: {exc}", file=sys.stderr)
        return 1

    env = tool_env(cat.meta("clp_s"))
    out, alerts, keys = [], [], {}
    w = out.append

    # ------------------------------------------------------------------ inputs
    archive_kinds = {kind: (aid, records) for aid, kind, records
                     in cat.rows("SELECT archive_id, kind, records FROM archives")}
    archive_dir, archive_note = resolve_archive(args.archive, bundle, archive_kinds)
    # A captured --axes stdout is re-scored later by clp session score, so it has
    # to say what it was measured from; without this, provenance is lost the
    # moment the output leaves the pipe.
    keys["BUNDLE"] = str(bundle)
    if archive_dir is not None:
        keys["ARCHIVE"] = str(archive_dir)

    turns, turns_note = load_turns(args, archive_dir, env)
    outcomes, outcomes_note = load_outcomes(bundle, env)
    repo, repo_note = load_repo(bundle, env)

    totals = bundle_totals(cat)
    has_agents = bool(cat.one("SELECT COUNT(*) FROM nodes WHERE kind = 'agent'", default=0))
    has_workflows = bool(cat.one("SELECT COUNT(*) FROM nodes WHERE kind = 'workflow'", default=0))
    multi = has_agents or has_workflows
    single_note = "not applicable - single-threaded session (the catalog has no agent or workflow nodes)"

    w("# Session trajectory facts (computed in code; every figure below is exact)\n")
    w(wrap("Each section's figures are computed from the bundle's catalog, from clp session turns, "
           "or from clp bundle outcomes/repo, and every count carries the denominator it is a share of. "
           "Quote them; do not re-derive them. A figure that could not be computed says so and why."))
    w("")
    w("**What the markers mean.** Every figure carries one, and the four kinds do not overlap:")
    w("")
    for marker in (MEASURED, DERIVED, INFERENCE, DOMAIN):
        w(f"- `{marker}` {TIERS[marker]}")
    w("")
    w(wrap("A percentage in parentheses is always derived, from the two counts printed beside it, so "
           "it takes no marker of its own. In a table the markers are in the column headings. A line "
           "with no marker is orientation - a heading, a source note, or an instruction to the writer "
           "- and asserts nothing about the session."))
    w("")
    w(wrap("Section 10 lists every headline figure again with the one command that reproduces it. "
           "Those commands name this plugin's own `clp` with no path, so nothing here records one "
           "machine's install layout: run them with the plugin's `bin/` on $PATH. " + CATALOG_NOTE))
    w("")

    F = {"checks": []}   # every figure section 9 needs, computed once here
    F["bundle"] = str(bundle)
    if archive_dir is not None:
        F["archive"] = str(archive_dir)
    F["turns_cmd"] = (f"clp session turns --top 0 --waits {args.waits} {archive_dir}"
                      if archive_dir is not None else None)

    section_session(cat, turns, turns_note, archive_kinds, archive_dir, archive_note, w, F, keys,
                    alerts, totals)
    section_reliability(cat, multi, single_note, w, F, keys, alerts, args.top)
    section_cost(cat, w, F, keys, alerts, args.top, multi, single_note, totals)
    section_time(cat, turns, turns_note, w, F, keys, alerts, args.top)
    section_outcomes(cat, outcomes, outcomes_note, repo, repo_note, w, F, keys, alerts, args.top)
    section_harness(cat, multi, single_note, w, F, keys, alerts, args.top)
    section_human(cat, turns, turns_note, w, F, keys, alerts)
    section_rework(cat, multi, single_note, w, F, keys, alerts)

    # -- two figures need more than one section, so they are finished here
    artifacts = F.get("confirmed_artifacts")
    if artifacts:
        F["tokens_per_artifact"] = F["total_input"] / artifacts
    total_in = F.get("total_input") or 0
    if total_in:
        worst = max(F.get("largest_turn_input") or 0, F.get("largest_run_input") or 0) / total_in
        if worst > 0.15:
            fire(alerts, "cost:token-concentration-high", worst)

    axis_rows = section_axes(w, F) if args.axes else None
    section_verification(w, F, axis_rows)
    # --check-scale never feeds a score back into the run: it only reports on the
    # file, so a broken scale is loud instead of silently mis-scoring a session.
    scale_problems = check_scale(args.scale) if args.check_scale else None

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip() + "\n")

    # ------------------------------------------------------------------ stdout
    print(f"FACTS_FILE={args.out}")
    for key in ("SESSION_ID", "TURNS", "WALL_CLOCK_HOURS", "TOTAL_INPUT_TOKENS", "CACHE_HIT_RATE",
                "ATTEMPT_OK_RATE", "WASTE_SHARE", "CONFIRMED_COMMITS", "CONFIRMED_PRS"):
        print(f"{key}={keys.get(key, 'n/a')}")
    for key in sorted(k for k in keys if k not in {
            "SESSION_ID", "TURNS", "WALL_CLOCK_HOURS", "TOTAL_INPUT_TOKENS", "CACHE_HIT_RATE",
            "ATTEMPT_OK_RATE", "WASTE_SHARE", "CONFIRMED_COMMITS", "CONFIRMED_PRS"}):
        print(f"{key}={keys[key]}")
    for row in axis_rows or []:
        head = f"AXIS {row['id']} group={row['group']} value={bare(row['value'])}"
        if row["value"] is None:
            print(f'{head} reason="{quoted(row["reason"])}"')
        else:
            den = f' denominator={row["denominator"]}' if row.get("denominator") else ""
            print(f'{head} unit={row["unit"]}{den} components="{quoted(row["components"])}"')
    # The check goes on a line of its own rather than in a field of the AXIS line:
    # a command holds double quotes, and `components="..."` has to stay the last
    # field for a consumer that reads it. One command per line is also what a
    # reader wants to copy.
    for row in axis_rows or []:
        if row.get("check"):
            print(f"AXIS_CHECK {row['id']} {row['check']}")
        elif row["value"] is not None:
            print(f"AXIS_NO_SINGLE_CHECK {row['id']} {quoted(row.get('check_note') or NO_REASON)}")
    for slug, value, test in dict.fromkeys(alerts):
        print(f"ALERT={slug} value={value} threshold={test}")
    if scale_problems is not None:
        if scale_problems:
            for slug, detail in scale_problems:
                print(f"SCALE_PROBLEM={slug} {detail}")
            return 2
        print(f"SCALE_OK file={args.scale} axes={len(AXES)}")
    return 0


# ---------------------------------------------------------------------------
# input loading
# ---------------------------------------------------------------------------

def resolve_archive(given, bundle, archive_kinds):
    if given:
        path = Path(given).expanduser().resolve()
        return path, f"given on the command line: {path}"
    main_archive = archive_kinds.get("main")
    if not main_archive:
        return None, "the catalog's `archives` table has no row with kind='main'"
    path = bundle / "archives" / main_archive[0]
    if not path.is_dir():
        return None, f"the catalog names main archive {main_archive[0]}, but {path} is not a directory"
    return path, f"derived from the catalog (archives.kind='main'): {path}"


def load_turns(args, archive_dir, env):
    if args.turns_file:
        try:
            return parse_turns(Path(args.turns_file).read_text(encoding="utf-8")), \
                f"parsed from the pre-captured run at {args.turns_file}"
        except OSError as exc:
            return None, f"--turns-file {args.turns_file} could not be read: {exc}"
    if archive_dir is None:
        return None, "no main-log archive to run clp session turns against"
    text, reason = run_tool([str(BIN_DIR / "clp"), "session", "turns", "--top", "0",
                             "--waits", str(args.waits), str(archive_dir)], env)
    if text is None:
        return None, reason
    return parse_turns(text), f"clp session turns --top 0 --waits {args.waits} {archive_dir}"


def load_outcomes(bundle, env):
    text, reason = run_tool([str(BIN_DIR / "clp"), "bundle", str(bundle), "outcomes", "--json"], env)
    if text is None:
        return None, reason
    try:
        return json.loads(text), "clp bundle outcomes --json"
    except ValueError as exc:
        return None, f"clp bundle outcomes produced unparseable JSON: {exc}"


def load_repo(bundle, env):
    text, reason = run_tool([str(BIN_DIR / "clp"), "bundle", str(bundle), "repo", "--json"], env)
    if text is None:
        return None, reason
    try:
        return json.loads(text), "clp bundle repo --json"
    except ValueError as exc:
        return None, f"clp bundle repo produced unparseable JSON: {exc}"


# ---------------------------------------------------------------------------
# 1. Session
# ---------------------------------------------------------------------------

def section_session(cat, turns, turns_note, archive_kinds, archive_dir, archive_note, w, F, keys,
                    alerts, totals):
    w("## 1. Session")
    session_id = cat.meta("session_id") or "(not recorded)"
    keys["SESSION_ID"] = session_id
    span = cat.rows("SELECT MIN(ts), MAX(ts) FROM events")
    first, last = span[0] if span else (None, None)
    span_min = minutes_between(first, last)
    hours = (span_min / 60.0) if span_min is not None else None
    F["wall_clock_hours"] = hours
    keys["WALL_CLOCK_HOURS"] = f"{hours:.1f}" if hours is not None else "n/a"

    span_sql = sql_check(F["bundle"], "SELECT MIN(ts) AS first_record, MAX(ts) AS last_record, "
                                      "ROUND((julianday(MAX(ts))-julianday(MIN(ts)))*24.0,1) AS "
                                      "wall_clock_hours FROM events")
    w(f"- {MEASURED} Session id: `{session_id}`")
    w(f"- {MEASURED} Bundle built: {cat.meta('built_at') or 'unrecorded'}; working directory "
      f"`{cat.meta('cwd') or 'unrecorded'}`")
    w(f"- {DERIVED} Time span: {first or 'unknown'} to {last or 'unknown'} "
      + (f"({hours:.1f} wall-clock hours, {span_min / 1440:.1f} days), from those two measured "
         "timestamps" if hours is not None
         else "(span unavailable: the catalog has no event timestamps)"))
    if hours is not None:
        add_check(F, "Wall-clock span", DERIVED, f"{hours:.1f} hours ({span_min / 1440:.1f} days)",
                  span_sql,
                  derivation="(last record's timestamp - first record's timestamp), in hours. It "
                             "answers \"how long was this session open\" because the first and last "
                             "records bracket everything the harness wrote.",
                  trap="it is the span the records cover, not time anyone spent: most of it can be "
                       "a gap between two days' work. Section 4's e2e minutes are the worked time.")
    if turns:
        F["turns"] = turns["turns_count"]
        keys["TURNS"] = turns["turns_count"]
        w(f"- {MEASURED} Turns: {num(turns['turns_count'])}; human prompts: {num(turns['prompts'])}; "
          f"tool calls seen by clp session turns: {num(turns['tool_calls'])}")
        w(f"  - Source: {turns_note}")
        add_check(F, "Turns", MEASURED, num(turns["turns_count"]), F.get("turns_cmd"),
                  note="" if F.get("turns_cmd") else
                  "the turns run was supplied as a file, so the command that produced it is not known "
                  "here; re-run clp session turns against the main archive")
    else:
        catalog_turns = cat.one("SELECT COUNT(DISTINCT turn) FROM events WHERE turn IS NOT NULL", default=0)
        F["turns"] = catalog_turns
        keys["TURNS"] = catalog_turns
        w(f"- {MEASURED} Turns: {num(catalog_turns)} distinct turn numbers in the catalog. "
          f"clp session turns is UNAVAILABLE: {turns_note}")
        add_check(F, "Turns", MEASURED, num(catalog_turns),
                  sql_check(F["bundle"], "SELECT COUNT(DISTINCT turn) AS turns FROM events "
                                         "WHERE turn IS NOT NULL"))

    # -- models
    rows = cat.rows("""
        SELECT COALESCE(a.model, '(not recorded in the catalog)') AS model,
               COUNT(*), SUM(e.tokens_input), SUM(e.tokens_output)
          FROM events e LEFT JOIN agents a ON a.agent_id = e.agent_id
         WHERE e.tokens_input IS NOT NULL
         GROUP BY 1 ORDER BY 2 DESC""")
    named = [r for r in rows if not r[0].startswith("(")]
    logged_rows = sum(r[1] for r in rows)
    # Bundle-wide, so each response counts once however many logs recorded it.
    total_responses = (totals or {}).get("responses", logged_rows)
    F["responses"] = total_responses
    F["models_named"] = bool(named)
    responses_sql = sql_check(
        F["bundle"], f"SELECT COUNT(*) AS responses FROM (SELECT 1 FROM events "
                     f"WHERE tokens_input IS NOT NULL GROUP BY {DEDUP_SQL})")
    w(f"- {MEASURED} Model responses (each counted once): {num(total_responses)}"
      + (f", from {num(logged_rows)} token-bearing records - {num(logged_rows - total_responses)} of "
         "them are second copies of a response another log already recorded (see the Cost section)"
         if logged_rows != total_responses else ""))
    add_check(F, "Model responses, each counted once", MEASURED, num(total_responses), responses_sql,
              derivation="a count of the distinct grouping keys, not a division, so it is measured: "
                         "one group per `message_id`, and one group per row where a log recorded no "
                         "message id.")
    if named:
        for model, n, tin, tout in named:
            w(f"  - {MEASURED} `{model}`: {frac(n, total_responses, 'responses')}, {num(tin)} input / {num(tout)} output tokens")
        unknown = sum(r[1] for r in rows if r[0].startswith("("))
        if unknown:
            w(f"  - {MEASURED} model not recorded for {frac(unknown, total_responses, 'responses')} "
              "(the catalog records a model only where the harness logged one; the rest are omitted "
              "rather than guessed)")
    else:
        w("  - Per-model breakdown: unavailable. The catalog records no model for any response, "
          "so the model mix is omitted rather than guessed.")

    # -- archives and their files
    w(f"- {MEASURED} Archives in the bundle (records compressed, and source files behind them):")
    sources = dict(cat.rows("SELECT kind, COUNT(*) FROM sources GROUP BY 1"))
    for kind, (aid, records) in sorted(archive_kinds.items()):
        files = sources.get(kind)
        w(f"  - `{kind}`: {num(records)} records, {num(files) if files is not None else 'unknown'} "
          f"source file(s) (archive `{aid}`)")
    extra = {k: v for k, v in sources.items() if k not in archive_kinds}
    if extra:
        w("  - Other source files in the bundle, not separately archived: "
          + ", ".join(f"`{k}` {num(v)}" for k, v in sorted(extra.items())))
    add_check(F, "Records per archive, and the source files behind them", MEASURED,
              f"one row per archive kind, {num(len(archive_kinds))} of them",
              sql_check(F["bundle"], "SELECT a.kind, a.records, (SELECT COUNT(*) FROM sources s "
                                     "WHERE s.kind = a.kind) AS source_files FROM archives a "
                                     "ORDER BY a.kind"))
    w(f"- Main-log archive used for the time split: {archive_note}")
    w("")


# ---------------------------------------------------------------------------
# 2. Reliability
# ---------------------------------------------------------------------------

def section_reliability(cat, multi, single_note, w, F, keys, alerts, top):
    w("## 2. Reliability")

    attempts = cat.rows("SELECT status, COUNT(*) FROM nodes WHERE kind = 'attempt' GROUP BY 1 ORDER BY 2 DESC")
    total_attempts = sum(n for _, n in attempts)
    ok_attempts = dict(attempts).get("ok", 0)
    stalled = dict(attempts).get("stalled-retried", 0)
    F.update(attempts_total=total_attempts, attempts_ok=ok_attempts, attempts_stalled=stalled)
    F["attempt_ok_rate"] = (ok_attempts / total_attempts) if total_attempts else None
    keys["ATTEMPT_OK_RATE"] = f"{F['attempt_ok_rate']:.3f}" if F["attempt_ok_rate"] is not None else "n/a"

    if not total_attempts:
        w(f"- Workflow attempts: {single_note}")
    else:
        w(f"- {MEASURED} Attempts by status ({num(total_attempts)} in all):")
        for status, n in attempts:
            w(f"  - {status or '(none)'}: {frac(n, total_attempts, 'attempts')}")
        add_check(F, "Attempts by status", MEASURED,
                  ", ".join(f"{status or '(none)'} {num(n)}" for status, n in attempts),
                  sql_check(F["bundle"], "SELECT status, COUNT(*) AS attempts, (SELECT COUNT(*) FROM "
                                         "nodes WHERE kind='attempt') AS all_attempts FROM nodes "
                                         "WHERE kind='attempt' GROUP BY 1 ORDER BY 2 DESC"))
        add_check(F, "Attempt ok rate", DERIVED, f"{F['attempt_ok_rate']:.3f}",
                  sql_check(F["bundle"], "SELECT COUNT(*) AS attempts, SUM(status='ok') AS ok "
                                         "FROM nodes WHERE kind='attempt'"),
                  derivation="attempts with status `ok` over all attempts. It answers \"how often did "
                             "a unit of work reach a usable end\" because `ok` is the only attempt "
                             "status the runtime treats as a result it will keep.")
        w(f"- {DERIVED} Stalls: {frac(stalled, total_attempts, 'attempts')} were stalled and retried")
        causes = cat.rows("""SELECT status, COALESCE(cause, '(no cause recorded)'), COUNT(*)
                               FROM nodes WHERE kind = 'attempt' AND status <> 'ok'
                              GROUP BY 1, 2 ORDER BY 3 DESC""")
        bad = sum(n for _, _, n in causes)
        w(f"- {MEASURED} Attempt failure causes ({frac(bad, total_attempts, 'attempts')} did not end ok):")
        for status, cause, n in causes:
            w(f"  - {status} / {cause}: {frac(n, bad, 'non-ok attempts')}")
        if causes:
            add_check(F, "Attempt failure causes", MEASURED,
                      f"{num(len(causes))} status/cause rows over {num(bad)} attempts that did not end ok",
                      sql_check(F["bundle"],
                                "SELECT status, COALESCE(cause,'(no cause recorded)') AS cause, "
                                "COUNT(*) AS attempts, (SELECT COUNT(*) FROM nodes WHERE "
                                "kind='attempt' AND status<>'ok') AS all_non_ok FROM nodes "
                                "WHERE kind='attempt' AND status<>'ok' GROUP BY 1,2 ORDER BY 3 DESC"))

    agents = cat.rows("SELECT status, COUNT(*) FROM nodes WHERE kind = 'agent' GROUP BY 1 ORDER BY 2 DESC")
    total_agents = sum(n for _, n in agents)
    completed = dict(agents).get("completed", 0)
    no_notify = dict(agents).get("no-notification", 0)
    F.update(agents_total=total_agents, agents_completed=completed, agents_no_notification=no_notify)
    if not total_agents:
        w(f"- Subagents: {single_note}")
    else:
        w(f"- {MEASURED} Agents by status ({num(total_agents)} in all):")
        for status, n in agents:
            w(f"  - {status or '(none)'}: {frac(n, total_agents, 'agents')}")
        agent_causes = cat.rows("""SELECT COALESCE(cause, '(no cause recorded)'), COUNT(*)
                                     FROM nodes WHERE kind = 'agent' AND status <> 'completed'
                                    GROUP BY 1 ORDER BY 2 DESC""")
        if agent_causes:
            w(f"  - {MEASURED} Causes behind the agents that did not complete: "
              + ", ".join(f"{c} {num(n)}" for c, n in agent_causes))
        w(f"- {MEASURED} Agents that finished but never notified their caller: "
          f"{frac(no_notify, total_agents, 'agents')}.")
        w(f"  - {DOMAIN} A no-notification agent's work reaches nobody: the caller waits, then moves "
          "on without it.")
        add_check(F, "Agents by status", MEASURED,
                  ", ".join(f"{status or '(none)'} {num(n)}" for status, n in agents),
                  sql_check(F["bundle"], "SELECT status, COALESCE(cause,'(no cause recorded)') AS cause, "
                                         "COUNT(*) AS agents, (SELECT COUNT(*) FROM nodes WHERE "
                                         "kind='agent') AS all_agents FROM nodes WHERE kind='agent' "
                                         "GROUP BY 1,2 ORDER BY 3 DESC"))

    # -- what was in flight when an attempt stalled
    if stalled:
        rows = cat.rows("""
            SELECT COALESCE((SELECT t.name FROM events e
                               JOIN event_tools t ON t.event = e.id AND t.role = 'use'
                              WHERE e.agent_id = n.agent_id
                              ORDER BY e.ts DESC, e.id DESC LIMIT 1), '(none)') AS last_tool,
                   COUNT(*) AS n
              FROM nodes n
             WHERE n.kind = 'attempt' AND n.status = 'stalled-retried'
             GROUP BY 1 ORDER BY n DESC""")
        w(f"- {MEASURED} The last tool each stalled attempt used before it stopped ({num(stalled)} stalls):")
        for name, n in rows[:top]:
            note = "  (the attempt made no tool call at all before stalling)" if name == "(none)" else ""
            w(f"  - {name}: {frac(n, stalled, 'stalls')}{note}")
        add_check(F, "The last tool before each stall", MEASURED,
                  ", ".join(f"{name} {num(n)}" for name, n in rows[:3])
                  + f" (of {num(stalled)} stalls)",
                  sql_check(F["bundle"], """SELECT COALESCE((SELECT t.name FROM events e
                      JOIN event_tools t ON t.event = e.id AND t.role = 'use'
                      WHERE e.agent_id = n.agent_id ORDER BY e.ts DESC, e.id DESC LIMIT 1),
                      '(none)') AS last_tool, COUNT(*) AS stalls,
                      (SELECT COUNT(*) FROM nodes WHERE kind='attempt'
                       AND status='stalled-retried') AS all_stalls FROM nodes n
                      WHERE n.kind='attempt' AND n.status='stalled-retried'
                      GROUP BY 1 ORDER BY 2 DESC"""))

    # -- tool calls that never came back
    uses = cat.one("SELECT COUNT(*) FROM event_tools WHERE role = 'use'", default=0)
    orphans = cat.one("""SELECT COUNT(*) FROM event_tools u
                          WHERE u.role = 'use'
                            AND NOT EXISTS (SELECT 1 FROM event_tools r
                                             WHERE r.tool_use_id = u.tool_use_id AND r.role = 'result')""",
                      default=0)
    F.update(tool_uses=uses, tool_orphans=orphans)
    w(f"- {MEASURED} Tool calls with a `use` record and no matching `result`: "
      f"{frac(orphans, uses, 'calls')}"
      + ("" if orphans else " - every call that was issued came back."))
    add_check(F, "Tool calls issued, and those with no result", MEASURED,
              f"{num(orphans)} of {num(uses)}",
              sql_check(F["bundle"], """SELECT COUNT(*) AS issued,
                  SUM(NOT EXISTS (SELECT 1 FROM event_tools r
                      WHERE r.tool_use_id = u.tool_use_id AND r.role='result')) AS without_result
                  FROM event_tools u WHERE u.role='use'"""))

    # -- launches the runtime refused outright
    launch_errors = cat.rows("SELECT label, start, attrs FROM nodes WHERE kind = 'launch_error' ORDER BY start")
    launches = cat.one("SELECT COUNT(*) FROM edges WHERE kind = 'launch'", default=0) + len(launch_errors)
    F.update(launch_errors=len(launch_errors), launches=launches)
    rejected_config = 0
    if launch_errors:
        w(f"- {MEASURED} Launches the runtime rejected before anything ran: "
          f"{frac(len(launch_errors), launches, 'launches')}")
        for label, start, raw in launch_errors:
            reason = " ".join(str(attrs_of(raw).get("error", "(no reason recorded)")).split())
            if CONFIG_REJECTION.search(reason):
                rejected_config += 1
            w(f"  - {start or 'unknown time'} {label or 'launch'}: {reason[:200]}")
    else:
        w(f"- {MEASURED} Launches the runtime rejected before anything ran: 0 of {num(launches)} launches")
    F["launch_rejected_config"] = rejected_config
    add_check(F, "Launches, and the ones the runtime rejected", MEASURED,
              f"{num(len(launch_errors))} of {num(launches)}",
              sql_check(F["bundle"], "SELECT (SELECT COUNT(*) FROM edges WHERE kind='launch') + "
                                     "(SELECT COUNT(*) FROM nodes WHERE kind='launch_error') AS launches, "
                                     "(SELECT COUNT(*) FROM nodes WHERE kind='launch_error') AS rejected"),
              derivation="a launch is a `launch` edge, plus a `launch_error` node for one the runtime "
                         "refused before there was anything to point an edge at - so the rejected "
                         "launches are inside the denominator and not outside it.")

    if F["attempt_ok_rate"] is not None and F["attempt_ok_rate"] < 0.80:
        fire(alerts, "reliability:attempt-ok-rate-low", F["attempt_ok_rate"])
    if total_attempts and stalled / total_attempts > 0.20:
        fire(alerts, "reliability:stall-rate-high", stalled / total_attempts)
    if total_agents and (total_agents - completed) / total_agents > 0.20:
        fire(alerts, "reliability:agent-failure-rate-high", (total_agents - completed) / total_agents)
    if no_notify:
        fire(alerts, "reliability:agents-never-notified", no_notify)
    if orphans:
        fire(alerts, "reliability:tool-calls-without-results", orphans)
    w("")


# ---------------------------------------------------------------------------
# 3. Cost
# ---------------------------------------------------------------------------

def section_cost(cat, w, F, keys, alerts, top, multi, single_note, totals):
    w("## 3. Cost")
    rows = cat.rows("""SELECT kind, COUNT(*), SUM(tokens_input), SUM(tokens_output),
                              SUM(tokens_cache_read), SUM(tokens_cache_write)
                         FROM events WHERE tokens_input IS NOT NULL
                        GROUP BY 1 ORDER BY 3 DESC""")
    # Per-kind sums are each one log's own accounting and are left as they are.
    # The bundle-wide totals count each response once - see bundle_totals().
    kind_in = sum(r[2] or 0 for r in rows)
    kind_out = sum(r[3] or 0 for r in rows)
    kind_cr = sum(r[4] or 0 for r in rows)
    kind_cw = sum(r[5] or 0 for r in rows)
    kind_resp = sum(r[1] for r in rows)
    t = totals or {}
    total_in = t.get("input", kind_in)
    total_out = t.get("output", kind_out)
    total_cr = t.get("cache_read", kind_cr)
    total_cw = t.get("cache_write", kind_cw)
    total_resp = t.get("responses", kind_resp)
    F.update(total_input=total_in, total_output=total_out, cache_read=total_cr, cache_write=total_cw)
    keys["TOTAL_INPUT_TOKENS"] = total_in

    w("Token usage is counted once per API response. The catalog sets `tokens_*` on one record per "
      "response *per log*, and one response can be written to more than one log, so the bundle-wide "
      "total below is deduplicated by `message_id` while each per-kind row is left as that log's own "
      "accounting.")
    w(f"{DOMAIN} `tokens_input` is the whole context sent with the call, so a long conversation's "
      "input total is far larger than its context window.")
    w("")
    w(f"| Archive kind | Responses {MEASURED} | Input {MEASURED} | Output {MEASURED} "
      f"| Cache read {MEASURED} | Cache write {MEASURED} |")
    w("|---|---:|---:|---:|---:|---:|")
    for kind, n, tin, tout, cr, cw in rows:
        w(f"| {kind} | {num(n)} | {num(tin)} | {num(tout)} | {num(cr)} | {num(cw)} |")
    w(f"| *sum of the rows above* | {num(kind_resp)} | {num(kind_in)} | {num(kind_out)} "
      f"| {num(kind_cr)} | {num(kind_cw)} |")
    w(f"| **bundle total, each response once** | {num(total_resp)} | {num(total_in)} | {num(total_out)} "
      f"| {num(total_cr)} | {num(total_cw)} |")
    w("")
    dup_rows = t.get("rows", kind_resp) - total_resp
    if dup_rows > 0:
        w(f"**The two last rows differ on purpose, and the column does not add up to the bundle total.** "
          f"{num(t.get('collisions'))} responses appear in more than one log; the bundle-wide totals "
          f"count each once, {num(kind_in - total_in)} input tokens fewer than the per-kind sums "
          f"({pct(kind_in - total_in, kind_in, 2)} of them). The same response is in a subagent's "
          "transcript and in the main log, and a fork inherits its parent's transcript, so it can be in "
          f"several agent logs too: {num(t.get('rows'))} token-bearing records hold {num(total_resp)} "
          "distinct responses. Each per-kind row is that log's own honest accounting and is not "
          "adjusted; only the cross-kind total would double-count, so only it is deduplicated.")
        if t.get("disagreeing"):
            w(f"- {MEASURED} The copies do not always agree: for {num(t['disagreeing'])} of those "
              f"responses the copies report different token counts. Where they differ this keeps "
              f"{TIE_BREAK}.")
        else:
            w(f"- {MEASURED} Every duplicated response's copies agree. Where they would not, the rule "
              f"is to keep {TIE_BREAK}.")
    else:
        w(f"No response is recorded in more than one log, so the per-kind rows add up to the bundle "
          f"total exactly.")
    if t.get("unkeyed"):
        w(f"- {MEASURED} {num(t['unkeyed'])} token-bearing records carry no `message_id`, so they "
          "cannot be matched against a copy in another log and are each counted as their own response. "
          "If that number is large, this log records no message ids and the bundle total may still "
          "double-count.")
    w("")
    dedup_sql = sql_check(
        F["bundle"], f"SELECT COUNT(*) AS responses, SUM(ti) AS input, SUM(t_out) AS output, "
                     f"SUM(cr) AS cache_read, SUM(cw) AS cache_write FROM (SELECT "
                     f"MAX(tokens_input) AS ti, tokens_output AS t_out, tokens_cache_read AS cr, "
                     f"tokens_cache_write AS cw FROM events WHERE tokens_input IS NOT NULL "
                     f"GROUP BY {DEDUP_SQL})")
    add_check(F, "Total input tokens", MEASURED, num(total_in), dedup_sql,
              derivation="a sum, once the copies of one response are collapsed: group the "
                         "token-bearing records by `message_id` (a record with none is its own group), "
                         "keep the largest `tokens_input` in each group, and add those up.",
              trap="the per-kind rows above sum higher, and they are not wrong: each is one log's own "
                   "accounting, and only the cross-kind total would count a shared response twice.")
    if rows:
        add_check(F, "Input tokens per archive kind", MEASURED,
                  ", ".join(f"{kind} {num(tin)}" for kind, _, tin, *_ in rows),
                  sql_check(F["bundle"],
                            "SELECT kind, COUNT(*) AS responses, SUM(tokens_input) AS input, "
                            "SUM(tokens_output) AS output, SUM(tokens_cache_read) AS cache_read, "
                            "SUM(tokens_cache_write) AS cache_write FROM events "
                            "WHERE tokens_input IS NOT NULL GROUP BY 1 ORDER BY 3 DESC"))
    for kind, n, tin, tout, cr, cw in rows:
        w(f"- {DERIVED} `{kind}` is {frac(tin or 0, kind_in, 'input tokens')} and "
          f"{frac(n, kind_resp, 'records')}, as a share of the per-kind sums (not of the "
          "deduplicated bundle total, so these add to 100%)")

    hit = (total_cr / total_in) if total_in else None
    F["cache_hit_rate"] = hit
    keys["CACHE_HIT_RATE"] = f"{hit:.4f}" if hit is not None else "n/a"
    w(f"- {DERIVED} Cache hit rate (cache_read / input): {num(total_cr)} / {num(total_in)} = "
      + (f"{hit:.4%}" if hit is not None else "n/a")
      + ("  -  nothing was ever served from cache; every token of every context was billed as fresh input."
         if hit == 0 else ""))
    if hit is not None:
        add_check(F, "Cache hit rate", DERIVED, f"{hit:.4%}", dedup_sql,
                  derivation=f"cache_read {num(total_cr)} over input {num(total_in)}, both from the one "
                             "deduplicated query. It answers \"how much of the context we sent did we "
                             "avoid paying full price for\" because cache_read is the part of the input "
                             "the provider served from a cache instead of reading afresh.",
                  trap="cache_read is a subset of input, not a figure beside it, so the ratio is a "
                       "share and can never exceed 1.")
    w(f"- {DERIVED} Input:output ratio: {num(total_in)} : {num(total_out)} = "
      + (f"{total_in / total_out:.1f}:1" if total_out else "n/a")
      + f". {DOMAIN} Input dominates the bill, so waste and cache both matter more than output length.")
    w("- **Every cost figure here is in tokens, on purpose.** The session log carries a "
      "`totalCostUSD` field and this report does not use it: it is a derived estimate computed from "
      "some unit cost, which is not necessarily what was actually paid, and it is not always updated, "
      "so it goes stale. Tokens are what was measured. Converting them to money needs a price list, a "
      "plan and a provider, so it is the reader's job with their own rates - do not quote a currency "
      "figure from this report.")

    # -- waste, in three distinct classes
    bad_attempt_n, bad_attempt_tok = cat.rows(
        "SELECT COUNT(*), COALESCE(SUM(tokens_input), 0) FROM nodes "
        "WHERE kind = 'attempt' AND status <> 'ok'")[0]
    bad_agent_n, bad_agent_tok = cat.rows(
        "SELECT COUNT(*), COALESCE(SUM(tokens_input), 0) FROM nodes "
        "WHERE kind = 'agent' AND status <> 'completed'")[0]
    discarded_n, discarded_tok = cat.rows("""
        SELECT COUNT(*), COALESCE(SUM(tokens_input), 0) FROM nodes
         WHERE kind = 'attempt' AND status = 'ok'
           AND instance IN (SELECT id FROM nodes WHERE kind = 'workflow'
                             AND status IN ('killed', 'aborted'))""")[0]
    waste = bad_attempt_tok + bad_agent_tok + discarded_tok
    # With no attempts and no agents there is nothing whose outcome could mark a
    # token as wasted, so the share is unknown, not zero.
    measurable = multi or bad_attempt_n or bad_agent_n or discarded_n
    share = (waste / total_in) if (total_in and measurable) else None
    F.update(waste_tokens=waste, waste_share=share, discarded_ok_tokens=discarded_tok,
             discarded_ok_attempts=discarded_n)
    keys["WASTE_SHARE"] = f"{share:.4f}" if share is not None else "n/a"

    waste_sql = sql_check(
        F["bundle"],
        "SELECT (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE kind='attempt' AND status<>'ok') "
        "+ (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE kind='agent' AND status<>'completed') "
        "+ (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE kind='attempt' AND status='ok' "
        "AND instance IN (SELECT id FROM nodes WHERE kind='workflow' AND status IN ('killed','aborted'))) "
        f"AS wasted_input, (SELECT SUM(ti) FROM (SELECT MAX(tokens_input) AS ti FROM events "
        f"WHERE tokens_input IS NOT NULL GROUP BY {DEDUP_SQL})) AS total_input")
    if not measurable:
        w(f"- Wasted input tokens: {single_note}. Nothing here carries an outcome, so no token can be "
          "shown to have been wasted - that is unknown, not zero.")
    else:
        w(f"- {DERIVED} **Wasted input tokens: {num(waste)}, {pct(waste, total_in)} of all input** "
          f"({num(waste)} / {num(total_in)}). Three classes, counted separately because they have "
          "different fixes:")
        w(f"  - {MEASURED} Attempts that did not end ok: {num(bad_attempt_n)} attempts, "
          f"{num(bad_attempt_tok)} input tokens ({pct(bad_attempt_tok, total_in)} of all input)")
        w(f"  - {MEASURED} Agents that did not complete: {num(bad_agent_n)} agents, "
          f"{num(bad_agent_tok)} input tokens ({pct(bad_agent_tok, total_in)} of all input)")
        w(f"  - {MEASURED} Successful work thrown away by a cancellation: {num(discarded_n)} attempts "
          f"that ended ok inside a killed or aborted workflow instance, {num(discarded_tok)} input "
          f"tokens ({pct(discarded_tok, total_in)} of all input).")
        w(f"    - {INFERENCE} This is its own waste class: the model did the work correctly and the "
          "runtime discarded it, so retry logic cannot recover it.")
        add_check(F, "Waste share of input tokens", DERIVED,
                  f"{pct(waste, total_in)} ({num(waste)} / {num(total_in)})", waste_sql,
                  derivation="the summed `tokens_input` of the attempts and agents that reached no kept "
                             "result - a non-ok attempt, an agent that did not complete, or an ok "
                             "attempt inside a killed or aborted instance - over the bundle-wide input "
                             "total. It answers \"how much did we pay for nothing\" because an attempt "
                             "with no kept result produced nothing that was used.",
                  trap="the three classes are disjoint by construction (the third takes only `ok` "
                       "attempts, which the first excludes), so they add up; a fourth class overlapping "
                       "them would make the sum meaningless.")

    # -- who spent it
    runs = cat.rows("SELECT label, tokens_input, tokens_output, attempts FROM nodes "
                    "WHERE kind = 'run' AND tokens_input IS NOT NULL ORDER BY tokens_input DESC LIMIT ?", top)
    agents = cat.rows("SELECT label, tokens_input, tokens_output, tool_calls FROM nodes "
                      "WHERE kind = 'agent' AND tokens_input IS NOT NULL ORDER BY tokens_input DESC LIMIT ?", top)
    if runs:
        w(f"- {MEASURED} Top {len(runs)} workflow runs by input tokens:")
        for label, tin, tout, att in runs:
            w(f"  - `{label}`: {num(tin)} input ({pct(tin, total_in)} of all input), {num(tout)} output, "
              f"{num(att)} attempts")
        F["largest_run_input"] = runs[0][1] or 0
        add_check(F, "Largest workflow run by input tokens", MEASURED, num(runs[0][1] or 0),
                  sql_check(F["bundle"], "SELECT label, tokens_input, tokens_output, attempts FROM nodes "
                                         "WHERE kind='run' AND tokens_input IS NOT NULL "
                                         "ORDER BY tokens_input DESC LIMIT 10"))
    if agents:
        w(f"- {MEASURED} Top {len(agents)} subagents by input tokens:")
        for label, tin, tout, calls in agents:
            w(f"  - `{label or '(unlabelled)'}`: {num(tin)} input ({pct(tin, total_in)} of all input), "
              f"{num(tout)} output, {num(calls)} tool calls")
        add_check(F, "Largest subagent by input tokens", MEASURED, num(agents[0][1] or 0),
                  sql_check(F["bundle"], "SELECT label, tokens_input, tokens_output, tool_calls FROM nodes "
                                         "WHERE kind='agent' AND tokens_input IS NOT NULL "
                                         "ORDER BY tokens_input DESC LIMIT 10"))
    if not runs and not agents:
        w(f"- Top token consumers among runs and agents: {single_note}")

    # -- where a big run's tokens actually went. A run whose input is mostly
    #    stalled-retried attempts is a different problem from one that is mostly
    #    ok attempts, and the totals above cannot tell them apart.
    if runs:
        wanted = [label for label, *_ in runs]
        rows = cat.rows("""
            SELECT r.label, a.status, COUNT(*), COALESCE(SUM(a.tokens_input), 0)
              FROM nodes a JOIN nodes r ON r.id = 'run:' || a.run_id
             WHERE a.kind = 'attempt' AND r.kind = 'run'
             GROUP BY 1, 2""")
        by_run = defaultdict(list)
        for label, status, n, tin in rows:
            by_run[label].append((status, n, tin))
        w(f"- Attempt outcomes and input tokens inside those top {len(wanted)} runs "
          "(a run's total says nothing about whether the tokens bought anything):")
        w("")
        w(f"| Run | Attempt status | Attempts {MEASURED} | Input tokens {MEASURED} "
          f"| Share of the run's input {DERIVED} |")
        w("|---|---|---:|---:|---:|")
        for label in wanted:
            entries = sorted(by_run.get(label, []), key=lambda x: -x[2])
            run_total = sum(t for _, _, t in entries)
            for status, n, tin in entries:
                w(f"| `{label}` | {status or '(none)'} | {num(n)} | {num(tin)} | {pct(tin, run_total)} |")
        w("")

    if hit is not None and hit < 0.05:
        fire(alerts, "cost:cache-hit-rate-zero", hit)
    elif hit is not None and hit < 0.30:
        fire(alerts, "cost:cache-hit-rate-low", hit)
    if share is not None and share > 0.10:
        fire(alerts, "cost:waste-share-high", share)
    w("")


# ---------------------------------------------------------------------------
# 4. Time
# ---------------------------------------------------------------------------

def section_time(cat, turns, turns_note, w, F, keys, alerts, top):
    w("## 4. Time")
    if not turns:
        w(f"- UNAVAILABLE: {turns_note}")
        w("  The per-turn time split comes only from clp session turns, so none of it can be quoted. "
          "The catalog-only figures below still hold.")
    else:
        m = turns["minutes"]
        e2e = m.get("e2e") or 0.0
        F["minutes"] = m
        w(f"- {MEASURED} Total end-to-end: {m.get('e2e', 0):.1f} minutes ({(e2e / 60):.1f} hours) over "
          f"{num(turns['turns_count'])} turns. Buckets do not double-count a second:")
        w("")
        w(f"| Bucket | Minutes {MEASURED} | Share of e2e {DERIVED} |")
        w("|---|---:|---:|")
        for bucket in BUCKETS:
            value = m.get(bucket) or 0.0
            w(f"| {bucket} | {value:.1f} | {pct(value, e2e)} |")
        w(f"| **e2e** | {e2e:.1f} | 100.0% |")
        w("")
        waiting = (m.get("human") or 0.0) + (m.get("idle") or 0.0)
        F["human_idle_share"] = (waiting / e2e) if e2e else None
        w(f"- Waiting on a person or on nothing at all (human + idle): {waiting:.1f} minutes, "
          f"{pct(waiting, e2e)} of e2e.")
        w(f"  - {INFERENCE} That is the ceiling on what more autonomy could buy back.")
        add_check(F, "Waiting on a person or on nothing, as a share of e2e", DERIVED,
                  f"{pct(waiting, e2e)} ({waiting:.1f} of {e2e:.1f} minutes)", F.get("turns_cmd"),
                  derivation="the `human` and `idle` minutes of the TOTAL_MIN line over its `e2e` "
                             "minutes. It answers \"how much of the elapsed time was nobody working\" "
                             "because the five buckets partition e2e and never double-count a second, "
                             "so human + idle is exactly the part with neither a tool nor the model "
                             "running.",
                  note="" if F.get("turns_cmd") else "the turns run was supplied as a file")
        w(f"- {DERIVED} Working (tool + model): {(m.get('tool') or 0) + (m.get('model') or 0):.1f} "
          f"minutes, {pct((m.get('tool') or 0) + (m.get('model') or 0), e2e)} of e2e.")
        w(f"- {MEASURED} Token totals clp session turns measured on the main thread: "
          f"input {num(turns['tokens'].get('input'))}, output {num(turns['tokens'].get('output'))}, "
          f"cache_read {num(turns['tokens'].get('cache_read'))}, "
          f"cache_write {num(turns['tokens'].get('cache_write'))}")

        listed = sorted(turns["turns"], key=lambda t: -(t["e2e"] or 0.0))
        shown = [t for t in listed if (t["e2e"] or 0) > 0][:top]
        F["largest_turn_input"] = max((t["tokens_in"] or 0) for t in turns["turns"]) if turns["turns"] else 0
        F["turn_rows"] = turns["turns"]
        w(f"- The {len(shown)} longest turns of {num(len(listed))} listed, longest first, with the bucket "
          f"that dominated each. Every column is {MEASURED} except Dominant bucket, which is "
          f"{DERIVED} - the largest of the five bucket columns on that row:")
        w("")
        w("| Turn | Start | e2e min | Dominant bucket | human | tool | model | idle | other | calls | errors | input tokens |")
        w("|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for t in shown:
            w(f"| {t['turn']} | {t['start']} | {t['e2e']:.1f} | {dominant_bucket(t)} | {t['human']:.1f} "
              f"| {t['tool']:.1f} | {t['model']:.1f} | {t['idle']:.1f} | {t['other']:.1f} "
              f"| {num(t['calls'])} | {num(t['errors'])} | {num(t['tokens_in'])} |")
        w("")
        for t in shown[: min(5, len(shown))]:
            w(f"  - Turn {t['turn']} ({t['e2e']:.1f} min, {pct(t['e2e'], e2e)} of e2e): longest single wait "
              f"{t['longest']} " + (f"{t['longest_minutes']:.1f} min" if t["longest_minutes"] is not None else "")
              + f'; prompt: "{t["prompt"][:110]}"')

        waits = turns["waits"]
        human_waits = [x for x in waits if x["human"]]
        other_waits = [x for x in waits if not x["human"]]
        F["waits"] = waits
        F["human_wait_minutes"] = sum(x["minutes"] for x in human_waits)
        w(f"- {MEASURED} Longest tool waits clp session turns listed: {num(len(waits))} in all - "
          f"{num(len(human_waits))} waiting on the person "
          f"({F['human_wait_minutes']:.1f} minutes), {num(len(other_waits))} waiting on a tool "
          f"({sum(x['minutes'] for x in other_waits):.1f} minutes). These are the N longest, not every wait.")
        for x in human_waits[: top]:
            w(f"  - human: {x['tool']} {x['minutes']:.1f} min at {x['at']}")
        for x in other_waits[: top]:
            w(f"  - tool: {x['tool']} {x['minutes']:.1f} min at {x['at']}")

    # -- fan-out: attempt minutes are concurrent, not elapsed
    spans = cat.rows("""SELECT status, instance, start, end FROM nodes
                         WHERE kind = 'attempt' AND start IS NOT NULL AND end IS NOT NULL""")
    agent_spans = cat.rows("""SELECT status, start, end FROM nodes
                               WHERE kind = 'agent' AND start IS NOT NULL AND end IS NOT NULL""")
    cancelled = {i for (i,) in cat.rows("SELECT id FROM nodes WHERE kind = 'workflow' "
                                        "AND status IN ('killed', 'aborted')")}
    attempt_minutes = useful = 0.0
    for status, instance, start, end in spans:
        got = minutes_between(start, end)
        if got is None:
            continue
        attempt_minutes += got
        # Minutes only count as useful when the result survived: an ok attempt
        # inside a cancelled instance was thrown away with the instance.
        if status == "ok" and instance not in cancelled:
            useful += got
    agent_minutes = 0.0
    for status, start, end in agent_spans:
        got = minutes_between(start, end)
        if got is None:
            continue
        agent_minutes += got
        if status == "completed":
            useful += got
    F.update(attempt_minutes=attempt_minutes, agent_minutes=agent_minutes, useful_minutes=useful)
    wall = (F.get("wall_clock_hours") or 0) * 60
    minutes_sql = sql_check(
        F["bundle"],
        "SELECT kind, COUNT(*) AS nodes, ROUND(SUM((julianday(end)-julianday(start))*1440.0),0) AS minutes, "
        "ROUND(SUM(CASE WHEN (kind='attempt' AND status='ok' AND (instance IS NULL OR instance NOT IN "
        "(SELECT id FROM nodes WHERE kind='workflow' AND status IN ('killed','aborted')))) "
        "OR (kind='agent' AND status='completed') THEN (julianday(end)-julianday(start))*1440.0 END),0) "
        "AS survived_minutes FROM nodes WHERE kind IN ('attempt','agent') "
        "AND start IS NOT NULL AND end IS NOT NULL GROUP BY 1")
    if spans or agent_spans:
        w(f"- {DERIVED} Attempt and agent minutes summed: {attempt_minutes:,.0f} minutes across "
          f"{num(len(spans))} attempts and {agent_minutes:,.0f} minutes across {num(len(agent_spans))} "
          f"agents, against a wall-clock span of {wall:,.0f} minutes. That is "
          + (f"{(attempt_minutes + agent_minutes) / wall:.1f}x" if wall else "n/a")
          + " the elapsed time.")
        w(f"  - {DOMAIN} **It measures fan-out, not duration**, because these ran in parallel. Never "
          "present it as how long the session took.")
        w(f"  - {DERIVED} Of those {attempt_minutes + agent_minutes:,.0f} agent-minutes, "
          f"{useful:,.0f} ({pct(useful, attempt_minutes + agent_minutes)}) went to work that survived: "
          "an attempt that ended ok inside an instance that was not cancelled, or an agent that completed.")
        add_check(F, "Agent-minutes, and the share of them that survived", DERIVED,
                  f"{useful:,.0f} of {attempt_minutes + agent_minutes:,.0f} "
                  f"({pct(useful, attempt_minutes + agent_minutes)})", minutes_sql,
                  derivation="each node's end minus its start, in minutes, summed; then the same sum "
                             "over only the nodes whose result survived. It answers \"how much of the "
                             "fleet's time bought something\" because a cancelled instance took its "
                             "attempts' output with it however well they ran.",
                  trap="summed attempt minutes are not elapsed time: the attempts run in parallel, so "
                       "this total exceeds the wall-clock span and says nothing about how long the "
                       "session took.")
    else:
        w("- Attempt and agent minutes: no attempt or agent node carries both a start and an end.")

    overlaps, pairs = phase_overlaps(cat)
    F["phase_overlaps"] = overlaps
    if pairs:
        w(f"- {DERIVED} Phases running at the same time: {frac(overlaps, pairs, 'same-run phase pairs')} "
          "overlap. Phase nodes carry no start or end of their own, so each phase's span is taken from "
          "the first start and last end of the units in it.")
        w(f"  - {INFERENCE} An overlap means the workflow was not running its phases in order.")
        add_check(F, "Phase pairs that overlap", DERIVED, f"{num(overlaps)} of {num(pairs)}",
                  sql_check(F["bundle"], """WITH s AS (SELECT run_id, phase, MIN(start) AS a,
                      MAX(end) AS b FROM nodes WHERE kind='unit' AND phase IS NOT NULL
                      AND start IS NOT NULL AND end IS NOT NULL GROUP BY 1,2)
                      SELECT SUM(x.a < y.b AND y.a < x.b) AS overlapping_pairs,
                      COUNT(*) AS comparable_pairs FROM s x JOIN s y
                      ON x.run_id=y.run_id AND x.phase < y.phase"""),
                  derivation="two phases of one run overlap when each starts before the other ends; the "
                             "denominator is every pair of phases in one run that both have timestamps. "
                             "It answers \"did this workflow run its phases in order\" because ordered "
                             "phases cannot share a second.",
                  trap="a phase's span is inferred from its units, so a single stray unit timestamp "
                       "widens a phase and can manufacture an overlap.")
    else:
        w("- Phases running at the same time: no workflow phase has units with timestamps, so no pair "
          "can be compared.")

    if F.get("human_idle_share") is not None and F["human_idle_share"] > 0.35:
        fire(alerts, "time:human-and-idle-over-a-third", F["human_idle_share"])
    if overlaps:
        fire(alerts, "time:phases-overlap", overlaps)
    w("")


def phase_overlaps(cat):
    """(overlapping pairs, comparable pairs) among phases of the same run.

    Phase nodes have no start or end, so a phase's span is the first start and
    last end of its units.
    """
    rows = cat.rows("""SELECT run_id, phase, MIN(start), MAX(end) FROM nodes
                        WHERE kind = 'unit' AND phase IS NOT NULL
                          AND start IS NOT NULL AND end IS NOT NULL
                        GROUP BY 1, 2""")
    by_run = defaultdict(list)
    for run_id, phase, start, end in rows:
        a, b = parse_ts(start), parse_ts(end)
        if a and b:
            by_run[run_id].append((phase, a, b))
    overlaps = pairs = 0
    for spans in by_run.values():
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                pairs += 1
                if spans[i][1] < spans[j][2] and spans[j][1] < spans[i][2]:
                    overlaps += 1
    return overlaps, pairs


# ---------------------------------------------------------------------------
# 5. Outcomes
# ---------------------------------------------------------------------------

MATCH_ORDER = ("exact", "time+subject", "time", "ambiguous", "none")


def section_outcomes(cat, outcomes, outcomes_note, repo, repo_note, w, F, keys, alerts, top):
    w("## 5. Outcomes")

    # -- what the command output alone claimed
    claimed = {a: (n, failed or 0, conf or 0) for a, n, failed, conf in cat.rows(
        "SELECT action, COUNT(*), SUM(failed), SUM(confirmed) FROM actions GROUP BY 1")}
    commit_run, _, commit_claimed = claimed.get("commit", (0, 0, 0))
    pr_run, pr_failed, pr_claimed = claimed.get("pr", (0, 0, 0))
    test_run, test_failed_runs, _ = claimed.get("test", (0, 0, 0))
    tests_passed = cat.one("SELECT COALESCE(SUM(tests_passed), 0) FROM actions WHERE action = 'test'", default=0)
    tests_failed = cat.one("SELECT COALESCE(SUM(tests_failed), 0) FROM actions WHERE action = 'test'", default=0)

    # -- what the repository confirms
    confirmed_commits = confirmed_prs = None
    exact = 0
    if repo is None:
        w(f"- **Repository confirmation is UNAVAILABLE: {repo_note}**")
        w("  Everything below this line is derived from the session's own command output and is "
          "**unconfirmed**: a `git commit` that printed nothing, or a `gh pr create` whose output was "
          "truncated, is indistinguishable here from one that never happened. Do not call these figures "
          "delivered work.")
    else:
        commands = repo.get("commits") or []
        by_match = defaultdict(int)
        for c in commands:
            by_match[c.get("match") or "none"] += 1
        exact = by_match.get("exact", 0)
        confirmed_commits = len(commands) - by_match.get("none", 0)
        pr_cmds = repo.get("prs") or []
        confirmed_prs = sum(len(p.get("prs") or []) for p in pr_cmds)
        pr_cmds_with_prs = sum(1 for p in pr_cmds if p.get("prs"))
        F["exact_commits"] = exact
        repo_cmd = f"clp bundle {F['bundle']} repo --json"
        w(f"- {MEASURED} Repository: `{repo.get('repo')}` (source: {repo_note})")
        w(f"- {MEASURED} **Commits the repository confirms: "
          f"{frac(confirmed_commits, len(commands), 'commit commands')}**, "
          "by how well each command matched a real commit:")
        for kind in MATCH_ORDER:
            label = "no match" if kind == "none" else kind
            w(f"  - {label}: {frac(by_match.get(kind, 0), len(commands), 'commit commands')}")
        w(f"  - {DERIVED} Only the `exact` matches carry a sha the command itself printed; "
          f"{frac(exact, max(confirmed_commits, 1), 'confirmed commits')} are that solid. The rest were "
          "matched by time, or by time and subject, so they are attributed rather than proven.")
        add_check(F, "Commits the repository confirms", MEASURED,
                  f"{num(confirmed_commits)} of {num(len(commands))} commit commands", repo_cmd,
                  derivation="a commit command counts as confirmed when clp bundle repo matched it to a "
                             "commit in the repository by any of `exact`, `time+subject`, `time` or "
                             "`ambiguous`.",
                  trap="most of these are attributed, not proven: only an `exact` match rests on a sha "
                       f"the command itself printed, and here that is {num(exact)} of "
                       f"{num(confirmed_commits)}. The rest were matched by time, or by time and "
                       "subject, so a commit made by something else in the same minute can take the "
                       "credit.")
        w(f"- {MEASURED} Commits in the repository over the session's span: "
          f"{num(repo.get('commits_in_span'))}. "
          f"Of those, {num(sum((repo.get('unattributed') or {}).values()))} came from something other than "
          "a commit command in this session"
          + (": " + ", ".join(f"{k} {num(v)}" for k, v in sorted(
              (repo.get("unattributed") or {}).items(), key=lambda x: -x[1])[:top]) if repo.get("unattributed") else "")
          + ".")
        w(f"  - {DOMAIN} Rebases and cherry-picks rewrite commits the session had already made, so this "
          "is not work from elsewhere.")
        w(f"- {MEASURED} **PRs GitHub confirms: {num(confirmed_prs)}**, created by "
          f"{frac(pr_cmds_with_prs, len(pr_cmds), 'PR commands')}"
          + (f"  ({repo.get('github')})" if repo.get("github") else ""))
        add_check(F, "PRs GitHub confirms", MEASURED, num(confirmed_prs), repo_cmd,
                  derivation="the PRs listed against the session's PR commands, counted once each.",
                  trap="this one asks GitHub, so it needs network and credentials; without them "
                       "clp bundle repo reports no PRs, which reads the same as a session that made "
                       "none.")

    # -- command output versus the repository
    w(f"- {MEASURED} What the command output alone claimed: "
      f"{frac(commit_claimed, commit_run, 'commit commands')} "
      f"printed a result the parser could confirm, and {frac(pr_claimed, pr_run, 'PR commands')} did.")
    add_check(F, "What the command output alone claimed", MEASURED,
              f"{num(commit_claimed)} of {num(commit_run)} commit commands, "
              f"{num(pr_claimed)} of {num(pr_run)} PR commands",
              sql_check(F["bundle"], "SELECT action, COUNT(*) AS commands, SUM(confirmed) AS "
                                     "self_confirmed, SUM(failed) AS failed FROM actions GROUP BY 1"))
    if confirmed_commits is not None:
        w(f"  - {DERIVED} Ratio, repository-confirmed to output-claimed commits: "
          f"{num(confirmed_commits)} : {num(commit_claimed)} = "
          + (f"{confirmed_commits / commit_claimed:.2f}x" if commit_claimed else "n/a")
          + ". Reading only the session's own output "
          + ("undercounts" if confirmed_commits > commit_claimed else "overcounts")
          + " the commits by that factor.")
        w(f"    - {INFERENCE} The session's own output is not a reliable record of what landed.")
    if confirmed_prs is not None:
        w(f"  - {DERIVED} Ratio, GitHub-confirmed to output-claimed PRs: {num(confirmed_prs)} : "
          f"{num(pr_claimed)} = "
          + (f"{confirmed_prs / pr_claimed:.2f}x" if pr_claimed else "n/a"))

    keys["CONFIRMED_COMMITS"] = confirmed_commits if confirmed_commits is not None else "unavailable"
    keys["CONFIRMED_PRS"] = confirmed_prs if confirmed_prs is not None else "unavailable"
    F["confirmed_commits"] = confirmed_commits
    F["confirmed_prs"] = confirmed_prs
    if confirmed_commits is not None or confirmed_prs is not None:
        F["confirmed_artifacts"] = (confirmed_commits or 0) + (confirmed_prs or 0)

    # -- files and tests
    files_changed = cat.one("SELECT COUNT(DISTINCT file_hash) FROM file_changes", default=0)
    edits = cat.one("SELECT COUNT(*) FROM file_changes", default=0)
    versions = cat.one("SELECT COUNT(*) FROM file_versions", default=0)
    backed_up = cat.one("SELECT COUNT(*) FROM file_versions WHERE backup IS NOT NULL", default=0)
    distinct_paths = cat.one("SELECT COUNT(DISTINCT path) FROM file_versions", default=0)
    w(f"- {MEASURED} Files changed: {num(files_changed)} distinct files, by {num(edits)} successful "
      f"edits and writes ({DERIVED} {rate(edits, files_changed, 1)} edits per file).")
    w(f"- {MEASURED} File versions the harness kept: {num(versions)} snapshots over "
      f"{num(distinct_paths)} paths, of which {frac(backed_up, versions, 'snapshots')} have a backup "
      "file on disk.")
    w(f"- {MEASURED} Test commands run: {num(test_run)}; of those "
      f"{frac(test_failed_runs, test_run, 'test commands')} failed as commands.")
    w(f"  - {DOMAIN} A test command that errored is not the same as a failing test: the runner never "
      "got as far as an assertion.")
    w(f"- {MEASURED} Assertions: {num(tests_passed)} passed, {num(tests_failed)} failed "
      f"({DERIVED} {pct(tests_passed, tests_passed + tests_failed)} pass rate over "
      f"{num(tests_passed + tests_failed)} assertions with a recorded count).")
    F.update(test_commands=test_run, test_command_failures=test_failed_runs,
             tests_passed=tests_passed, tests_failed=tests_failed)
    add_check(F, "Files changed and edits made", MEASURED,
              f"{num(files_changed)} files, {num(edits)} edits",
              sql_check(F["bundle"], "SELECT COUNT(DISTINCT file_hash) AS files, COUNT(*) AS edits "
                                     "FROM file_changes"))
    add_check(F, "Assertion pass rate", DERIVED,
              f"{pct(tests_passed, tests_passed + tests_failed)} "
              f"({num(tests_passed)} / {num(tests_passed + tests_failed)})",
              sql_check(F["bundle"], "SELECT COALESCE(SUM(tests_passed),0) AS passed, "
                                     "COALESCE(SUM(tests_failed),0) AS failed, COUNT(*) AS test_commands, "
                                     "COALESCE(SUM(failed),0) AS commands_errored FROM actions "
                                     "WHERE action='test'"),
              derivation="assertions that passed over assertions that passed or failed. It answers \"did "
                         "the tests that ran agree the work was right\" because an assertion is the "
                         "smallest thing a test suite reports a verdict on.",
              trap="the denominator counts only the test commands whose output carried a pass/fail "
                   "count, so a suite whose output the parser could not read is invisible here rather "
                   "than counted as failing.")

    if outcomes is None:
        w(f"- Per-turn outcome breakdown: UNAVAILABLE ({outcomes_note}). The totals above come from the "
          "catalog's `actions` and `file_changes` tables directly, so they still hold.")
    else:
        rows = [g for g in outcomes if (g.get("commit") or [0])[0] or (g.get("pr") or [0])[0]]
        rows.sort(key=lambda g: -((g.get("commit") or [0])[0] + (g.get("pr") or [0])[0]))
        w(f"- {MEASURED} Turns that produced a commit or a PR: {num(len(rows))} of {num(len(outcomes))} "
          f"groups clp bundle outcomes reports (source: {outcomes_note}). The busiest:")
        for g in rows[:top]:
            commit, pr = g.get("commit") or [0, 0], g.get("pr") or [0, 0]
            w(f"  - turn {g.get('group')}: {num(g.get('files'))} files, {num(g.get('edits'))} edits, "
              f"{commit[0]} commit commands ({commit[1]} self-confirmed), "
              f"{pr[0]} PR commands ({pr[1]} self-confirmed)")

    if repo is None:
        fire(alerts, "outcomes:repo-unconfirmed", 0)
    if tests_failed:
        fire(alerts, "outcomes:failing-assertions", tests_failed)
    if test_failed_runs:
        fire(alerts, "outcomes:test-commands-errored", test_failed_runs)
    if confirmed_commits is not None and commit_claimed and confirmed_commits / commit_claimed > 2:
        fire(alerts, "outcomes:command-output-undercounts-commits", confirmed_commits / commit_claimed)
    w("")


# ---------------------------------------------------------------------------
# 6. Harness faults
# ---------------------------------------------------------------------------

def section_harness(cat, multi, single_note, w, F, keys, alerts, top):
    w("## 6. Harness faults")

    api = cat.rows("""SELECT kind, status, cause, COUNT(*) FROM nodes
                       WHERE cause LIKE 'api-%' OR cause = 'timeout'
                       GROUP BY 1, 2, 3 ORDER BY 4 DESC""")
    api_errors = sum(n for *_, n in api)
    responses = F.get("responses") or 0
    F["api_errors"] = api_errors
    F["api_errors_per_1k"] = (1000.0 * api_errors / responses) if responses else None
    # Both sides of the rate in one statement: the per-cause breakdown alone would
    # print the numerator and leave the reader to find the denominator elsewhere.
    api_sql = sql_check(
        F["bundle"], "SELECT (SELECT COUNT(*) FROM nodes WHERE cause LIKE 'api-%' OR cause='timeout') "
                     "AS api_and_timeout_failures, (SELECT COUNT(*) FROM (SELECT 1 FROM events "
                     f"WHERE tokens_input IS NOT NULL GROUP BY {DEDUP_SQL})) AS responses")
    api_breakdown_sql = sql_check(
        F["bundle"], "SELECT kind, status, cause, COUNT(*) AS failures FROM nodes "
                     "WHERE cause LIKE 'api-%' OR cause='timeout' GROUP BY 1,2,3 ORDER BY 4 DESC")
    if api:
        w(f"- {MEASURED} API and timeout failures: {num(api_errors)} across attempts and agents"
          + (f" ({DERIVED} {F['api_errors_per_1k']:.2f} per 1,000 model responses, over "
             f"{num(responses)} responses in all)" if responses else "") + ":")
        for kind, status, cause, n in api:
            w(f"  - {kind} / {status} / {cause}: {num(n)}")
        add_check(F, "API and timeout failures per 1,000 responses", DERIVED,
                  f"{F['api_errors_per_1k']:.2f}" if responses else num(api_errors), api_sql,
                  derivation="attempts and agents whose recorded cause is an `api-*` status or a "
                             "timeout, over model responses, times 1,000. It answers \"how often did the "
                             "provider fail us\" because those causes are set by the runtime when a call "
                             "came back an error or never came back at all.",
                  trap="the numerator counts failed attempts and agents, not failed calls: one attempt "
                       "may have retried the same call several times, so this is a floor.")
        add_check(F, "API and timeout failures by cause", MEASURED,
                  f"{num(len(api))} kind/status/cause rows over {num(api_errors)} failures",
                  api_breakdown_sql)
    else:
        w(f"- {MEASURED} API and timeout failures: none recorded on any attempt or agent node.")
        # Not the per-cause breakdown: with nothing to group it prints a header and
        # no row, and a check that prints nothing cannot confirm a zero.
        add_check(F, "API and timeout failures", MEASURED, "0", api_sql)

    # -- the runtime-honesty check
    instances = cat.rows("SELECT id, label, status FROM nodes WHERE kind = 'workflow'")
    if not instances:
        w(f"- Reported status versus attempt reality: {single_note}")
        F["instances_total"] = 0
        F["instances_honest"] = 0
    else:
        dishonest = cat.rows("""
            SELECT w.id, w.label,
                   (SELECT COUNT(*) FROM nodes a WHERE a.kind = 'attempt' AND a.instance = w.id),
                   (SELECT COUNT(*) FROM nodes a WHERE a.kind = 'attempt' AND a.instance = w.id AND a.status <> 'ok')
              FROM nodes w
             WHERE w.kind = 'workflow' AND w.status = 'completed'
               AND EXISTS (SELECT 1 FROM nodes a WHERE a.kind = 'attempt'
                            AND a.instance = w.id AND a.status <> 'ok')
             ORDER BY 4 DESC""")
        completed = sum(1 for _, _, s in instances if s == "completed")
        F["instances_total"] = len(instances)
        F["instances_honest"] = len(instances) - len(dishonest)
        w(f"- {MEASURED} **Workflow instances that reported `completed` while holding a failing attempt: "
          f"{frac(len(dishonest), completed, 'instances that reported completed')}** "
          f"({frac(len(dishonest), len(instances), 'instances in all')}).")
        w(f"  - {INFERENCE} This is the runtime-honesty check: the status the runtime reported is not "
          "what its own attempts did.")
        for wid, label, total, bad in dishonest[: max(top, len(dishonest))]:
            w(f"  - `{label or wid}` (`{wid}`): {frac(bad, total, 'attempts')} did not end ok")
        add_check(F, "Workflow instances whose reported status misses a failing attempt", MEASURED,
                  f"{num(len(dishonest))} of {num(len(instances))} instances",
                  sql_check(F["bundle"], "SELECT (SELECT COUNT(*) FROM nodes WHERE kind='workflow') AS "
                                         "instances, (SELECT COUNT(*) FROM nodes w WHERE w.kind='workflow' "
                                         "AND w.status='completed' AND EXISTS (SELECT 1 FROM nodes a "
                                         "WHERE a.kind='attempt' AND a.instance=w.id AND a.status<>'ok')) "
                                         "AS misreported"))

    resumed = cat.rows("""SELECT w.id, w.label, w.status, json_extract(w.attrs, '$.resumed_from')
                            FROM nodes w WHERE w.kind = 'workflow'
                             AND json_extract(w.attrs, '$.resumed_from') IS NOT NULL""")
    F["resumed_instances"] = len(resumed)
    if instances:
        w(f"- {MEASURED} Resumed workflow instances: {frac(len(resumed), len(instances), 'instances')}.")
        w(f"  - {DOMAIN} A resume reuses the run id and makes a second instance, so the run's totals "
          "cover both.")
        for wid, label, status, src in resumed:
            w(f"  - `{label or wid}` resumed from `{src}`, second instance ended `{status}`")

    # -- what the catalog cannot see
    w("- Context compactions, truncated reads and permission denials: **not derivable from this catalog.** "
      "It stores each record's structure, not its text, and a compaction summary, a truncation notice and "
      "a permission refusal are all plain `user` records with no flag of their own. Reading them needs a "
      "text search of the archive, which this script does not run. Do not report them as zero.")

    # -- error rate per tool
    rows = cat.rows("""SELECT u.name, COUNT(*) AS calls, COALESCE(SUM(r.is_error), 0) AS errors
                         FROM event_tools u
                         JOIN event_tools r ON r.tool_use_id = u.tool_use_id AND r.role = 'result'
                        WHERE u.role = 'use' GROUP BY 1 ORDER BY errors DESC, calls DESC""")
    calls_total = sum(r[1] for r in rows)
    errors_total = sum(r[2] for r in rows)
    F.update(tool_calls=calls_total, tool_errors=errors_total)
    F["tool_error_rate"] = (errors_total / calls_total) if calls_total else None
    F["tool_rows"] = rows
    w(f"- {DERIVED} Tool error rate: {frac(errors_total, calls_total, 'completed tool calls')} returned "
      "an error.")
    tools_sql = sql_check(
        F["bundle"], "SELECT u.name, COUNT(*) AS calls, COALESCE(SUM(r.is_error),0) AS errors "
                     "FROM event_tools u JOIN event_tools r ON r.tool_use_id=u.tool_use_id "
                     "AND r.role='result' WHERE u.role='use' GROUP BY 1 ORDER BY errors DESC, calls DESC")
    if calls_total:
        add_check(F, "Tool error rate", DERIVED,
                  f"{pct(errors_total, calls_total)} ({num(errors_total)} / {num(calls_total)})",
                  sql_check(F["bundle"], "SELECT COUNT(*) AS completed_calls, "
                                         "COALESCE(SUM(r.is_error),0) AS errors FROM event_tools u "
                                         "JOIN event_tools r ON r.tool_use_id=u.tool_use_id "
                                         "AND r.role='result' WHERE u.role='use'"),
                  derivation="result records flagged `is_error` over result records paired to a use "
                             "record. It answers \"how often did a tool the agent reached for come back "
                             "unusable\" because a call with no result cannot be said to have errored, "
                             "so it is excluded from both sides rather than counted as a failure.",
                  trap="the denominator is results, not calls: a tool that emits two result records for "
                       "one call is counted twice. Use the Issued column for \"how many times did it "
                       "call X\".")
    issued = dict(cat.rows("SELECT name, COUNT(*) FROM event_tools WHERE role = 'use' GROUP BY 1"))
    issued_total = sum(issued.values())
    if calls_total != issued_total:
        w(f"  - The Calls column counts `result` records paired to a `use` record: {num(calls_total)} of "
          f"them answered {num(issued_total)} issued calls, because a few tools emit more than one result "
          "record for a single call. The Issued column is the call count; use it for anything phrased as "
          "\"how many times did it call X\".")
    if rows:
        add_check(F, "Tool calls and errors per tool", MEASURED,
                  f"one row per tool, {num(len(rows))} of them", tools_sql,
                  derivation="the Calls and Errors columns of the table above. The Issued column is a "
                             "separate count of `use` records, because a tool that emits two result "
                             "records for one call would otherwise be counted twice.")
    w("")
    w(f"| Tool | Issued {MEASURED} | Calls (results) {MEASURED} | Errors {MEASURED} "
      f"| Error rate {DERIVED} | Share of all calls {DERIVED} |")
    w("|---|---:|---:|---:|---:|---:|")
    for name, calls, errors in rows:
        w(f"| {name or '(unnamed)'} | {num(issued.get(name, 0))} | {num(calls)} | {num(errors)} "
          f"| {pct(errors, calls)} | {pct(calls, calls_total)} |")
    w("")

    # -- harness-injected records
    injected = cat.one("SELECT COUNT(*) FROM events WHERE kind = 'main' AND type = 'user' "
                       "AND COALESCE(human, 0) = 0", default=0)
    user_records = cat.one("SELECT COUNT(*) FROM events WHERE kind = 'main' AND type = 'user'", default=0)
    F["injected_share"] = (injected / user_records) if user_records else None
    w(f"- {MEASURED} Harness-injected records on the main thread: "
      f"{frac(injected, user_records, 'user-role records')} were not typed by the person - tool results, "
      "task notifications, command output and the like. Only the remainder are prompts.")
    add_check(F, "Harness-injected records on the main thread", MEASURED,
              f"{num(injected)} of {num(user_records)}",
              sql_check(F["bundle"], "SELECT COUNT(*) AS user_records, "
                                     "SUM(COALESCE(human,0)=0) AS injected FROM events "
                                     "WHERE kind='main' AND type='user'"))

    if api_errors:
        fire(alerts, "harness:api-errors", api_errors)
    if instances and F["instances_honest"] < len(instances):
        fire(alerts, "harness:status-misreported", len(instances) - F["instances_honest"])
    if F.get("launch_errors"):
        fire(alerts, "harness:launches-rejected", F["launch_errors"])
    if F.get("tool_error_rate") is not None and F["tool_error_rate"] > 0.03:
        fire(alerts, "harness:tool-error-rate-high", F["tool_error_rate"])
    w("")


# ---------------------------------------------------------------------------
# 7. Human loop
# ---------------------------------------------------------------------------

def section_human(cat, turns, turns_note, w, F, keys, alerts):
    w("## 7. Human loop")
    human_msgs = cat.one("SELECT COUNT(*) FROM events WHERE human = 1", default=0)
    total_events = cat.one("SELECT COUNT(*) FROM events", default=0)
    w(f"- {MEASURED} Messages the person actually wrote: "
      f"{frac(human_msgs, total_events, 'records in the bundle')}.")
    add_check(F, "Messages the person actually wrote", MEASURED,
              f"{num(human_msgs)} of {num(total_events)}",
              sql_check(F["bundle"], "SELECT (SELECT COUNT(*) FROM events WHERE human=1) AS "
                                     "human_messages, (SELECT COUNT(*) FROM events) AS records"))

    rows = cat.rows("SELECT kind, COUNT(*) FROM events WHERE interrupt = 1 GROUP BY 1 ORDER BY 2 DESC")
    total_int = sum(n for _, n in rows)
    human_int = sum(n for k, n in rows if k == "main")
    runtime_int = total_int - human_int
    F.update(human_interrupts=human_int, runtime_interrupts=runtime_int)
    w(f"- {MEASURED} Interrupts: {num(total_int)} in all, and they are two different things:")
    for kind, n in rows:
        if kind == "main":
            w(f"  - `{kind}`: {frac(n, total_int, 'interrupts')} - {DOMAIN} **a person pressing escape.** "
              "Main-thread interrupts are the only human ones.")
        else:
            w(f"  - `{kind}`: {frac(n, total_int, 'interrupts')} - {DOMAIN} **the runtime killing an "
              "agent that stopped making progress**, not a person. Counting these as human intervention "
              "would misread an automated stall-kill as impatience.")
    if not rows:
        w("  - none recorded.")
    if rows:
        add_check(F, "Interrupts, human and runtime", MEASURED,
                  f"{num(human_int)} human of {num(total_int)}",
                  sql_check(F["bundle"], "SELECT kind, COUNT(*) AS interrupts FROM events "
                                         "WHERE interrupt=1 GROUP BY 1 ORDER BY 2 DESC"),
                  derivation="only the `main` row is a person: the others are the runtime killing an agent.")

    w("- Permission denials: not derivable from this catalog (a denial is a plain errored tool result "
      "with no flag of its own, and the catalog holds no record text). Not zero - unknown.")

    asks = cat.one("""SELECT COUNT(*) FROM event_tools WHERE role = 'use' AND name = 'AskUserQuestion'""",
                   default=0)
    if turns:
        ask_waits = [x for x in turns["waits"] if x["tool"] == "AskUserQuestion"]
        ask_minutes = sum(x["minutes"] for x in ask_waits)
        F["ask_minutes"] = ask_minutes
        w(f"- {MEASURED} AskUserQuestion: {num(asks)} calls; the {num(len(ask_waits))} that clp session "
          f"turns listed among the longest waits cost {ask_minutes:.1f} minutes "
          f"({pct(ask_minutes, turns['minutes'].get('e2e') or 0)} of e2e). Shorter ones are not listed, "
          "so this is a floor.")
        add_check(F, "AskUserQuestion calls", MEASURED, num(asks),
                  sql_check(F["bundle"], "SELECT COUNT(*) AS calls FROM event_tools "
                                         "WHERE role='use' AND name='AskUserQuestion'"))
        retries = [t for t in turns["turns"] if RETRY_PROMPT.search(t["prompt"] or "")]
        F["manual_retries"] = len(retries)
        w(f"- {MEASURED} Turns whose prompt reads like a manual retry: "
          f"{frac(len(retries), len(turns['turns']), 'turns')} match /try again|retry|continue|keep going/i.")
        w(f"  - {INFERENCE} **This is a heuristic on the prompt text**, not a recorded fact: it will "
          'catch a genuine "continue with the next item" and miss a rephrased retry. Whether these were '
          "retries is an argument, not a measurement.")
        for t in retries[:10]:
            w(f'  - turn {t["turn"]} ({t["start"]}): "{(t["prompt"] or "")[:90]}"')
        add_check(F, "Turns whose prompt reads like a manual retry", MEASURED,
                  f"{num(len(retries))} of {num(len(turns['turns']))} turns", None,
                  note="the regex runs over the `prompt=` field of each TURN line, and a grep of the "
                       "whole line would also match the tool names and paths on it. Run "
                       f"`{F.get('turns_cmd') or 'clp session turns --top 0 <archive>'}` and apply "
                       "/try again|retry|continue|keep going/i to the prompt text alone.")
    else:
        w(f"- {MEASURED} AskUserQuestion: {num(asks)} calls. Their wait time and the manual-retry count "
          f"are UNAVAILABLE: {turns_note}")

    if F.get("manual_retries"):
        fire(alerts, "human:manual-retries", F["manual_retries"])
    if human_int:
        fire(alerts, "human:session-interrupted", human_int)
    w("")


# ---------------------------------------------------------------------------
# 8. Rework
# ---------------------------------------------------------------------------

def section_rework(cat, multi, single_note, w, F, keys, alerts):
    w("## 8. Rework")
    units = cat.one("SELECT COUNT(*) FROM nodes WHERE kind = 'unit'", default=0)
    if not units:
        w(f"- {single_note}")
        w("")
        return
    retried = cat.one("SELECT COUNT(*) FROM nodes WHERE kind = 'unit' AND attempts > 1", default=0)
    F.update(units=units, units_retried=retried)
    w(f"- {DERIVED} Logical units that needed more than one attempt: {frac(retried, units, 'units')}.")
    w(f"- {DERIVED} Stalled attempts: "
      f"{frac(F.get('attempts_stalled', 0), F.get('attempts_total', 0) or 1, 'attempts')}.")
    w(f"- {MEASURED} Resumed workflow instances: {num(F.get('resumed_instances', 0))} of "
      f"{num(F.get('instances_total', 0))}.")
    add_check(F, "Units that needed more than one attempt", DERIVED,
              f"{pct(retried, units)} ({num(retried)} / {num(units)})",
              sql_check(F["bundle"], "SELECT COUNT(*) AS units, SUM(attempts > 1) AS retried "
                                     "FROM nodes WHERE kind='unit'"),
              derivation="units whose `attempts` count is above one, over all units. It answers \"how "
                         "much of the work had to be done more than once\" because a unit is the "
                         "smallest piece of work the runtime will retry as a whole.")

    dist = cat.rows("SELECT attempts, COUNT(*) FROM nodes WHERE kind = 'unit' GROUP BY 1 ORDER BY 1")
    total_attempts_on_units = sum((a or 0) * n for a, n in dist)
    w(f"- Attempts per unit ({num(total_attempts_on_units)} attempts over {num(units)} units, "
      f"{DERIVED} {rate(total_attempts_on_units, units, 2)} per unit):")
    w("")
    w(f"| Attempts | Units {MEASURED} | Share of units {DERIVED} | Attempts spent here {DERIVED} |")
    w("|---:|---:|---:|---:|")
    for attempts, n in dist:
        w(f"| {attempts if attempts is not None else '(none)'} | {num(n)} | {pct(n, units)} "
          f"| {num((attempts or 0) * n)} |")
    w("")

    # -- did a unit that went wrong ever come right, without a person?
    recovery = cat.rows("""
        SELECT COALESCE(json_extract(u.attrs, '$.state'), '(no state)'), COUNT(*)
          FROM nodes u
         WHERE u.kind = 'unit'
           AND EXISTS (SELECT 1 FROM edges e JOIN nodes a ON a.id = e.dst
                        WHERE e.kind = 'contains' AND e.src = u.id
                          AND a.kind = 'attempt' AND a.status <> 'ok')
         GROUP BY 1 ORDER BY 2 DESC""")
    hurt = sum(n for _, n in recovery)
    done = dict(recovery).get("done", 0)
    F.update(units_hurt=hurt, units_recovered=done)
    if hurt:
        w(f"- {MEASURED} Units that hit at least one bad attempt: {frac(hurt, units, 'units')}, and "
          "where they ended:")
        for state, n in recovery:
            w(f"  - final state `{state}`: {frac(n, hurt, 'hurt units')}")
        w(f"  - {DERIVED} Self-recovery rate: {frac(done, hurt, 'units that hit a bad attempt')} still "
          "reached `done`, with no person involved.")
        add_check(F, "Self-recovery rate", DERIVED, f"{pct(done, hurt)} ({num(done)} / {num(hurt)})",
                  sql_check(F["bundle"], """SELECT COUNT(*) AS units_hurt,
                      COALESCE(SUM(json_extract(u.attrs,'$.state')='done'),0) AS reached_done
                      FROM nodes u WHERE u.kind='unit' AND EXISTS (SELECT 1 FROM edges e
                      JOIN nodes a ON a.id=e.dst WHERE e.kind='contains' AND e.src=u.id
                      AND a.kind='attempt' AND a.status<>'ok')"""),
                  derivation="units that reached the `done` state over units that hit at least one "
                             "non-ok attempt. It answers \"when something went wrong, did the system fix "
                             "it itself\" because the denominator is exactly the units that had something "
                             "to recover from.",
                  trap="no person appears in this denominator, so a unit a person rescued by hand is "
                       "counted here as a self-recovery; the human-loop figures in section 7 are what "
                       "tell you whether that happened.")
    else:
        w(f"- {MEASURED} Units that hit at least one bad attempt: none.")

    if units and retried / units > 0.10:
        fire(alerts, "rework:unit-retry-rate-high", retried / units)
    w("")


# ---------------------------------------------------------------------------
# 9. Scoring inputs
#
# The raw value behind each axis, and the numerator and denominator it came
# from. Nothing here is scored: no 0-10 number, no group mean, no composite.
# Applying a ladder to these values is the agent's job, against
# scoring-scale.json, so that a customer who disagrees with a threshold can edit
# one JSON file instead of patching this script.
# ---------------------------------------------------------------------------

def section_axes(w, F):
    """Section 9. Returns one record per axis, in id order, for stdout."""
    records = {}
    bundle = F.get("bundle", "<bundle>")
    turns_cmd = F.get("turns_cmd")

    def put(axis, value, components="", reason="", denominator=None, because="",
            check=None, check_note=""):
        """Record an axis. `value` None means it could not be measured, and then
        `reason` says why - it is never quietly turned into a zero, because a
        missing measurement and a bad one are different findings.

        `denominator` is the count the value was divided by, published so a
        consumer can see how many observations the rate rests on: a rate over a
        handful of them is noise whatever ladder reads it. It is given only where
        the divisor really is a count of things; where the divisor is a duration
        or another continuous quantity it is left out rather than faked, and the
        axis's rationale in the scale says so.

        `because` is what makes this ratio the right answer for this axis, and it
        is appended to the components: a numerator over a denominator is arithmetic,
        and a scorecard invites the question of why that arithmetic answers the
        question the axis is named after. `check` is the one command that
        reproduces the numerator and the denominator together; where no single
        command can, `check_note` says what a reader has to run instead."""
        detail = components
        if because and value is not None:
            detail = f"{components}; the right ratio for {AXES[axis]['label']} because {because}"
        records[axis] = {"id": axis, "group": AXES[axis]["group"], "unit": AXES[axis]["unit"],
                         "value": value, "components": detail, "reason": reason,
                         "denominator": denominator,
                         "check": check if value is not None else None,
                         "check_note": check_note}

    # A count of 0 out of 0 is not a bad measurement, it is a category that does
    # not exist in this session. SINGLE says so, instead of printing "0 of 0".
    SINGLE = "not applicable - single-threaded session"

    # -- Group A
    terminal_total = (F.get("attempts_total") or 0) + (F.get("agents_total") or 0)
    good = (F.get("attempts_ok") or 0) + (F.get("agents_completed") or 0)
    put("A1", (good / terminal_total) if terminal_total else None,
        f"{num(good)} of {num(terminal_total)} attempts+agents reached a good terminal state "
        f"({pct(good, terminal_total)})" if terminal_total else SINGLE,
        "the catalog has no attempt or agent nodes, so there is no terminal state to count",
        denominator=terminal_total or None,
        because="an attempt that ended `ok` and an agent that `completed` are the only two terminal "
                "states whose work the runtime keeps, so everything else is the platform failing to "
                "finish something it started",
        check=sql_check(bundle, "SELECT (SELECT COUNT(*) FROM nodes WHERE kind='attempt' AND "
                                "status='ok') + (SELECT COUNT(*) FROM nodes WHERE kind='agent' AND "
                                "status='completed') AS good_terminal, (SELECT COUNT(*) FROM nodes "
                                "WHERE kind IN ('attempt','agent')) AS attempts_and_agents"))

    put("A2", F.get("api_errors_per_1k"),
        f"{num(F.get('api_errors'))} API and timeout failures over {num(F.get('responses'))} "
        "model responses" if F.get("api_errors_per_1k") is not None else "no model responses",
        "no model response carries token usage, so there is no response count to divide by",
        denominator=F.get("responses") or None,
        because="a provider fault has to be read against how much was asked of the provider; per 1,000 "
                "responses is the rate that stays comparable between a short session and a long one",
        check=sql_check(bundle, "SELECT (SELECT COUNT(*) FROM nodes WHERE cause LIKE 'api-%' OR "
                                "cause='timeout') AS api_and_timeout_failures, (SELECT COUNT(*) FROM "
                                f"(SELECT 1 FROM events WHERE tokens_input IS NOT NULL GROUP BY "
                                f"{DEDUP_SQL})) AS responses"))

    hit = F.get("cache_hit_rate")
    cache_components = (f"cache_read {num(F.get('cache_read'))} of input {num(F.get('total_input'))} "
                        f"tokens ({pct(F.get('cache_read') or 0, F.get('total_input') or 0)})")
    cache_because = ("cache_read is the part of the input the provider served from a cache instead of "
                     "reading afresh, so the ratio is exactly the fraction of the context nobody had to "
                     "pay full price for")
    cache_check = sql_check(
        bundle, f"SELECT SUM(cr) AS cache_read, SUM(ti) AS input FROM (SELECT MAX(tokens_input) AS ti, "
                f"tokens_cache_read AS cr FROM events WHERE tokens_input IS NOT NULL GROUP BY "
                f"{DEDUP_SQL})")
    put("A3", hit, cache_components if hit is not None else "no input tokens",
        "no response records input tokens, so there is nothing to divide by",
        denominator=F.get("total_input") or None, because=cache_because, check=cache_check)

    launches = F.get("launches") or 0
    rejected = F.get("launch_rejected_config") or 0
    put("A4", (rejected / launches) if launches else None,
        f"{num(rejected)} of {num(launches)} launches were rejected for a config or syntax fault "
        f"({pct(rejected, launches)})" if launches else SINGLE,
        "the session launched no agent and no workflow",
        denominator=launches or None,
        because="a launch the runtime refused for a config or syntax fault never ran at all, so it is a "
                "fault in what was handed to the runtime rather than in anything the model then did",
        check=sql_check(bundle, f"SELECT (SELECT COUNT(*) FROM nodes WHERE kind='launch_error' AND "
                                f"({CONFIG_LIKE})) AS rejected_for_config, (SELECT COUNT(*) FROM edges "
                                f"WHERE kind='launch') + (SELECT COUNT(*) FROM nodes WHERE "
                                f"kind='launch_error') AS launches"),
        check_note="")

    inst = F.get("instances_total") or 0
    honest = F.get("instances_honest") or 0
    put("A5", (honest / inst) if inst else None,
        f"{num(honest)} of {num(inst)} workflow instances reported a status matching what their "
        f"attempts did ({pct(honest, inst)})" if inst else SINGLE,
        "no workflow instances, so there is no reported status to check against reality",
        denominator=inst or None,
        because="an instance that reports `completed` while holding a failing attempt tells its caller "
                "something its own records contradict, and a caller who cannot trust the status has to "
                "re-read the attempts itself",
        check=sql_check(bundle, "SELECT (SELECT COUNT(*) FROM nodes WHERE kind='workflow') AS instances, "
                                "(SELECT COUNT(*) FROM nodes w WHERE w.kind='workflow' AND "
                                "w.status='completed' AND EXISTS (SELECT 1 FROM nodes a WHERE "
                                "a.kind='attempt' AND a.instance=w.id AND a.status<>'ok')) AS "
                                "misreported"))

    # -- Group B
    err = F.get("tool_error_rate")
    tool_check = sql_check(
        bundle, "SELECT COUNT(*) AS completed_calls, COALESCE(SUM(r.is_error),0) AS errors, "
                "SUM(u.name='Bash') AS bash_calls, SUM(u.name IN "
                f"({', '.join(chr(39) + t + chr(39) for t in SEARCH_TOOLS)})) AS search_tool_calls "
                "FROM event_tools u JOIN event_tools r ON r.tool_use_id=u.tool_use_id "
                "AND r.role='result' WHERE u.role='use'")
    put("B1", err,
        f"{num(F.get('tool_errors'))} of {num(F.get('tool_calls'))} completed tool calls returned "
        f"an error ({pct(F.get('tool_errors') or 0, F.get('tool_calls') or 0)})"
        if err is not None else "no completed tool calls",
        "no tool call has both a use and a result record, so there is nothing to divide",
        denominator=F.get("tool_calls") or None,
        because="a call whose result came back flagged as an error is the agent having used a tool "
                "wrongly or on the wrong thing, and only calls that came back at all can be judged, "
                "so the denominator is results rather than calls issued",
        check=tool_check)

    rows = F.get("tool_rows") or []
    calls_total = sum(r[1] for r in rows)
    bash = sum(r[1] for r in rows if r[0] == "Bash")
    search = sum(r[1] for r in rows if r[0] in SEARCH_TOOLS)
    put("B2", (bash / calls_total) if calls_total else None,
        f"Bash {num(bash)} of {num(calls_total)} tool calls ({pct(bash, calls_total)}); dedicated "
        f"search tools ({', '.join(SEARCH_TOOLS)}) were used {num(search)} times"
        if calls_total else "no tool calls",
        "the session made no tool call",
        denominator=calls_total or None,
        because="Bash is the tool that can do anything, so reaching for it where a purpose-built tool "
                "exists is the measurable trace of a poor choice; the search-tool count beside it is "
                "what a scorer needs to tell a justified shell call from a lazy one",
        check=tool_check)

    attempts_total = F.get("attempts_total") or 0
    not_kept = (attempts_total - (F.get("attempts_ok") or 0)) + (F.get("discarded_ok_attempts") or 0)
    put("B3", (not_kept / attempts_total) if attempts_total else None,
        f"{num(not_kept)} of {num(attempts_total)} attempts reached no kept result "
        f"({pct(not_kept, attempts_total)}), counting {num(F.get('discarded_ok_attempts'))} that "
        "succeeded inside a cancelled instance" if attempts_total else SINGLE,
        "no attempts, so there is no retry to count",
        denominator=attempts_total or None,
        because="an attempt whose result was not kept has to be done again by somebody, whether it "
                "failed or whether it succeeded inside an instance that was then cancelled",
        check=sql_check(bundle, "SELECT (SELECT COUNT(*) FROM nodes WHERE kind='attempt' AND "
                                "status<>'ok') + (SELECT COUNT(*) FROM nodes WHERE kind='attempt' AND "
                                "status='ok' AND instance IN (SELECT id FROM nodes WHERE "
                                "kind='workflow' AND status IN ('killed','aborted'))) AS "
                                "no_kept_result, (SELECT COUNT(*) FROM nodes WHERE kind='attempt') "
                                "AS attempts"))

    put("B4", None, "compactions are not recorded in the catalog",
        "the catalog stores each record's structure, not its text, and a compaction summary is a "
        "plain user record with no flag of its own; counting them needs a text search of the main "
        "archive, which this script does not run. This is unknown, not zero.")

    total_min = (F.get("attempt_minutes") or 0) + (F.get("agent_minutes") or 0)
    useful = F.get("useful_minutes")
    put("B5", (useful / total_min) if (useful is not None and total_min) else None,
        f"{useful:,.0f} of {total_min:,.0f} agent-minutes went to work that survived "
        f"({pct(useful or 0, total_min)})" if total_min else SINGLE,
        "no agent or attempt node carries both a start and an end, so there are no minutes to divide",
        because="orchestration is judged on what it did with the fleet's time, and a minute spent on an "
                "attempt whose output was thrown away bought nothing however well the attempt ran",
        check=sql_check(bundle, "SELECT ROUND(SUM((julianday(end)-julianday(start))*1440.0),0) AS "
                                "agent_minutes, ROUND(SUM(CASE WHEN (kind='attempt' AND status='ok' AND "
                                "(instance IS NULL OR instance NOT IN (SELECT id FROM nodes WHERE "
                                "kind='workflow' AND status IN ('killed','aborted')))) OR "
                                "(kind='agent' AND status='completed') THEN "
                                "(julianday(end)-julianday(start))*1440.0 END),0) AS survived_minutes "
                                "FROM nodes WHERE kind IN ('attempt','agent') AND start IS NOT NULL "
                                "AND end IS NOT NULL"))

    # -- Group C
    artifacts = F.get("confirmed_artifacts")
    model_hours = ((F.get("minutes") or {}).get("model") or 0) / 60.0
    if artifacts is None:
        put("C1", None, "no repository confirmation",
            "clp bundle repo was unavailable, so no artifact can be called confirmed")
    elif not model_hours:
        put("C1", None, f"{num(artifacts)} confirmed artifacts, model time unknown",
            "clp session turns was unavailable, so there is no model-hour denominator")
    else:
        put("C1", artifacts / model_hours,
            f"{num(artifacts)} repository-confirmed artifacts over {model_hours:.1f} model-hours. "
            "COHORT-RELATIVE: comparable only against other sessions doing the same kind of work, "
            "never as an absolute",
            because="throughput has to be read against the time the model was actually thinking, not "
                    "against elapsed time that includes waiting on a person",
            check_note="the artifacts come from `clp bundle " + bundle +
                       " repo --json` and the model-hours from `" +
                       (turns_cmd or "clp session turns --top 0 <archive>") +
                       "`, whose TOTAL_MIN line carries model=. Each input is one command; the ratio "
                       "is not.")

    cc = F.get("confirmed_commits")
    put("C2", ((F.get("exact_commits") or 0) / cc) if cc else None,
        f"{num(F.get('exact_commits'))} of {num(cc)} repository-confirmed commits matched exactly, "
        f"by a sha the command itself printed ({pct(F.get('exact_commits') or 0, cc)})" if cc
        else "no repository-confirmed commits",
        "clp bundle repo was unavailable" if cc is None else "the session confirmed no commits",
        denominator=cc or None,
        because="only an `exact` match rests on a sha the command printed; the rest are attributed by "
                "time and subject, so this ratio is how much of the delivery claim is proven rather "
                "than inferred",
        check=f"clp bundle {bundle} repo --json | python3 -c \"import json,sys; "
              "c=json.load(sys.stdin)['commits']; "
              "print(sum(1 for x in c if x.get('match')=='exact'), 'exact of', "
              "sum(1 for x in c if (x.get('match') or 'none')!='none'), 'confirmed')\"")

    assertions = (F.get("tests_passed") or 0) + (F.get("tests_failed") or 0)
    commands = F.get("test_commands") or 0
    put("C3", ((F.get("tests_passed") or 0) / assertions) if assertions else None,
        f"{num(F.get('tests_passed'))} of {num(assertions)} assertions passed "
        f"({pct(F.get('tests_passed') or 0, assertions)}), over {num(commands)} test commands "
        f"({num(F.get('test_command_failures'))} of which errored as commands)"
        + (f", against {num(artifacts)} confirmed artifacts" if artifacts else "")
        if assertions else f"{num(commands)} test commands, none with a recorded assertion count",
        "no test command reported how many assertions passed or failed",
        denominator=assertions or None,
        because="an assertion is the smallest thing a suite returns a verdict on, so the pass rate is "
                "the finest-grained evidence the session produced that its work was right; the command "
                "count beside it is what says whether enough was checked at all",
        check=sql_check(bundle, "SELECT COALESCE(SUM(tests_passed),0) AS passed, "
                                "COALESCE(SUM(tests_failed),0) AS failed, COUNT(*) AS test_commands, "
                                "COALESCE(SUM(failed),0) AS commands_errored FROM actions "
                                "WHERE action='test'"))

    share = F.get("human_idle_share")
    put("C4", (1 - share) if share is not None else None,
        f"human and idle together are {share:.1%} of the {(F.get('minutes') or {}).get('e2e', 0):.1f} "
        f"end-to-end minutes, so {1 - share:.1%} of the session ran without waiting"
        if share is not None else "no time split",
        "clp session turns was unavailable, so there is no human/idle split",
        because="the five time buckets partition end-to-end time without double-counting a second, so "
                "one minus the waiting share is exactly the part of the session that ran on its own",
        check=turns_cmd,
        check_note="" if turns_cmd else "the turns run was supplied as a file, so the command behind it "
                                        "is not recorded here; re-run clp session turns on the main "
                                        "archive and read its TOTAL_MIN line")

    hurt = F.get("units_hurt")
    put("C5", ((F.get("units_recovered") or 0) / hurt) if hurt else None,
        f"{num(F.get('units_recovered'))} of {num(hurt)} units that hit a bad attempt still reached "
        f"done, with no person involved ({pct(F.get('units_recovered') or 0, hurt)})" if hurt
        else "no unit hit a bad attempt",
        "no unit hit a bad attempt, so there was nothing to recover from",
        denominator=hurt or None,
        because="the denominator is exactly the units that had something to recover from, so the ratio "
                "answers what happened when things went wrong rather than how often they went wrong",
        check=sql_check(bundle, """SELECT COUNT(*) AS units_hurt,
            COALESCE(SUM(json_extract(u.attrs,'$.state')='done'),0) AS reached_done FROM nodes u
            WHERE u.kind='unit' AND EXISTS (SELECT 1 FROM edges e JOIN nodes a ON a.id=e.dst
            WHERE e.kind='contains' AND e.src=u.id AND a.kind='attempt' AND a.status<>'ok')"""))

    # -- Group D
    per_artifact = F.get("tokens_per_artifact")
    put("D1", per_artifact,
        f"{num(F.get('total_input'))} input tokens over {num(artifacts)} repository-confirmed "
        "artifacts" if per_artifact else "no confirmed artifacts",
        "clp bundle repo was unavailable, so there is no confirmed-artifact denominator",
        denominator=artifacts or None,
        because="the bill is in input tokens and the only output anyone can check is what the repository "
                "confirms, so this is what one confirmed artifact actually cost",
        check_note="the tokens come from the `Total input tokens` check in this "
                   "section and the artifacts from the `Commits the repository confirms` and `PRs "
                   "GitHub confirms` checks beside it. Each input is one command; the ratio is not.")

    waste = F.get("waste_share")
    put("D2", waste,
        f"{num(F.get('waste_tokens'))} of {num(F.get('total_input'))} input tokens were wasted "
        f"({pct(F.get('waste_tokens') or 0, F.get('total_input') or 0)})" if waste is not None
        else SINGLE,
        "nothing in this session carries an outcome, so no token can be shown to be wasted",
        denominator=(F.get("total_input") or None) if waste is not None else None,
        because="an attempt or agent that reached no kept result produced nothing that was used, so its "
                "input tokens are what was paid for nothing",
        check=sql_check(
            bundle,
            "SELECT (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE kind='attempt' AND "
            "status<>'ok') + (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE kind='agent' AND "
            "status<>'completed') + (SELECT COALESCE(SUM(tokens_input),0) FROM nodes WHERE "
            "kind='attempt' AND status='ok' AND instance IN (SELECT id FROM nodes WHERE "
            "kind='workflow' AND status IN ('killed','aborted'))) AS wasted_input, "
            f"(SELECT SUM(ti) FROM (SELECT MAX(tokens_input) AS ti FROM events WHERE tokens_input IS "
            f"NOT NULL GROUP BY {DEDUP_SQL})) AS total_input"))

    # The same measurement as A3, on purpose - see the comment on AXES["D3"].
    put("D3", hit, cache_components if hit is not None else "no input tokens",
        "no response records input tokens, so there is nothing to divide by",
        denominator=F.get("total_input") or None, because=cache_because, check=cache_check)

    put("D4", None, "no model is recorded against any response",
        "the catalog records no model for the responses, so neither the token share by model nor "
        "the stall rate to weight it by can be computed")

    # Either half may be missing - the turn share needs clp session turns and
    # the run share needs workflow runs - so each is described on its own and the
    # axis takes whichever halves exist.
    total_in = F.get("total_input") or 0
    parts, shares = [], []
    for what, value, why in (("turn", F.get("largest_turn_input"), "clp session turns was unavailable"),
                             ("run", F.get("largest_run_input"), "the session ran no workflows")):
        if value and total_in:
            shares.append(value / total_in)
            parts.append(f"largest {what} {num(value)} ({pct(value, total_in)} of input)")
        else:
            parts.append(f"largest {what} unknown ({why})")
    put("D5", max(shares) if shares else None, "; ".join(parts),
        "neither the largest turn nor the largest run could be measured",
        denominator=(total_in or None) if shares else None,
        because="a bill concentrated in one turn or one run is a different problem from the same bill "
                "spread evenly: it can be cut by fixing one thing, and it can also be an artefact of one "
                "deliberate batch, which is why this axis is gated on the declared workload shape",
        check_note="the largest turn comes from `" +
                   (turns_cmd or "clp session turns --top 0 <archive>") +
                   "` (the largest `tokens_in=` on a TURN line), the largest run from the `Largest "
                   "workflow run by input tokens` check in this section, and the denominator from the "
                   "`Total input tokens` check beside it. Each input is one command; the share is not.")

    # -- render
    w("## 9. Scoring inputs (raw measurements; this script does not score them)")
    w(wrap("Each row is one axis's raw value and the numerator and denominator behind it. There are "
           "deliberately no scores here, and no group means: what a value on an axis is worth is a "
           "judgement that differs between customers, so it lives in the scale file "
           "(scoring-scale.json) and an agent applies it. An axis that could not be measured says "
           "n/a and why; it is not a zero, and a scorer must leave it out of its group rather than "
           "count it against the session."))
    w("")
    w(wrap(f"Every axis value is {DERIVED} derived - each one is a ratio or a rate over measured "
           "counts - so the marker is on the value and the Components column carries the derivation: "
           "the numerator, the denominator, and what makes that ratio the right answer for that axis. "
           "Section 10 gives each axis the one command that reproduces its numerator and denominator "
           "together, or says why no single command can."))
    w("")
    w(wrap("The scale reports the four groups separately and never averages them into one number. "
           "The groups have different owners - infra and the gateway, the model and harness, the work "
           "itself, and whoever pays the bill - and a composite hides exactly the signal each owner "
           "needs."))
    w("")
    ordered = [a for g in "ABCD" for a in sorted(AXES) if AXES[a]["group"] == g]
    for group in "ABCD":
        w(f"### Group {group}: {GROUP_NAMES[group]}")
        w("")
        w("| Axis | Measures | Unit | Raw value | Denominator | Components it came from |")
        w("|---|---|---|---:|---:|---|")
        for axis in [a for a in ordered if AXES[a]["group"] == group]:
            rec, spec = records[axis], AXES[axis]
            cell = f"{DERIVED} {bare(rec['value'])}" if rec["value"] is not None else "n/a"
            den = num(rec["denominator"]) if rec.get("denominator") else "-"
            w(f"| {axis} {spec['label']} | {spec['measures']} | {spec['unit']} | {cell} "
              f"| {den} | {rec['components']} |")
        w("")
        in_group = [a for a in ordered if AXES[a]["group"] == group]
        missing = [a for a in in_group if records[a]["value"] is None]
        if missing:
            for axis in missing:
                w(f"- {axis} is n/a: {records[axis]['reason']}")
            w(f"- Group {group} can therefore be scored on {len(in_group) - len(missing)} of its "
              f"{len(in_group)} axes. {', '.join(missing)} "
              + ("was" if len(missing) == 1 else "were")
              + " not measured, so say the mean rests on "
              f"{len(in_group) - len(missing)} axes rather than scoring "
              + ("it" if len(missing) == 1 else "them") + " zero.")
            w("")
    return [records[a] for a in ordered]


# ---------------------------------------------------------------------------
# 10. Verification
#
# The long form of every figure's provenance, kept here rather than on the line
# that states the figure. A facts file whose every line triples in length is
# worse for a reader than one that is merely unlabelled, so the body carries a
# three-character marker and the inputs, and this section carries the command,
# the formula, the reason the formula answers the question, and the trap.
# ---------------------------------------------------------------------------

def section_verification(w, F, axis_rows):
    """Section 10: one command per headline figure and per axis."""
    checks = list(F.get("checks") or [])
    for row in axis_rows or []:
        if row["value"] is None:
            continue
        checks.append({
            "name": f"Axis {row['id']} {AXES[row['id']]['label']}", "tier": DERIVED,
            "value": bare(row["value"]),
            "command": row.get("check"), "derivation": row["components"], "trap": "",
            "note": row.get("check_note") or "no single command reproduces it",
        })
    if not checks:
        return
    w("## 10. Verification - the one command behind each figure")
    w(wrap("Copy a command, run it, and compare. The commands name this plugin's own `clp` with no "
           "path, so nothing here records one machine's install layout: run them with the plugin's "
           "`bin/` on $PATH. " + CATALOG_NOTE))
    w("")
    w(wrap("A figure two tools have to answer together gets no command: it says which two, and what "
           "each one checks. A command that only looks like a check is worse than none, because a "
           "reader who runs it and gets a different number concludes the figure is wrong."))
    w("")
    unverifiable = 0
    for c in checks:
        w(f"- **{c['name']}** `{c['tier']}` = {c['value']}")
        if c.get("derivation"):
            w(f"  - Derivation: {c['derivation']}")
        if c.get("trap"):
            w(f"  - Trap: {c['trap']}")
        if c.get("command"):
            w(f"  - Check: `{c['command']}`")
        else:
            unverifiable += 1
            w(f"  - **Not checkable in one command**: {c.get('note') or NO_REASON}")
    w("")
    w(f"{len(checks) - unverifiable} of {len(checks)} figures above are reproduced by a single command. "
      + verification_tail(unverifiable))
    w("")


def verification_tail(unverifiable):
    """The sentence that closes section 10."""
    if not unverifiable:
        return "Every one of them is."
    if unverifiable == 1:
        return "The other one says what it needs instead."
    return f"The other {unverifiable} say what they need instead."


# ---------------------------------------------------------------------------
# --check-scale: keep a customer's edited scale honest
#
# The script never scores, so it never reads a ladder to use it. It reads one
# only to check it: that every axis this script can emit is present, and that
# every ladder could actually produce a score for any value it might see. A
# ladder with a gap in it silently drops a session into no rung at all, which is
# worse than a threshold someone disagrees with.
# ---------------------------------------------------------------------------

DIRECTIONS = ("higher_is_better", "lower_is_better")

# What an axis's thresholds may rest on. A scale that does not say is not a
# valid scale: a customer recalibrating needs to know which rungs were reasoned
# and which were measured, and the honest answer for most of them is `judgement`.
BASES = ("definitional", "mechanism", "observed", "judgement")


def exhaustive_bound(direction, unit):
    """The bound the last rung must reach for the ladder to cover every value.

    Downward there is a floor - nothing is below zero. Upward there is none, so a
    share has to reach 1.0 and an open-ended unit has to reach a sentinel large
    enough that no real session passes it.
    """
    if direction == "higher_is_better":
        return 0.0
    return 1.0 if unit == "share" else 1e12


def check_scale(path):
    """Problems with a scale file, as (slug, detail) pairs. Empty means it is sound."""
    problems = []
    try:
        scale = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return [("scale-unreadable", f"{path}: {exc}")]
    except ValueError as exc:
        return [("scale-not-json", f"{path}: {exc}")]
    if not isinstance(scale, dict):
        return [("scale-not-an-object", f"{path} holds {type(scale).__name__}, not an object")]

    for field in ("scale_version", "name", "notes", "calibration", "groups", "axes"):
        if field not in scale:
            problems.append(("missing-field", f"the scale has no `{field}`"))
    groups = scale.get("groups") or {}
    for group in "ABCD":
        if group not in groups:
            problems.append(("missing-group", f"group {group} ({GROUP_NAMES[group]}) is not in `groups`"))
        elif not (groups[group] or {}).get("label"):
            problems.append(("group-unlabelled", f"group {group} has no label"))

    problems.extend(check_cohort_keys(scale.get("cohort_keys")))
    axes = scale.get("axes")
    if not isinstance(axes, list):
        problems.append(("axes-not-a-list", "`axes` must be a list of axis objects"))
        return problems

    seen = {}
    for i, axis in enumerate(axes):
        if not isinstance(axis, dict) or not axis.get("id"):
            problems.append(("axis-without-id", f"axes[{i}] has no `id`"))
            continue
        aid = axis["id"]
        if aid in seen:
            problems.append(("axis-duplicated", f"{aid} appears more than once"))
            continue
        seen[aid] = axis
        if aid not in AXES:
            problems.append(("axis-unknown", f"{aid} is in the scale but this script never emits it"))
            continue
        spec = AXES[aid]
        if axis.get("group") != spec["group"]:
            problems.append(("axis-wrong-group",
                             f"{aid} is group {axis.get('group')!r} in the scale, "
                             f"{spec['group']!r} in the script"))
        if axis.get("unit") != spec["unit"]:
            problems.append(("axis-wrong-unit",
                             f"{aid} is unit {axis.get('unit')!r} in the scale, "
                             f"{spec['unit']!r} in the script"))
        if bool(axis.get("cohort_relative")) != bool(spec.get("cohort")):
            problems.append(("axis-wrong-cohort-flag",
                             f"{aid} has cohort_relative={axis.get('cohort_relative')!r}, "
                             f"the script says {bool(spec.get('cohort'))}"))
        problems.extend(check_justification(aid, axis))
        problems.extend(check_gate(aid, axis, scale.get("cohort_keys") or {}))
        floor = axis.get("min_denominator")
        if floor is not None and (not isinstance(floor, int) or isinstance(floor, bool) or floor < 0):
            problems.append(("axis-bad-min-denominator",
                             f"{aid} has min_denominator {floor!r}; it must be a non-negative "
                             "integer count of observations, or be left out"))
        direction = axis.get("direction")
        if direction not in DIRECTIONS:
            problems.append(("axis-bad-direction",
                             f"{aid} has direction {direction!r}, expected one of {DIRECTIONS}"))
            continue
        problems.extend(check_ladder(aid, axis, direction))

    for aid in sorted(AXES):
        if aid not in seen:
            problems.append(("axis-missing",
                             f"{aid} ({AXES[aid]['label']}) is emitted by this script but is not "
                             "in the scale, so it could never be scored"))
    return problems


def check_cohort_keys(declared):
    """Problems with the scale's cohort vocabulary.

    A gate names a cohort key and the values it accepts. Declaring the vocabulary
    in the scale rather than hardcoding it here is what lets a customer add a
    dimension of their own without editing Python.
    """
    if declared is None:
        return []
    if not isinstance(declared, dict):
        return [("bad-cohort-keys", "`cohort_keys` must be an object mapping a key to its values")]
    problems = []
    for key, spec in declared.items():
        if not isinstance(spec, dict):
            problems.append(("bad-cohort-keys", f"cohort_keys.{key} is not an object"))
            continue
        values = spec.get("values")
        if not isinstance(values, list) or not values or not all(
                isinstance(v, str) and v.strip() for v in values):
            problems.append(("bad-cohort-keys",
                             f"cohort_keys.{key} needs a non-empty `values` array of strings"))
        if not isinstance(spec.get("describes"), str) or not spec["describes"].strip():
            problems.append(("bad-cohort-keys",
                             f"cohort_keys.{key} needs a `describes` saying what the key means"))
    return problems


def check_gate(aid, axis, declared):
    """Problems with an axis's gate.

    A gate says an axis only means something under one declared intent. A gate
    that names a key or a value the scale never declares would silently never
    fire, so it is a defect and not a quirk.
    """
    gate = axis.get("gated_by")
    if gate is None:
        return []
    if not isinstance(gate, dict):
        return [("axis-bad-gate", f"{aid} has a `gated_by` that is not an object")]
    problems = []
    for field in ("cohort_key", "skip_reason", "undeclared_reason"):
        if not isinstance(gate.get(field), str) or not gate[field].strip():
            problems.append(("axis-bad-gate", f"{aid} gate has no `{field}`"))
    when = gate.get("score_when")
    if not isinstance(when, list) or not when or not all(isinstance(v, str) for v in when):
        problems.append(("axis-bad-gate",
                         f"{aid} gate needs a non-empty `score_when` array of values"))
        when = []
    key = gate.get("cohort_key")
    if not isinstance(key, str) or not key.strip():
        return problems
    spec = declared.get(key)
    if spec is None:
        problems.append(("axis-bad-gate", f"{aid} gates on cohort key {key!r}, which the scale's "
                                          "`cohort_keys` does not declare"))
        return problems
    allowed = (spec or {}).get("values") or []
    for value in when:
        if value not in allowed:
            problems.append(("axis-bad-gate",
                             f"{aid} gate scores when {key}={value!r}, which is not one of that "
                             f"key's declared values {allowed}"))
    return problems


def check_justification(aid, axis):
    """Problems with an axis's stated basis, rationale and anchors."""
    problems = []
    basis = axis.get("basis")
    if basis is None:
        problems.append(("axis-missing-basis", f"{aid} has no `basis`; a threshold must say what it "
                                               f"rests on, one of {BASES}"))
    elif basis not in BASES:
        problems.append(("axis-bad-basis", f"{aid} has basis {basis!r}, expected one of {BASES}"))
    rationale = axis.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        problems.append(("axis-missing-basis", f"{aid} has no `rationale`; a threshold nobody can "
                                               "argue with is a threshold nobody can correct"))
    anchors = axis.get("anchors")
    if anchors is None:
        return problems
    if not isinstance(anchors, list) or not anchors:
        problems.append(("axis-bad-anchors", f"{aid} has `anchors` that is not a non-empty list"))
        return problems
    for i, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            problems.append(("axis-bad-anchors", f"{aid} anchors[{i}] is not an object"))
            continue
        value = anchor.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(("axis-bad-anchors", f"{aid} anchors[{i}] has value {value!r}, "
                                                 "expected a number"))
        if not isinstance(anchor.get("means"), str) or not anchor["means"].strip():
            problems.append(("axis-bad-anchors", f"{aid} anchors[{i}] has no `means` saying what "
                                                 "that value represents"))
    return problems


def check_ladder(aid, axis, direction):
    """Problems with one axis's ladder: shape, ordering, reachability, coverage."""
    problems = []
    key = "min" if direction == "higher_is_better" else "max"
    ladder = axis.get("ladder")
    if not isinstance(ladder, list) or not ladder:
        return [("ladder-empty", f"{aid} has no ladder")]

    scores, bounds = [], []
    for j, rung in enumerate(ladder):
        if not isinstance(rung, dict):
            problems.append(("rung-not-an-object", f"{aid} ladder[{j}] is not an object"))
            continue
        if not isinstance(rung.get("score"), int) or not 0 <= rung["score"] <= 10:
            problems.append(("rung-bad-score",
                             f"{aid} ladder[{j}] has score {rung.get('score')!r}, "
                             "expected an integer 0-10"))
        else:
            scores.append(rung["score"])
        other = "max" if key == "min" else "min"
        if key not in rung:
            problems.append(("rung-missing-bound",
                             f"{aid} ladder[{j}] has no `{key}` (a {direction} ladder is "
                             f"bounded by `{key}`)"))
        elif not isinstance(rung[key], (int, float)) or isinstance(rung[key], bool):
            problems.append(("rung-bound-not-a-number",
                             f"{aid} ladder[{j}] has {key}={rung[key]!r}"))
        else:
            bounds.append(float(rung[key]))
        if other in rung:
            problems.append(("rung-wrong-bound",
                             f"{aid} ladder[{j}] also carries `{other}`, which a {direction} "
                             "ladder must not use"))
    if len(bounds) != len(ladder) or len(scores) != len(ladder):
        return problems   # a malformed rung makes the ordering checks meaningless

    if scores != sorted(scores, reverse=True) or len(set(scores)) != len(scores):
        problems.append(("ladder-unordered",
                         f"{aid} scores run {scores}; they must descend strictly, highest first, "
                         "so the first rung a value satisfies is its score"))
    # Reachability: for higher_is_better the bounds must fall as the score falls;
    # for lower_is_better they must rise. A repeated or out-of-order bound makes a
    # rung unreachable, because an earlier rung already caught every value.
    rising = key == "max"
    ordered_ok = all((bounds[i] < bounds[i + 1]) if rising else (bounds[i] > bounds[i + 1])
                     for i in range(len(bounds) - 1))
    if not ordered_ok:
        problems.append(("ladder-rung-unreachable",
                         f"{aid} `{key}` bounds run {bounds}; they must "
                         + ("rise" if rising else "fall")
                         + " strictly, or a rung can never be reached"))
    required = exhaustive_bound(direction, axis.get("unit"))
    last = bounds[-1]
    covers = last <= required if key == "min" else last >= required
    if not covers:
        problems.append(("ladder-not-exhaustive",
                         f"{aid} ends at {key}={last:g}, so a value "
                         + (f"below {last:g}" if key == "min" else f"above {last:g}")
                         + f" falls off the ladder with no score; the last rung must be "
                           f"unconditional ({key}={required:g})"))
    return problems

