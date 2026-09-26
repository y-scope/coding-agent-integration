"""
clp-insights facts - compute every number of a log-insights report in
code, so the report writer only has to put them into words
(log-insights skill, step 7).

A small model asked to add up a category table, pick the right severity count,
or work out a time span from example records gets them wrong: in a trial it
reported 49 warnings for 92, 5,370 templates for 11,558, and a 9.5-minute span
for a 74-hour log. Nothing here needs judgement, so nothing here is left to it.

Inputs (all produced earlier in the run):
  --archive-dir DIR      The top-level archive directory. Its
                         .yscope-clp-archive.json gives the time span: the
                         timeRange clp-s-compress-folder records from clp-s
                         when it compresses with --timestamp-key.
  --baseline-results-file F
                         The baseline pool's results (default:
                         /tmp/clp-insights-baseline-results.ndjson). Its entries
                         (origin "baseline") carry the severity and logger
                         breakdown; "follow-up of N" entries carry the fetched
                         records behind a small count.
  --results-file F       The classified plan's results (default:
                         /tmp/clp-insights-query-results.ndjson). The two are
                         numbered separately, so a query is cited as
                         [baseline #N] or [plan #N].
  --category-totals F    clp-insights extract's {category: {templates,
                         records, priority, why}} (default:
                         /tmp/log-shape-category-totals.json, or "none" when
                         frequencies were unavailable)
  --focus-file F         clp-insights focus's record of the user's focus and
                         context (default: /tmp/clp-insights-focus.json; absent
                         when no one was asked). Its section comes first, with
                         the results of the focus entries (origin "focus").
  --top-templates-file F The extract's top templates, overall and per category,
                         each with its category and exact count (default:
                         /tmp/log-shape-top-templates.json). Without it the top
                         templates come from --freqs-file, without categories.
  --freqs-file F         Per-template counts, most frequent first (default:
                         /tmp/log-shape-freqs.ndjson, or "none")
  --schema-json JSON     The classification schema (the extract's SCHEMA= line)
  --top N                Templates and message groups to list (default: 20)
  --out F                Where to write the facts (default:
                         /tmp/clp-insights-facts.md)

Every section says where its numbers come from. What cannot be known is said
to be unavailable rather than estimated, with the reason: notably the archive's
time span when the metadata has no timeRange. The fetched records' own first
and last timestamp are given, labelled as covering only those records.

Every figure also carries its provenance, because a reader cannot otherwise
tell four different kinds of claim apart: [M] a number counted off the records,
[D] arithmetic over such numbers, [I] a claim about what caused these records,
and [K] a claim about how this kind of system behaves. The first two are
checkable and the Verification section at the end gives each of them the one
command that reproduces it - the KQL the query pool actually ran, with the
wrapper named by basename so the file records no install layout. The last two
are arguments and are labelled as arguments. See PROVENANCE below.

Prints the output path. Exit codes: 0 ok, 1 input problem.
"""

import argparse
import datetime
import json
import os
import re
import sys
from collections import defaultdict


# ---------------------------------------------------------------------------
# Provenance.
#
# The same four tiers clp-session facts uses, so a reader who has seen one facts
# file already knows how to read the other. They do not overlap: a figure is read
# off the records, or computed from figures that were, or it is an argument about
# cause, or an argument about how this kind of system behaves.
#
# The markers are three characters at the front of the line, not a table column:
# a column would push the figures off a narrow screen, and a reader scanning for
# "is this measured" wants the answer where the eye already is.
# ---------------------------------------------------------------------------

MEASURED = "[M]"
DERIVED = "[D]"
INFERENCE = "[I]"
DOMAIN = "[K]"

TIERS = {
    MEASURED: "measured - counted off the records by one query. The query is in the Verification "
              "section, and it is the query that actually ran.",
    DERIVED: "derived - arithmetic over measured values: a share, a sum of counts, a duration between "
             "two timestamps. The line names its inputs; Verification gives the formula and why it "
             "answers the question.",
    INFERENCE: "inference - a claim about what caused these records or what they mean. Not "
               "reproducible: it is an argument from the figures, and a reader can reject it without "
               "disputing a number.",
    DOMAIN: "domain knowledge - a claim about how this kind of system behaves, not taken from these "
            "records at all.",
}


# The only way a query's own count can end up with no command: the pool that wrote
# the results file recorded neither the command nor the KQL, so there is nothing to
# rebuild it from. Saying which file is missing what is the reason; "unstated" is
# not, because it claims a figure cannot be checked while giving no reason at all.
NO_REASON = ("no reason was recorded for this figure. That is a defect in clp-insights facts, not a "
             "property of the figure: report it rather than trusting the figure or discarding it.")

NO_QUERY_RECORDED = ("the results file recorded neither a command nor the KQL for this entry, so the "
                     "query that produced the count cannot be rebuilt from it. Re-run the plan with "
                     "clp-insights run, which records both.")


