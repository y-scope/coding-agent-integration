# Logtype insight reference (logtype-insights step 7)

Read this when a classification exists (`/tmp/logtype-classification.json`, either fresh from step 6 or fetched from the cache on UPTODATE). It covers building the insight subagent prompt and the report format.

## Build the prompt from the classification

Extract the pieces with `logtype-insight-extract` (stdlib-only Python; do not use a raw `jq` pipeline here — see below):

```bash
jq -r '.taxonomy[] | "- \(.category): \(.description)"' /tmp/logtype-classification.json
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-insight-extract" \
  --classification-file /tmp/logtype-classification.json \
  --freqs-file FREQS_FILE
```

(pass `--no-freqs` instead of `--freqs-file` when the bootstrap reported `FREQS=UNAVAILABLE`.) It writes `/tmp/logtype-templates-by-category.txt` (paste as TEMPLATES BY CATEGORY) and `/tmp/logtype-query-plan.txt` (one query_plan entry per line, paste as QUERY PLAN), and prints a `SCHEMA=`/`TEMPLATES=`/`CATEGORIES=` summary — report those counts to the user. Per category it keeps only the top `--max-per-category` templates (default 25) ranked by the frequencies file, each truncated to `--trunc-chars` (default 180); for the overwhelming majority of apps, whose templates are short and few, this changes nothing observable. It exists because a raw `jq -r '.templates | group_by(.category)[] | ...'` loads and sorts the *entire* templates array with no bound on either count or per-template length: an app that logs large near-duplicate blobs as "distinct" templates (observed: CockroachDB serializing multi-line Pebble stats tables as single messages, one category alone holding 9810 of 11558 total templates, mean template length ~184KB) produces a multi-GB classification file that turns that one `jq` call into a 10+ minute (or effectively hung) step. Never fall back to the raw `jq` pipeline to "avoid a dependency" — `logtype-insight-extract` has no third-party dependencies either, it is simply bounded.

Spawn ONE insight subagent (Agent tool), model **haiku**; if the report comes back unusable, tell the user before re-spawning with `sonnet`. Replace `SEARCH_WRAPPER` with the **resolved absolute path** of `${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql` — the subagent does not inherit `${CLAUDE_PLUGIN_ROOT}`, so the literal variable will not work there. Likewise pass `FREQS_FILE` as the absolute path the bootstrap printed.

## Insight subagent prompt template

