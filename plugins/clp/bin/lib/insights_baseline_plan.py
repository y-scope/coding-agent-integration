"""
clp-insights baseline-plan - write the app-agnostic baseline queries of a
log-insights run to their own plan file (log-insights skill, step 4).

The insight subagent used to discover the severity and logger breakdowns by
running its own searches, one at a time, out of sight. Those queries depend on
nothing but the schema, which the bootstrap already reveals, so they run in the
query pool as soon as the schema is chosen -- in the background, while the
templates are clustered and classified -- under the same memory control as the
classified plan, which runs later from its own plan file.

Per low-cardinality field (the schema's severity and logger):
  * the field's values are sampled from the head of the archive, cheaply;
  * one `count` entry per common value;
  * one `count` entry for the RESIDUAL -- everything that is not one of those
    values -- which is where rare severities and unexpected loggers hide. A
    sample cannot see values that only appear later in the log, so the residual
    is what keeps the breakdown complete.
For the severity field, a residual of a few hundred records or fewer carries a
`then` rule: the pool fetches those records too (the errors and warnings
themselves), so the report is grounded in what was logged, not in a guess.
One semantic entry, scoped by that residual, catches problems the templates do
not name. Nothing here knows the application: every entry is built from the
schema and the sampled values.

`--unique FIELD` is deliberately not used: it scans every record (80 s on a
16.5M-record archive, against about 7 s for a count).

Usage:
  clp-insights baseline-plan --archive DIR --schema-json JSON [options]

Options:
  --archive DIR         Archives directory to sample.
  --schema-json JSON    The schema chosen from the bootstrap, e.g.
                        '{"timestamp":"t.$date","severity":"s","logger":"c",
                        "message":"msg"}'. Uses its `severity`, `logger`,
                        `timestamp` and `message` fields.
  --plan-file F         The baseline's own plan file (default:
                        /tmp/clp-insights-baseline-plan.txt), run by
                        clp-insights run with its own results file.
                        Entries from an earlier run (origin "baseline") are
                        replaced, so it is safe to run twice.
  --sample N            Records to sample from the head of the archive
                        (default: 20000).
  --max-values N        Common values counted per field (default: 5).
  --max-cardinality N   Skip a field with more distinct sampled values than
                        this (default: 40).
  --max-residual N      Fetch the severity residual's records when there are N
                        or fewer (default: 300).
  --no-semantic         Do not add the semantic entry.
  --search-wrapper P    Search wrapper (default: clp-s-search-kql next to this
                        script).

Prints BASELINE_ENTRIES= and one line per field with the sampled vocabulary.
Exit codes: 0 ok, 1 input problem.
"""

import argparse
import json
import os
import subprocess
import sys
from collections import Counter

LIB_DIR = os.path.dirname(os.path.realpath(__file__))
# The sibling tools this module runs live one level up, in bin/.
BIN_DIR = os.path.dirname(LIB_DIR)
sys.path.insert(0, LIB_DIR)
from kql_build import FilterError, check_entry  # noqa: E402

ORIGIN = "baseline"
SEMANTIC_QUESTION = "errors, failures, warnings or unexpected conditions"


def dig(record, path):
    """Value at a dotted path, tolerating both nested and flat records."""
    if path in record:
        return record[path]
    cur = record
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def sample_values(wrapper, archive, fields, limit):
    proc = subprocess.run(
        [wrapper, "--projection", ",".join(fields), "--limit", str(limit), archive, "*"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1])
    counters = {f: Counter() for f in fields}
    sampled = 0
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        sampled += 1
        for f in fields:
            v = dig(rec, f)
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                counters[f][v] += 1
    return counters, sampled


def not_any(field, values):
    eqs = [{"field": field, "eq": v} for v in values]
    return {"not": eqs[0] if len(eqs) == 1 else {"any": eqs}}


