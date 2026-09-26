"""
clp-insights extract - build the report writer's prompt pieces from a
classification (log-insights skill, step 7).

The classification (`log-shape-cache get`, `log-shape-cache merge`, or
`log-shape-cluster expand` output, e.g. /tmp/log-shape-classification.json) names
each template by hash, not by text (see lib/log_shapes.py): `hash` of the full
template and `prefix_hash` of its first max_chars characters, with its
category. The text comes from the bootstrap's frequencies file, read from the
stored archive (`log-shape-cache freqs --archive-ids`): each line carries the
template's hash, its full length and its first max_chars characters, so the
join needs no hashing. When the archive has no stored counts, the text comes
from the bootstrap's log shapes file instead, and each template is hashed.
Either file is streamed one line at a time. A template whose full hash is not in the
classification takes the category of one that shares its prefix hash (it
differs only past the limit); one with neither is "unclassified".

Templates are unbounded in both count and length. Observed in practice:
CockroachDB serializes a multi-line Pebble storage-engine stats table as a
single log message, with column widths shifting the exact bytes between dumps
so clp-s never collapses them into one log shape -- 11558 templates, one category
holding 9810 of them, mean length ~184KB (max ~1.6MB). So nothing here holds
every template at once: per category it keeps only the top
`--max-per-category` templates by frequency (ranked using the frequencies file
the bootstrap already produced, never recomputed), each truncated to
`--trunc-chars` characters with embedded newlines rendered as " <NL> " so the
result stays one line per template.

Usage:
  clp-insights extract [options]

Options:
  --classification-file F   Classification JSON
                             (default: /tmp/log-shape-classification.json)
  --freqs-file F            Per-log-shape frequency NDJSON from the bootstrap
                             (default: /tmp/log-shape-freqs.ndjson; pass
                             --no-freqs when the bootstrap reported
                             FREQS=UNAVAILABLE)
  --no-freqs                Skip frequency ranking; read the templates from
                             --log-shapes-file instead and keep the first
                             `--max-per-category` per category.
  --log-shapes-file F         The bootstrap's log shapes NDJSON, used with
                             --no-freqs (default: /tmp/log-shapes.ndjson)
  --max-per-category N      Cap templates shown per category (default: 25)
  --trunc-chars N           Truncate each template to N characters for the
                             OUTPUT only (default: 180)
  --out-templates F         Where to write the templates-by-category text
                             (default: /tmp/log-shape-templates-by-category.txt)
  --out-query-plan F        Where to write the core plan: the "core" entries,
                             one per line as compact JSON, high priority first
                             (default: /tmp/clp-insights-query-plan.txt)
  --out-drill-plan F        Where to write the "drill" entries the same way,
                             grouped by category; they run only when the user
                             focuses on their category (clp-insights focus)
                             (default: /tmp/clp-insights-drill-plan.txt)
  --focus-inbox F           The inbox the plan runner reads focus entries from
                             (clp-insights run --inbox); emptied here,
                             with the previous run's focus file
                             (--focus-file), so a new analysis starts with no
                             focus (defaults: /tmp/clp-insights-focus-inbox.ndjson,
                             /tmp/clp-insights-focus.json)
  --out-top-templates F     With frequencies: the most frequent templates
                             overall (--top-templates, default 30) and per
                             category (--top-per-category, default 5), each
                             with its category and exact count (default:
                             /tmp/log-shape-top-templates.json)
  --out-category-totals F   With frequencies: where to write {category:
                             {templates, records, priority, why}} JSON,
                             records being the stored per-template counts
                             summed -- an exact record count per category
                             with no search -- and priority and why the
                             classifier's ranking of the category
                             (default: /tmp/log-shape-category-totals.json)

Prints a KEY=VALUE summary to stdout: SCHEMA=, TEMPLATES=, CATEGORIES=,
QUERY_PLAN= (core entries), DRILL_PLAN= (drill entries), QUERY_PLAN_INVALID=,
UNCLASSIFIED= when any template has no category, and one line per category,
largest first:
  CATEGORY <name> <templates> records=<records> priority=<p> drill=<entries>
(records= only with frequencies) -- paste the counts into the user-facing
summary and use the file paths for the report writer's prompt.

QUERY_PLAN_INVALID counts the plan entries without a valid `match` filter or
ranking (lib/kql_build.py, lib/classification.py); the numbers follow as
QUERY_PLAN_INVALID_ENTRIES=. `log-shape-cache` stores no such entry, so it is 0
unless the classification file was edited by hand.

Exit codes: 0 ok, 1 input problem.
"""