Fill in `ARCHIVE`, `GOAL`, `FREQS_FILE` (the bootstrap's `FREQS_FILE=` path, or `unavailable` when it reported `FREQS=UNAVAILABLE`), the schema fields, and the extracted taxonomy / templates-by-category / query_plan:

```
Analyze this CLP archive by executing the provided query plan: ARCHIVE
Search wrapper: SEARCH_WRAPPER
Template frequencies: FREQS_FILE
Goal: GOAL

SCHEMA (field names in this archive):
  timestamp: <TS>   severity: <SEV>   logger: <LOGGER>   message: <MSG>
  payload leaves: <...>     time-range flags work: <yes if epoch / no if string>

TAXONOMY (categories):
<PASTE taxonomy>

TEMPLATES BY CATEGORY (top templates per category by frequency out of the
category's full count, which is stated per category; <*> marks variables; a
"[N]" prefix is the archive-wide occurrence count from the frequencies file,
absent when frequencies are unavailable; a trailing "…" means the template
text was truncated for length, and " <NL> " marks an embedded literal newline
in the original message):
<PASTE templates grouped by category>

QUERY PLAN (each entry derived from a real template — execute each):
<PASTE query_plan>

Method (follow strictly — avoid grep/jq over full record scans wherever
possible; the archive can hold millions of records and messages can be
large, so a per-record grep pass is the slowest option available):
1. Execute every query_plan entry. For "count": run the KQL with `--count`
   (in-engine aggregation, cannot be combined with --projection), never
   `--projection ... | grep -c '^{'`. Its cost barely depends on how many
   records match (measured ~6-7s whether a filter matched 92 or 16.5M
   records of a 16.5M-record archive). It prints one
   {"archive_id":...,"count":N} line per archive and NOTHING when zero
   records match; treat empty output as a real zero, not a failed command.
   For "project+grep": fold the target into the KQL as `<message>:*text*`
   alongside the given `kql` filter; for a keyword alternation, OR the
   wildcards in the same query (`<message>:*a* OR <message>:*b*`). Pipe to
   `grep -Ei '<grep>'` only when the target needs real regex features
   (anchors, character classes, backreferences). For "project+jq": run the
   KQL with --projection, then `grep '^{' | jq -r '<jq>'`. For "semantic":
   run `semantic("...") AND <kql>` with --projection. When you only need a
   few example records, add `--limit N` (it caps the output; it saves time
   only if the limit is reached before later tables or archives are read).
2. `<message>:term` is an **exact** match against the whole field value, so
   it correctly returns 0 unless a message equals exactly `term` — the
   message field follows the same KQL rule as any other field. Exact match
   is faster, so use it directly wherever a field's full value is known
   (severity, an exact logger path); message content is free text, so it
   almost always needs a substring wildcard: `<message>:*term*`. Prefer that
   over project+grep for a template's distinctive STATIC text. Combine with
   a scalar field in one compound query when you can:
     <severity>:<value> AND <message>:*term*
     <logger>:*<substr>* AND <message>:*term*
   Fall back to project+grep only when the distinctive text needs a regex the
   wildcard syntax can't express.
3. Per-template FREQUENCIES for the whole archive (the count baseline) are
   already computed from the counts clp-s stored at compression time. The
   frequencies file holds {"count":N,"logtype":"..."} NDJSON, most frequent
   first; read the top entries with `head -20 FREQS_FILE`. Report the
   dominant templates by count as "top repeated messages". Never recompute
   frequencies by projecting and counting messages. If the frequencies are
   "unavailable", say so in the Logtype Baseline section and omit counts.
4. Total records: `'*' --count`. Severity breakdown: `--count` per severity
   value, including the dominant one (it costs the same as a rare one, so
   never count the rare values and subtract). Logger breakdown: `--count`
   per value when the logger field is low-cardinality; when its values are
   unknown, list them first with `--unique <logger>` (it still scans the
   matching records). Time span: project the timestamp field and use
   head/tail (records are chronological; do NOT sort), OR use --tge/--tle if
   the schema says time-range flags work.
5. MANDATORY semantic pass — in addition to any query_plan entries whose
   method is "semantic", always run at least one scoped semantic() query
   derived from the goal or the dominant templates, e.g.
   semantic("...") AND <severity>:<value> or
   semantic("...") AND <logger>:*<substr>*. Never run an unscoped
   semantic(). Discard any query that returns nothing or only
   generic/meaningless logtypes — do not include it in the report.

Efficiency rules:
- Compound KQL, not many separate queries.
- Project aggressively; omit --projection only when you need the full record.
- Do NOT use --tge/--tle unless the schema says the timestamp is epoch.
- `<message>:term` is an exact match, so it correctly returns 0 unless a
  message equals exactly `term`. Exact match is faster, so use it when you
  know a field's full value; message content is free text and almost always
  needs a substring wildcard — `<message>:*term*`. A keyword alternation
  is not a reason to grep; OR the wildcards in the KQL query instead. Fall
  back to projecting and grepping/jq-filtering only when the match needs real
  regex features (anchors, character classes, backreferences).
- For "how many records match this group of templates", sum `count` over the
  matching templates in FREQS_FILE — O(distinct templates), not O(records).
- Add --ignore-case when case is uncertain.

Return ONLY a Markdown Logtype Insights Report with these sections:
1. Summary — total records, severity counts, time span, top logger/component.
2. Logtype Baseline — total distinct templates; the top N templates by
   frequency (count + template); the discovered category breakdown
   (errors: K templates, performance: K, ...). This is the spine of the
   report. If one category's true count is far larger than the number of
   templates shown for it, say so and note the likely cause (e.g. the app
   logs large near-duplicate blobs — a multi-line stats table, a stack
   trace — as a single message, so minor byte differences between dumps
   register as distinct templates instead of one recurring one); do not
   treat that inflated count as evidence of that many genuinely different
   behaviors.
3. Issues & Warnings — error/warning counts, top 3 warning TEMPLATES (not
   substrings), actionable problems; semantic-only findings if any.
4. Notable Categories — for each discovered category of interest, counts +
   representative templates and what they indicate.
5. Performance Signals — timing/throughput/slow-operation templates and counts
   (if the app produces any); semantic-only findings if any.
6. Configuration & Startup — config/init templates grounded in the baseline
   (if any).
7. Semantic Search Coverage — MANDATORY (the semantic pass always runs).
   Report ONLY meaningful findings: matches that template-classification
   missed or confirmed, with their queries. NEVER list empty/no-hit queries
   or meaningless matches — drop them. If nothing meaningful was found, the
   section is a single line saying semantic search surfaced nothing beyond
   the baseline.
8. Top 3 follow-up KQL queries (derived from templates, mix keyword+semantic).
```

## Report format (present in this order)

1. **Summary** — total records, severity counts, archive span, top logger/component.
2. **Logtype Baseline** — distinct template count, top templates by frequency with counts, the discovered category breakdown. The spine of the report. Flag a category whose true count dwarfs the templates shown for it as a likely large-near-duplicate-blob artifact, not genuine behavioral diversity.
3. **Issues & Warnings** — errors, warnings, top 3 warning *templates* (grounded, not guessed), actionable problems; semantic-only findings if any.
4. **Notable Categories** — per discovered category of interest, counts + representative templates and what they indicate.
5. **Performance Signals** — timing/throughput/slow-operation templates and counts (if the app produces any); semantic-only findings if any.
6. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
7. **Semantic Search Coverage** — mandatory (the semantic pass always runs), but report only meaningful findings — matches that template-classification missed or confirmed, with their queries; drop empty/no-hit queries. If nothing meaningful surfaced, one line saying so.
8. **Follow-up queries** — 2–3 concrete queries derived from templates.