def verification_tail(unverifiable):
    """The sentence that closes the Verification section."""
    if not unverifiable:
        return "Every one of them is."
    if unverifiable == 1:
        return "The other one says what it needs instead."
    return f"The other {unverifiable} say what they need instead."


def one_command(entry, archive=None):
    """The single command that reproduces one result entry, or None.

    The pool records the command it ran with an absolute path to the wrapper. The
    path is one machine's install layout, so only the basename is kept: a reader
    runs these with the plugin's bin/ on $PATH.

    An entry written by a pool that recorded no `command` still carries the KQL it
    ran and the archive it ran against, which is that command spelled out, so it
    is rebuilt rather than left with no check at all. Only an entry with no KQL ran
    nothing that could be re-run.
    """
    command = entry.get("command")
    if command:
        head, sep, rest = str(command).partition(" ")
        return os.path.basename(head) + sep + rest
    kql, where = entry.get("kql"), entry.get("archive") or archive
    if not kql or not where:
        return None
    select = f"--projection {entry['project']}" if entry.get("project") else "--count"
    return f"clp-s-search-kql {select} {where} '{kql}'"


def load_results(path):
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return sorted(results, key=lambda r: r.get("index", 0))


def entry_identity(entry):
    """What makes two result entries the same result, or None if it cannot tell.

    The rendered KQL plus the count: the KQL is the query that ran and the count
    is what it returned, so two entries agreeing on both are one result recorded
    twice, not two queries that happen to resemble each other. An entry with no
    KQL (one that failed before rendering) has no identity and is never treated
    as a duplicate -- guessing wrong here would drop a real count.
    """
    kql = entry.get("kql")
    if not kql:
        return None
    return (kql, entry.get("count"))


def overlapping_entries(results):
    """The identities that appear in both results files.

    A count summed once per entry is wrong as soon as the same entry arrives
    twice, which is what passing one file as both --baseline-results-file and
    --results-file does. This says whether that happened, so an excess over the
    record total can be explained by the inputs before it is blamed on the data.
    """
    seen = {}
    for r in results:
        ident = entry_identity(r)
        if ident is not None:
            seen.setdefault(ident, set()).add(r.get("table"))
    return {ident for ident, tables in seen.items() if len(tables) > 1}


def field_values(match):
    """(field, [values]) for an eq match or a not-of-eq(s) match, else None."""
    if not isinstance(match, dict):
        return None
    if "field" in match and "eq" in match:
        return match["field"], [match["eq"]], False
    inner = match.get("not")
    if isinstance(inner, dict):
        if "field" in inner and "eq" in inner:
            return inner["field"], [inner["eq"]], True
        eqs = inner.get("any")
        if isinstance(eqs, list) and eqs and all("eq" in e and "field" in e for e in eqs):
            fields = {e["field"] for e in eqs}
            if len(fields) == 1:
                return fields.pop(), [e["eq"] for e in eqs], True
    return None


def dig(record, path):
    if path in record:
        return record[path]
    cur = record
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def values_at(obj, keys):
    """Every value at a key path, stepping through arrays: in a record whose
    `message.content` is a list of blocks, `message.content.text` is each
    block's text. A key may also hold the dotted path whole."""
    if not keys:
        return [obj]
    if isinstance(obj, list):
        return [v for item in obj for v in values_at(item, keys)]
    if not isinstance(obj, dict):
        return []
    for i in range(len(keys), 0, -1):
        key = ".".join(keys[:i])
        if key in obj:
            return values_at(obj[key], keys[i:])
    return []