def field_entries(role, field, counter, args, schema):
    values = [v for v, _ in counter.most_common(args.max_values)]
    entries = [
        {"label": f"Baseline: {role} {field}={v} records",
         "match": {"field": field, "eq": v}, "method": "count", "origin": ORIGIN}
        for v in values
    ]
    residual = not_any(field, values)
    entry = {"label": f"Baseline: {role} {field} other than {', '.join(map(str, values))}",
             "match": residual, "method": "count", "origin": ORIGIN}
    if role == "severity":
        cols = [c for c in (schema.get("timestamp"), field, schema.get("message")) if c]
        entry["then"] = [{
            "when": {"count_gt": 0, "count_lte": args.max_residual},
            "run": {"label": f"Baseline: the records with {field} other than {', '.join(map(str, values))}",
                    "match": residual, "project": ",".join(cols), "method": "project+grep",
                    "samples": args.max_residual, "sample_chars": 500, "origin": ORIGIN},
        }]
    entries.append(entry)
    return entries, residual


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add baseline queries to a log-insights query plan.")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--schema-json", required=True)
    parser.add_argument("--plan-file", default="/tmp/clp-insights-baseline-plan.txt")
    parser.add_argument("--sample", type=int, default=20000)
    parser.add_argument("--max-values", type=int, default=5)
    parser.add_argument("--max-cardinality", type=int, default=40)
    parser.add_argument("--max-residual", type=int, default=300)
    parser.add_argument("--no-semantic", action="store_true")
    parser.add_argument(
        "--search-wrapper",
        default=os.path.join(BIN_DIR, "clp-s-search-kql"),
    )
    args = parser.parse_args(argv)

    try:
        schema = json.loads(args.schema_json)
        plan = []
        if os.path.exists(args.plan_file):
            with open(args.plan_file, "r", encoding="utf-8") as f:
                plan = [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    plan = [e for e in plan if e.get("origin") != ORIGIN]

    roles = [(role, schema[role]) for role in ("severity", "logger") if schema.get(role)]
    if not roles:
        print("BASELINE_ENTRIES=0 (the schema has no severity or logger field)")
        return 0
    try:
        counters, sampled = sample_values(
            args.search_wrapper, os.path.abspath(args.archive), [f for _, f in roles], args.sample)
    except (OSError, RuntimeError) as exc:
        print(f"error: sampling failed: {exc}", file=sys.stderr)
        return 1

    added, severity_residual = [], None
    for role, field in roles:
        counter = counters[field]
        vocab = ", ".join(f"{v}({c})" for v, c in counter.most_common(8))
        if not counter or len(counter) > args.max_cardinality:
            print(f"FIELD {role} {field}: skipped ({len(counter)} distinct in {sampled} sampled)")
            continue
        entries, residual = field_entries(role, field, counter, args, schema)
        added += entries
        if role == "severity":
            severity_residual = residual
        print(f"FIELD {role} {field}: {len(counter)} distinct in {sampled} sampled: {vocab}")

    if severity_residual and not args.no_semantic:
        cols = [c for c in (schema.get("timestamp"), schema.get("severity"), schema.get("message")) if c]
        added.append({
            "label": "Baseline: semantic scan of the non-dominant severities",
            "match": {"all": [severity_residual, {"semantic": SEMANTIC_QUESTION}]},
            "project": ",".join(cols), "method": "semantic", "samples": 10, "origin": ORIGIN,
        })

    for i, entry in enumerate(added, 1):
        try:
            check_entry(entry)
            for rule in entry.get("then", []):
                check_entry(rule["run"])
        except FilterError as exc:
            print(f"error: generated entry {i} is invalid: {exc}", file=sys.stderr)
            return 1

    with open(args.plan_file, "w", encoding="utf-8") as f:
        for entry in plan + added:
            f.write(json.dumps(entry) + "\n")
    print(f"BASELINE_ENTRIES={len(added)}")
    print(f"PLAN_ENTRIES={len(plan) + len(added)}")
    return 0