import argparse
import heapq
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from classification import category_names, plan_errors, priority_rank  # noqa: E402
from log_shapes import default_max_chars, prefix_hash, template_hash  # noqa: E402

DEFAULT_CLASSIFICATION_FILE = "/tmp/log-shape-classification.json"
DEFAULT_FREQS_FILE = "/tmp/log-shape-freqs.ndjson"
DEFAULT_LOG_SHAPES_FILE = "/tmp/log-shapes.ndjson"
DEFAULT_OUT_TEMPLATES = "/tmp/log-shape-templates-by-category.txt"
DEFAULT_OUT_QUERY_PLAN = "/tmp/clp-insights-query-plan.txt"
DEFAULT_OUT_CATEGORY_TOTALS = "/tmp/log-shape-category-totals.json"
DEFAULT_OUT_TOP_TEMPLATES = "/tmp/log-shape-top-templates.json"
DEFAULT_OUT_DRILL_PLAN = "/tmp/clp-insights-drill-plan.txt"
DEFAULT_FOCUS_INBOX = "/tmp/clp-insights-focus-inbox.ndjson"
DEFAULT_FOCUS_FILE = "/tmp/clp-insights-focus.json"
UNCLASSIFIED = "unclassified"


def truncate(text: str, limit: int, cut: bool = False) -> str:
    """text for display, at most limit characters; "…" marks a cut, including
    one made before (cut: text is only the start of the template)."""
    text = text.replace("\n", " <NL> ")
    if len(text) > limit:
        return text[:limit] + "…"
    return text + "…" if cut else text