def strings_in(value):
    """The string values in a value, at any depth, in order (keys excluded)."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def leaf_items(value, prefix=""):
    """(dotted key, value) for each scalar in a record, arrays kept whole."""
    if isinstance(value, dict):
        return [item for k, v in value.items() for item in leaf_items(v, f"{prefix}.{k}" if prefix else k)]
    return [(prefix, value)] if prefix else []


def record_text(rec, msg, ts=None):
    """What a record says, for a sample or an example line. The message field
    may not be a flat string: its text can sit in arrays of blocks, or the
    record can hold it one level up (a string where other records hold a list
    of blocks). So: the strings at the message path; else at the nearest
    ancestor of it that the record has; and among those, the ones with
    whitespace in them (text, not ids or enum values) when there are any. A
    record with no message at all (a duration or a status record) is shown by
    its other fields, as key=value."""
    if not isinstance(rec, dict):
        return str(rec)
    keys = msg.split(".") if msg else []
    for i in range(len(keys), 0, -1):
        found = values_at(rec, keys[:i])
        if not found:
            continue
        texts = [s for v in found for s in strings_in(v)]
        if not texts:
            scalars = [str(v) for v in found if v is not None and not isinstance(v, (dict, list))]
            if scalars:
                return " | ".join(scalars)
            continue
        prose = [t for t in texts if any(c.isspace() for c in t.strip())]
        return " | ".join(prose or texts)
    others = [(k, v) for k, v in leaf_items(rec) if k != ts]
    if not others:
        return "(no fields)"
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
                     for k, v in others)


def sample_text(sample, msg, ts=None, chars=200):
    """One recorded sample line, as text on one line."""
    try:
        text = record_text(json.loads(sample), msg, ts)
    except json.JSONDecodeError:
        text = sample
    return text.replace("\n", " <NL> ")[:chars]


def fmt_ts(value):
    try:
        return datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def fmt_duration(seconds):
    if seconds < 120:
        return f"{seconds:.0f} seconds"
    if seconds < 2 * 3600:
        return f"{seconds / 60:.1f} minutes"
    if seconds < 72 * 3600:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def time_span(archive_dir):
    """(text, check) for the archive's time span, from the timeRange clp-s records.

    `check` is the one command that prints the two timestamps the span is computed
    from, or None when there is no span to check.
    """
    meta = path = None
    # The metadata sits in the top-level archive directory; accept the inner clp-s one too.
    for d in (archive_dir, os.path.dirname(os.path.normpath(archive_dir))):
        try:
            candidate = os.path.join(d, ".yscope-clp-archive.json")
            with open(candidate, "r", encoding="utf-8") as f:
                meta = json.load(f)
            path = candidate
            break
        except (OSError, json.JSONDecodeError):
            continue
    if meta is None:
        return ("unavailable. The archive has no .yscope-clp-archive.json, so neither clp-s-compress-folder "
                "nor clp-s-compress-session compressed it; recompress it with one of them and a timestamp "
                "key to record the span."), None
    key = meta.get("timestampKey")
    if not key:
        return ("unavailable. The archive was compressed without --timestamp-key; recompress it with one "
                "to record the span."), None
    if "timeRange" not in meta:
        return ("unavailable. The archive was compressed by a version of the plugin's compression wrappers "
                "that did not record time ranges; recompress it to record the span."), None
    if meta["timeRange"] is None:
        return f"unavailable. No record has the timestamp key `{key}`.", None
    begin, end = meta["timeRange"]["beginMs"] / 1000, meta["timeRange"]["endMs"] / 1000
    check = (f"python3 -c \"import datetime,json; r=json.load(open('{path}'))['timeRange']; "
             "print(*[datetime.datetime.fromtimestamp(r[k]/1000, datetime.timezone.utc) "
             "for k in ('beginMs','endMs')])\"")
    return (f"{fmt_ts(begin)} to {fmt_ts(end)} ({fmt_duration(end - begin)}), the earliest and latest "
            f"`{key}` across every record, recorded by clp-s at compression."), check


MASK = re.compile(r"\d+(?:\.\d+)?")
QUOTED = re.compile(r'"[^"\s]{1,40}"')


def shape(message, limit=140):
    text = QUOTED.sub('"S"', MASK.sub("N", " ".join(str(message).split())))
    return text[:limit]


def pct(n, total):
    if not total:
        return ""
    p = 100.0 * n / total
    return "<0.01%" if 0 < p < 0.01 else f"{p:.2f}%"


def read_top_templates(path, n, chars=220):
    """(count, display text, raw log_shape) for the first n templates in the file.

    The raw shape is kept beside the display text so a check can grep the stored
    record for it: the display text has its newlines replaced and is truncated, so
    it would never match the file it came from.
    """
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if len(out) >= n:
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = rec.get("log_shape", "")
            # A stored template's "log_shape" is its prefix; "length" is the whole.
            cut = isinstance(rec.get("length"), int) and rec["length"] > len(raw)
            text = raw.replace("\n", " <NL> ")
            out.append((rec.get("count", 0), text[:chars] + ("…" if cut or len(text) > chars else ""),
                        raw))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute a log-insights report's numbers in code.")
    ap.add_argument("--results-file", default="/tmp/clp-insights-query-results.ndjson",
                    help="the classified plan's results")
    ap.add_argument("--baseline-results-file", default="/tmp/clp-insights-baseline-results.ndjson",
                    help="the baseline queries' results, run in their own pool")
    ap.add_argument("--category-totals", default="/tmp/log-shape-category-totals.json")
    ap.add_argument("--focus-file", default="/tmp/clp-insights-focus.json")
    ap.add_argument("--freqs-file", default="/tmp/log-shape-freqs.ndjson")
    ap.add_argument("--top-templates-file", default="/tmp/log-shape-top-templates.json")
    ap.add_argument("--schema-json", required=True)
    ap.add_argument("--archive-dir", required=True,
                    help="the top-level archive directory; its metadata gives the time span")
    ap.add_argument("--schema-tree-file", default=None,
                    help="clp-s-schema-tree --json-out file: the facts then name the text fields, "
                         "and every path in it counts as a field")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="/tmp/clp-insights-facts.md")
    args = ap.parse_args(argv)

    # One file passed as both pools' results doubles every count summed per entry,
    # and is never what the caller meant. Refuse before writing anything: a facts
    # file that exists is quoted downstream as trustworthy, so it is better to
    # produce none than one whose severity split adds up to twice the archive.
    if os.path.realpath(args.baseline_results_file) == os.path.realpath(args.results_file):
        print(f"error: --baseline-results-file and --results-file are the same file "
              f"({args.results_file}); every count in both would be counted twice. "
              f"Pass the baseline pool's results and the plan pool's results.",
              file=sys.stderr)
        return 2

    try:
        schema = json.loads(args.schema_json)
    except json.JSONDecodeError as exc:
        print(f"error: --schema-json: {exc}", file=sys.stderr)
        return 1
    # Two tables, numbered separately: the baseline pool's and the plan's.
    results = []
    for table, path in (("baseline", args.baseline_results_file), ("plan", args.results_file)):
        try:
            for r in load_results(path):
                r["table"] = table
                results.append(r)
        except FileNotFoundError:
            print(f"warning: no {table} results file: {path}", file=sys.stderr)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            return 1
    if not results:
        print(f"error: no results in {args.baseline_results_file} or {args.results_file}", file=sys.stderr)
        return 1

    sev, log, ts, msg = (schema.get(k) for k in ("severity", "logger", "timestamp", "message"))
    total = next((r["total_records"] for r in results if r.get("total_records")), None)
    # Two distinct files can still share entries -- the same query recorded in
    # both pools. Every sum below counts an entry once, so the numbers are right
    # either way; this is reported because a caller who did not mean to overlap
    # them has a broken pipeline that nothing else would show them.
    overlap = overlapping_entries(results)
    overlap_note = (
        f"the two results files overlap: {len(overlap):,} "
        f"{'entry' if len(overlap) == 1 else 'entries'} appear in both "
        f"`{args.baseline_results_file}` and `{args.results_file}`, and each is counted "
        f"once here. Pass distinct --baseline-results-file and --results-file."
    ) if overlap else None
    out = []
    w = out.append

    # -- totals
    totals = None
    if args.category_totals != "none":
        try:
            with open(args.category_totals, "r", encoding="utf-8") as f:
                totals = json.load(f)
        except (OSError, json.JSONDecodeError):
            totals = None
    # Every headline figure's provenance, rendered as the Verification section at
    # the end. The long form belongs there: a line that triples in length to carry
    # its own command is worse for a reader than one that is merely unlabelled.
    checks = []

    def add_check(name, tier, value, command=None, derivation="", trap="", note=""):
        checks.append({"name": name, "tier": tier, "value": value, "command": command,
                       "derivation": derivation, "trap": trap, "note": note})

    w("# Facts (computed in code; every figure below is exact)\n")
    if overlap_note:
        w(f"> **Input problem:** {overlap_note}\n")
    w("**What the markers mean.** Every figure carries one, and the four kinds do not overlap:\n")
    for marker in (MEASURED, DERIVED, INFERENCE, DOMAIN):
        w(f"- `{marker}` {TIERS[marker]}")
    w("")
    w("A percentage in parentheses is always derived, from that line's count over the total records "
      "above, so it takes no marker of its own. In a table the markers are in the column headings. A "
      "`[baseline #N]` or `[plan #N]` names the query that produced the line; the Verification section "
      "at the end lists those commands, with the plugin's wrappers named by basename - run them with "
      "the plugin's `bin/` on $PATH.\n")
    w("A line with no marker is not a figure: it is a heading, a caption, a note on where something "
      "came from, or the user's own words quoted back. The Focus section below is the clearest case - "
      "what the user said is input to the analysis, not a measurement from it, and the four tiers "
      "above describe only what this file claims about the records.\n")
    w("## Totals")
    w(f"- {MEASURED} Total records: {total:,}" if total else "- Total records: unavailable")
    if total:
        add_check("Total records", MEASURED, f"{total:,}",
                  f"clp-s-search-kql --count {args.archive_dir} '*'",
                  derivation="a count of every record in the archive, which is also the denominator of "
                             "every share in this file.")
    if totals:
        add_check("Distinct templates", DERIVED, f"{sum(c['templates'] for c in totals.values()):,}",
                  None,
                  derivation="the `templates` counts of the category table, summed.",
                  note="a template count comes from the log-shape store, not from a query over the "
                       f"archive, so it is checked against `{args.category_totals}` rather than by "
                       "re-counting: no KQL filter can name a template.")
        w(f"- {DERIVED} Distinct templates: {sum(c['templates'] for c in totals.values()):,} "
          "(sum of the category table below)")
    span_text, span_check = time_span(args.archive_dir)
    w(f"- {DERIVED if span_check else MEASURED} Time span: {span_text}")
    if span_check:
        add_check("Time span", DERIVED, span_text.split(",")[0], span_check,
                  derivation="the last timestamp minus the first, both recorded by clp-s at "
                             "compression. It answers \"what period do these logs cover\" because "
                             "clp-s took them from every record, not from a sample.",
                  trap="it is the span the records cover, not the span anything was running: a gap in "
                       "the middle is invisible here.")
    names = ", ".join(f"{role} = `{schema[role]}`" for role in ("timestamp", "severity", "logger", "message") if schema.get(role))
    payload = ", ".join(f"`{p}`" for p in schema.get("payload", []) or [])
    tree_note = ""
    if args.schema_tree_file:
        try:
            with open(args.schema_tree_file, "r", encoding="utf-8") as f:
                tree_rows = json.load(f)
            text = [r["path"] for r in tree_rows if r.get("type") == "ClpString"]
            tree_note = (f"; text fields: {', '.join(f'`{p}`' for p in dict.fromkeys(text))}; "
                         f"and any other path listed in `{args.schema_tree_file}`")
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"error: cannot read --schema-tree-file {args.schema_tree_file}: {e}", file=sys.stderr)
            return 2
    w(f"- {MEASURED} Fields a KQL query may filter on: {names}"
      + (f"; payload: {payload}" if payload else "")
      + tree_note
      + f". {DOMAIN} Nothing else is a field: the classification's categories exist only in the "
      "analysis, so a query cannot filter on a category name.\n")
    add_check("Fields a KQL query may filter on", MEASURED, names,
              f"clp-s-schema-tree {args.archive_dir}",
              derivation="every path the archive's schema tree lists is a field; the roles above are "
                         "this run's classification of them.")

    # -- the user's focus and context
    focus = None
    try:
        with open(args.focus_file, "r", encoding="utf-8") as f:
            focus = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    if focus:
        w("## Focus (the user's choice; lead the report with it)")
        kind = focus.get("focus")
        if kind == "categories":
            w(f"- Focus: the categories {', '.join(f'`{c}`' for c in focus.get('categories', []))}")
        elif kind == "question":
            w("- Focus: the user's own question (below)")
        else:
            w("- Focus: everything (no single area)")
        if focus.get("question"):
            w(f"- The user's question, verbatim: \"{focus['question']}\"")
        if focus.get("context"):
            w(f"- What the user said they already know, verbatim: \"{focus['context']}\". This is the "
              "user's account, not a finding: say whether the records below support it, contradict it, "
              "or say nothing about it.")
        else:
            w("- The user gave no background about these logs.")
        for cat in focus.get("categories", []):
            c = (totals or {}).get(cat)
            if c:
                w(f"- {MEASURED} `{cat}`: {c['templates']:,} templates, {c['records']:,} records "
                  f"({pct(c['records'], total)}); classifier priority {c.get('priority') or '-'}"
                  + (f" - {INFERENCE} {c['why']}" if c.get("why") else ""))
        mine = [r for r in results if r.get("origin") == "focus"]
        if mine:
            w("- Focus queries (their own counts):")
            for r in mine:
                w(f"  - {MEASURED} [plan #{r['index']}] {r.get('label')}: status {r.get('status')}, "
                  f"{r.get('count') or 0:,} ({pct(r.get('count') or 0, total)}); kql `{r.get('kql')}`")
                for smp in (r.get("samples") or [])[:3]:
                    w(f"    - sample: `{sample_text(smp, msg, ts)}`")
                add_check(f"[plan #{r['index']}] {r.get('label')}", MEASURED,
                          f"{r.get('count') or 0:,}", one_command(r, args.archive_dir),
                          note=NO_QUERY_RECORDED)
        elif focus.get("entries"):
            w("- Focus queries: queued but no results recorded.")
        w("")

    # -- baseline: severity and logger
    baseline = [r for r in results if r.get("origin") == "baseline" and r.get("status") in ("ok", "zero")]
    by_field = defaultdict(lambda: {"values": [], "residual": None})
    # Each value's count is appended, so the same entry arriving from both results
    # files would be added twice and the split would sum past the record total.
    # Count each result once; the residual is assigned, so it was never doubled.
    counted_once = set()
    for r in baseline:
        fv = field_values(r.get("match"))
        if not fv or r.get("method") != "count":
            continue
        ident = entry_identity(r)
        if ident is not None:
            if ident in counted_once:
                continue
            counted_once.add(ident)
        field, values, negated = fv
        if negated:
            by_field[field]["residual"] = (values, r.get("count", 0), r)
        else:
            by_field[field]["values"].append((values[0], r.get("count", 0), r))

    fetched = defaultdict(list)  # follow-up records by the entry they followed
    fetched_cmd = {}             # and the one command that fetched them again
    fetched_once = set()
    for r in results:
        origin = r.get("origin", "")
        if origin.startswith("follow-up of ") and r.get("samples"):
            # Same exposure as the severity split: a follow-up recorded in both
            # results files would contribute its records twice, inflating both the
            # fetched total and every per-shape group below.
            ident = entry_identity(r)
            if ident is not None:
                if ident in fetched_once:
                    continue
                fetched_once.add(ident)
            fetched_cmd.setdefault(origin, one_command(r, args.archive_dir))
            for s in r["samples"]:
                try:
                    fetched[origin].append(json.loads(s))
                except json.JSONDecodeError:
                    pass

    def cite(entry):
        return f"[{entry.get('table')} #{entry.get('index')}]"

    def breakdown(title, field, fetched_records=None, fetched_command=None):
        info = by_field.get(field)
        if not info:
            return
        w(f"## {title} (field `{field}`; exact counts from `count` queries)")
        for v, c, entry in sorted(info["values"], key=lambda x: -x[1]):
            w(f"- {MEASURED} {v}: {c:,} ({pct(c, total)}) {cite(entry)}")
            add_check(f"{title}: {field} = {v}", MEASURED, f"{c:,}",
                      one_command(entry, args.archive_dir), note=NO_QUERY_RECORDED)
        if info["residual"]:
            values, c, entry = info["residual"]
            w(f"- {MEASURED} everything other than {', '.join(map(str, values))}: {c:,} "
              f"({pct(c, total)}) {cite(entry)}")
            add_check(f"{title}: {field} other than " + ", ".join(map(str, values)), MEASURED,
                      f"{c:,}", one_command(entry, args.archive_dir),
                      note=NO_QUERY_RECORDED,
                      derivation="one negated count query, not the total minus the values above: a "
                                 "record with no value for this field is in neither, and that is how "
                                 "the sum check below can find it.")
            if fetched_records:
                counts = defaultdict(int)
                for rec in fetched_records:
                    counts[dig(rec, field)] += 1
                note = "" if len(fetched_records) == c else f" (fetched {len(fetched_records):,} of {c:,})"
                w(f"  - {MEASURED} of which, by value" + note + ": "
                  + ", ".join(f"{v}={n:,}" for v, n in sorted(counts.items(), key=lambda x: -x[1])))
                for v, n in sorted(counts.items(), key=lambda x: -x[1]):
                    add_check(f"{title}: {v} among the fetched records", MEASURED, f"{n:,}",
                              (f"{fetched_command} | grep -c '\"{field}\":\"{v}\"'"
                               if fetched_command else None),
                              derivation="the same follow-up query that fetched the records, with its "
                                         "rows filtered to that one value.",
                              note="" if fetched_command else
                              "the pool recorded no command for the follow-up that fetched these records")
        counted = sum(c for _, c, _ in info["values"]) + (info["residual"][1] if info["residual"] else 0)
        if total:
            if counted == total:
                w(f"- {DERIVED} check: the lines above sum to all {total:,} records")
            elif counted < total:
                w(f"- {DERIVED} check: the lines above sum to {counted:,}; the other {total - counted:,} "
                  f"records ({pct(total - counted, total)}) have no `{field}` value")
                add_check(f"{title}: records with no `{field}` value", DERIVED, f"{total - counted:,}",
                          None,
                          derivation=f"the total records minus the counts above ({total:,} - {counted:,}). "
                                     "It answers \"is the split complete\" because the values and the "
                                     "negated residual together cover every record that has the field "
                                     "at all, so whatever is left has no value for it.",
                          note="it is the total-records count minus the counts above, and each of those "
                               "is its own one-command check in this section.")
            else:
                # Every entry above was counted once, so an excess that is still
                # here is the data's and not the inputs'. Say so when the inputs
                # also overlapped, or the reader cannot tell which cause applies.
                w(f"- {DERIVED} check: the lines above sum to {counted:,}, more than the {total:,} "
                  f"records, so {INFERENCE} `{field}` is multi-valued in some records"
                  + (" -- each entry above was counted once, so the overlapping inputs noted "
                     "at the top do not explain this excess" if overlap_note else ""))
        w("")

    fetched_all = [rec for recs in fetched.values() for rec in recs]
    # One follow-up in the common case; with several, no single command reproduces
    # the combined per-value counts, and saying so is better than naming one of them.
    fetched_one_command = (list(fetched_cmd.values())[0] if len(fetched_cmd) == 1 else None)
    if sev:
        breakdown("Severity", sev, fetched_all, fetched_one_command)
    if log:
        breakdown("Logger / component", log)

    # -- categories
    if totals:
        rec_sum = sum(c["records"] for c in totals.values())
        # A record with several text fields (a session record's prompt, reply and
        # tool output) carries one templated value per field, so the counts are
        # values, not records, once they exceed the record count.
        per_value = bool(total) and rec_sum > total
        base = rec_sum if per_value else total
        unit = "Values" if per_value else "Records"
        w("## Categories (exact: the stored per-template counts, summed; no keyword involved)")
        w(f"| Category | Priority | Templates {MEASURED} | {unit} {MEASURED} "
          f"| Share of {unit.lower()} {DERIVED} |")
        w("|---|---|---|---|---|")
        for cat, c in sorted(totals.items(), key=lambda kv: -kv[1]["records"]):
            w(f"| {cat} | {c.get('priority') or '-'} | {c['templates']:,} | {c['records']:,} "
              f"| {pct(c['records'], base)} |")
        w(f"| **sum** | | {sum(c['templates'] for c in totals.values()):,} | {rec_sum:,} | {pct(rec_sum, base)} |")
        add_check(f"{unit} the categories account for", DERIVED, f"{rec_sum:,}", None,
                  derivation="the per-category counts above, summed; each category's own count is the "
                             "stored per-template counts of the templates in it, summed.",
                  note="a per-template count comes from the log-shape store, not from a query over the "
                       f"archive: check it against `{args.category_totals}`, because a template is not "
                       "something a KQL filter can name.")
        whys = [(cat, c) for cat, c in totals.items() if c.get("why") and c.get("priority") == "high"]
        if whys:
            w("\nWhy the classifier ranked these categories high (its judgement, not a finding):")
            for cat, c in whys:
                w(f"- {INFERENCE} `{cat}`: {c['why']}")
        if per_value:
            w(f"\n{DERIVED} Templated values: {rec_sum:,} in {total:,} records. {DOMAIN} A record "
              "carries one value per text field, so the values outnumber the records and every share "
              "above is a share of values.")
        elif total:
            w(f"\n{DERIVED} Records no template accounts for: {total - rec_sum:,} "
              f"({pct(total - rec_sum, total)}), the total records minus the sum above.")
        blobs = [(cat, c) for cat, c in totals.items() if c["templates"] > 500 and c["templates"] > 0.9 * c["records"]]
        for cat, c in blobs:
            w(f"- {DERIVED} `{cat}` has {c['templates']:,} templates for {c['records']:,} records: about "
              f"one template per record. {INFERENCE} That is large near-duplicate messages that never "
              "collapse into one recurring template, not that many different behaviours.")
        w("")

    # -- top templates
    top_json = None
    try:
        with open(args.top_templates_file, "r", encoding="utf-8") as f:
            top_json = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    # A stored per-template count is measured, but not against the archive: the
    # log-shape store counted it, and no KQL filter can name a template. So the
    # check reads the store's own record of it rather than pretending to re-count.
    def template_check(shape, store):
        head = str(shape).split("\n")[0][:60]
        if not head.strip():
            return None
        return f"grep -m1 -F -- '{head}' {store}"

    template_note = ("a per-template count is the log-shape store's own count. A template is not "
                     "something a KQL filter can name, so it cannot be re-counted against the archive "
                     "in one query; the command reads the stored record instead.")
    if top_json:
        w(f"## Top {min(args.top, len(top_json['overall']))} templates by records, with their category "
          "(stored per-template counts)")
        for t in top_json["overall"][: args.top]:
            w(f"- {MEASURED} {t['count']:,} ({pct(t['count'], total)}) [{t['category']}]: "
              f"`{t['log_shape']}`")
        first = top_json["overall"][0] if top_json["overall"] else None
        if first:
            add_check("The single most frequent template", MEASURED, f"{first['count']:,}",
                      template_check(first.get("log_shape"), args.top_templates_file),
                      derivation=f"category `{first.get('category')}`.", note=template_note)
        w("")
        w("## Top templates within each category (exact counts; use these for any per-template figure)")
        cats = sorted(top_json["by_category"].items(),
                      key=lambda kv: -(totals.get(kv[0], {}).get("records", 0) if totals else 0))
        for cat, items in cats:
            w(f"### {cat}")
            for t in items:
                w(f"- {MEASURED} {t['count']:,}: `{t['log_shape']}`")
        w("")
    elif args.freqs_file != "none":
        try:
            top = read_top_templates(args.freqs_file, args.top)
            w(f"## Top {len(top)} templates by records (stored per-template counts)")
            for c, t, _ in top:
                w(f"- {MEASURED} {c:,} ({pct(c, total)}): `{t}`")
            if top:
                add_check("The single most frequent template", MEASURED, f"{top[0][0]:,}",
                          template_check(top[0][2], args.freqs_file), note=template_note)
            w("")
        except OSError:
            pass

    # -- fetched records, grouped
    if fetched_all:
        groups = defaultdict(lambda: {"n": 0, "first": None, "last": None, "example": None})
        for rec in fetched_all:
            text = record_text(rec, msg, ts)
            key = (dig(rec, sev) if sev else None, shape(text))
            g = groups[key]
            g["n"] += 1
            t = dig(rec, ts) if ts else None
            try:
                tv = float(t)
                g["first"] = tv if g["first"] is None else min(g["first"], tv)
                g["last"] = tv if g["last"] is None else max(g["last"], tv)
            except (TypeError, ValueError):
                pass
            g["example"] = g["example"] or text.replace("\n", " <NL> ")[:260]
        w(f"## The fetched non-dominant-severity records, grouped by message shape (numbers masked as N, quoted names as \"S\")")
        w(f"{MEASURED} {len(fetched_all):,} records fetched; {DERIVED} {len(groups):,} distinct shapes "
          "after masking each message's numbers and quoted names. First/last are the timestamps of "
          "these records only, not the archive's span.")
        for (severity, shp), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"])[: args.top]:
            span = f", {fmt_ts(g['first'])} to {fmt_ts(g['last'])}" if g["first"] is not None else ""
            w(f"- {DERIVED} **{g['n']:,} x [{severity}]**{span}\n  - shape: `{shp}`\n"
              f"  - example: `{g['example']}`")
        w("")
        add_check("Distinct message shapes among the fetched records", DERIVED, f"{len(groups):,}",
                  None,
                  derivation="the fetched records grouped by severity and by the message with its "
                             "numbers masked to N and its short quoted strings to \"S\". It answers "
                             "\"how many different things are happening here\" because two records that "
                             "differ only in an id or a count are one event, not two.",
                  trap="the masking is a regex, not the log's own template: a message whose only "
                       "variable part is an unquoted word keeps that word, so it splits into as many "
                       "shapes as it has words there.",
                  note=("the masking and the grouping happen in this script, not in the query, so no "
                        "command prints the shape count. `" + fetched_one_command + "` reproduces the "
                        f"{len(fetched_all):,} records it grouped; the shapes themselves are listed "
                        "above.") if fetched_one_command else
                       ("more than one follow-up query contributed these records, so no single command "
                        "fetches the same set; each follow-up's own command is in the pool's results "
                        "file, and the grouping happens in this script rather than in a query."))
        add_check("Records fetched behind the non-dominant severities", MEASURED,
                  f"{len(fetched_all):,}", fetched_one_command,
                  note="" if fetched_one_command else
                       "more than one follow-up query contributed these records, so no single command "
                       "fetches the same set")

    # -- semantic and flags
    sem = [r for r in results if r.get("method") == "semantic"]
    if sem:
        w("## Semantic entries")
        w(f"{DOMAIN} A semantic query ranks records by meaning rather than matching a field, so its row "
          "count is what the embedding model judged relevant and not a count of anything the log says.")
        for r in sem:
            w(f"- {MEASURED} [{r['table']} #{r['index']}] {r.get('label')}: status {r.get('status')}, "
              f"{r.get('count') or 0:,} rows; kql `{r.get('kql')}`")
            for s in (r.get("samples") or [])[:3]:
                w(f"  - `{sample_text(s, msg, ts)}`")
            add_check(f"[{r['table']} #{r['index']}] {r.get('label')}", MEASURED,
                      f"{r.get('count') or 0:,} rows", one_command(r, args.archive_dir),
                      trap="a semantic query's row count depends on the embedding model and its "
                           "threshold, so re-running it is reproducible only against the same model.",
                      note=NO_QUERY_RECORDED)
        w("")
    flagged = [r for r in results if r.get("non_selective") or r.get("status") in ("error", "timeout", "zero") or r.get("retried")]
    if flagged:
        w("## Query flags")
        for r in flagged:
            why = [x for x in ("non_selective" if r.get("non_selective") else "", r.get("status") if r.get("status") != "ok" else "",
                               "retried" if r.get("retried") else "") if x]
            w(f"- {MEASURED} [{r['table']} #{r['index']}] {r.get('label')}: {', '.join(why)} "
              f"(count {r.get('count') or 0:,})")
        w("")

    # -- Verification: the long form of every headline figure's provenance
    if checks:
        w("## Verification - the one command behind each figure")
        w("Copy a command, run it, and compare. These are the commands the query pool ran, with the "
          "plugin's wrappers named by basename, so run them with the plugin's `bin/` on $PATH. A figure "
          "no single command reproduces says so and says what to run instead; a command that only looks "
          "like a check is worse than none, because a reader who runs it and gets a different number "
          "concludes the figure is wrong.\n")
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
        w(f"{len(checks) - unverifiable} of {len(checks)} figures above are reproduced by a single "
          "command. " + verification_tail(unverifiable))
        w("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"FACTS_FILE={args.out}")
    return 0