def read_templates(path: str):
    """(count, text, hash, cut) per template, one NDJSON line at a time: hash
    is the stored full-template hash (None when the line has none), and cut
    says text is only the template's start. A log shapes file has no counts;
    they read as 0."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("log_shape"), str):
                count = rec.get("count", 0)
                text = rec["log_shape"]
                h = rec.get("hash") if isinstance(rec.get("hash"), str) else None
                length = rec.get("length")
                cut = isinstance(length, int) and length > len(text)
                yield (count if isinstance(count, int) else 0), text, h, cut


class Top:
    """The n largest (count, text, category) seen, text truncated once when
    kept; with ranked=False, the first n seen."""

    def __init__(self, n, trunc_chars, ranked):
        self.n, self.trunc_chars, self.ranked = n, trunc_chars, ranked
        self.items, self.seq = [], 0

    def add(self, count, text, category, cut=False):
        self.seq += 1
        key = (count, -self.seq)  # ties keep the earlier template
        if not self.ranked:
            if len(self.items) < self.n:
                self.items.append((*key, truncate(text, self.trunc_chars, cut), category))
        elif len(self.items) < self.n:
            heapq.heappush(self.items, (*key, truncate(text, self.trunc_chars, cut), category))
        elif key > self.items[0][:2]:
            heapq.heapreplace(self.items, (*key, truncate(text, self.trunc_chars, cut), category))

    def sorted(self):
        items = sorted(self.items, reverse=True) if self.ranked else self.items
        return [(c, t, cat) for c, _, t, cat in items]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract taxonomy/templates-by-category/query_plan from a "
        "log shape classification, ranked and truncated so the output "
        "stays bounded regardless of how large individual templates are."
    )
    parser.add_argument("--classification-file", default=DEFAULT_CLASSIFICATION_FILE)
    parser.add_argument("--freqs-file", default=DEFAULT_FREQS_FILE)
    parser.add_argument("--no-freqs", action="store_true")
    parser.add_argument("--log-shapes-file", default=DEFAULT_LOG_SHAPES_FILE)
    parser.add_argument("--max-per-category", type=int, default=25)
    parser.add_argument("--trunc-chars", type=int, default=180)
    parser.add_argument("--out-templates", default=DEFAULT_OUT_TEMPLATES)
    parser.add_argument("--out-query-plan", default=DEFAULT_OUT_QUERY_PLAN)
    parser.add_argument("--out-drill-plan", default=DEFAULT_OUT_DRILL_PLAN)
    parser.add_argument("--focus-inbox", default=DEFAULT_FOCUS_INBOX)
    parser.add_argument("--focus-file", default=DEFAULT_FOCUS_FILE)
    parser.add_argument("--out-category-totals", default=DEFAULT_OUT_CATEGORY_TOTALS)
    parser.add_argument("--out-top-templates", default=DEFAULT_OUT_TOP_TEMPLATES)
    parser.add_argument("--top-templates", type=int, default=30)
    parser.add_argument("--top-per-category", type=int, default=5)
    args = parser.parse_args(argv)

    try:
        with open(args.classification_file, "r", encoding="utf-8") as f:
            classification = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: failed to read {args.classification_file}: {exc}", file=sys.stderr)
        return 1
    templates = classification.get("templates", []) if isinstance(classification, dict) else []
    if any(isinstance(t, dict) and "log_shape" in t for t in templates[:1]):
        print(f"error: {args.classification_file} names templates by text ('log_shape'); "
              "classifications name them by hash -- produce it with `log-shape-cluster expand` "
              "or `log-shape-cache get`", file=sys.stderr)
        return 1
    max_chars = classification.get("max_chars")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        max_chars = default_max_chars()
    by_hash, by_prefix = {}, {}
    for t in templates:
        if isinstance(t, dict) and isinstance(t.get("category"), str):
            by_hash[t.get("hash")] = t["category"]
            by_prefix.setdefault(t.get("prefix_hash"), t["category"])

    ranked = not args.no_freqs
    source = args.freqs_file if ranked else args.log_shapes_file
    per_cat = {}  # category -> [templates, records, Top]
    overall = Top(args.top_templates, args.trunc_chars, ranked)
    seen = 0
    try:
        for count, text, h, cut in read_templates(source):
            seen += 1
            # A stored prefix is at least max_chars long, so its prefix hash is
            # the template's.
            cat = by_hash.get(h or template_hash(text))
            if cat is None:
                cat = by_prefix.get(prefix_hash(text, max_chars), UNCLASSIFIED)
            entry = per_cat.get(cat)
            if entry is None:
                entry = per_cat[cat] = [0, 0, Top(max(args.max_per_category, args.top_per_category),
                                                  args.trunc_chars, ranked)]
            entry[0] += 1
            entry[1] += count
            entry[2].add(count, text, cat, cut)
            if ranked:
                overall.add(count, text, cat, cut)
    except OSError as exc:
        hint = " (pass --no-freqs if frequencies are unavailable)" if ranked else ""
        print(f"error: failed to read {source}: {exc}{hint}", file=sys.stderr)
        return 1
    if not seen:
        print(f"error: no templates in {source}", file=sys.stderr)
        return 1

    taxonomy = {c["category"]: c for c in classification.get("taxonomy", [])
                if isinstance(c, dict) and isinstance(c.get("category"), str)}
    if ranked:
        # Exact records per category: the stored per-template counts, summed.
        # No search runs, and no keyword filter has to guess at the category.
        totals = {cat: {"templates": n, "records": r,
                        "priority": taxonomy.get(cat, {}).get("priority"),
                        "why": taxonomy.get(cat, {}).get("why")}
                  for cat, (n, r, _) in per_cat.items()}
        with open(args.out_category_totals, "w", encoding="utf-8") as f:
            json.dump(totals, f, indent=1, sort_keys=True)

        # The most frequent templates overall and per category, each with its
        # category and its exact archive-wide count, so a report can name a
        # template's count and category without anyone re-deriving them.
        top = {
            "overall": [{"count": c, "category": cat, "log_shape": text} for c, text, cat in overall.sorted()],
            "by_category": {
                cat: [{"count": c, "category": cat, "log_shape": text}
                      for c, text, _ in t.sorted()[: args.top_per_category]]
                for cat, (_, _, t) in per_cat.items()
            },
        }
        with open(args.out_top_templates, "w", encoding="utf-8") as f:
            json.dump(top, f, indent=1)

    with open(args.out_templates, "w", encoding="utf-8") as f:
        for cat, (n, _, t) in per_cat.items():
            shown = t.sorted()[: args.max_per_category]
            f.write(f"### {cat} ({n} templates total, showing top {len(shown)} by frequency)\n" if ranked
                    else f"### {cat} ({n} templates total, showing first {len(shown)})\n")
            for cnt, text, _ in shown:
                f.write(f"- {f'[{cnt}] ' if ranked else ''}{text}\n")
            f.write("\n")

    query_plan = [q for q in classification.get("query_plan", []) if isinstance(q, dict)]
    names = category_names(list(taxonomy.values()))
    invalid = [i for i, q in enumerate(query_plan, 1) if plan_errors([q], names)]
    # Core entries run on every analysis, high priority first (sorted() is
    # stable, so the classifier's order holds within a priority). Drill
    # entries wait for the user's focus.
    core = sorted((q for q in query_plan if q.get("stage") != "drill"), key=priority_rank)
    drill = sorted((q for q in query_plan if q.get("stage") == "drill"),
                   key=lambda q: (str(q.get("category")), priority_rank(q)))
    for path, entries in ((args.out_query_plan, core), (args.out_drill_plan, drill)):
        with open(path, "w", encoding="utf-8") as f:
            for q in entries:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")
    # A new analysis starts with no focus.
    open(args.focus_inbox, "w").close()
    try:
        os.remove(args.focus_file)
    except FileNotFoundError:
        pass
    drill_per_cat = {}
    for q in drill:
        drill_per_cat[q.get("category")] = drill_per_cat.get(q.get("category"), 0) + 1

    print(f"SCHEMA={json.dumps(classification.get('schema', {}))}")
    print(f"TEMPLATES={seen}")
    print(f"CATEGORIES={len(per_cat)}")
    if UNCLASSIFIED in per_cat:
        print(f"UNCLASSIFIED={per_cat[UNCLASSIFIED][0]}")
    print(f"QUERY_PLAN={len(core)}")
    print(f"DRILL_PLAN={len(drill)}")
    print(f"QUERY_PLAN_INVALID={len(invalid)}")
    if invalid:
        print(f"QUERY_PLAN_INVALID_ENTRIES={','.join(map(str, invalid))}")
    print(f"OUT_TEMPLATES={args.out_templates}")
    print(f"OUT_QUERY_PLAN={args.out_query_plan}")
    print(f"OUT_DRILL_PLAN={args.out_drill_plan}")
    print(f"FOCUS_INBOX={args.focus_inbox}")
    if ranked:
        print(f"OUT_CATEGORY_TOTALS={args.out_category_totals}")
        print(f"OUT_TOP_TEMPLATES={args.out_top_templates}")
    order = (lambda kv: -kv[1][1]) if ranked else (lambda kv: -kv[1][0])
    for cat, (n, r, _) in sorted(per_cat.items(), key=order):
        records = f" records={r}" if ranked else ""
        print(f"CATEGORY {cat} {n}{records} priority={taxonomy.get(cat, {}).get('priority', '-')} "
              f"drill={drill_per_cat.get(cat, 0)}")

    return 0

