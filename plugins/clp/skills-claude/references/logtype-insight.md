# Logtype insight reference (logtype-insights step 7)

Read this when a classification exists (`/tmp/logtype-classification.json`, either fresh from step 6 or fetched from the cache on UPTODATE). It covers executing the query plan with per-entry progress, building the insight subagent prompt, and the report format.

## Build the prompt from the classification

Extract the pieces with `logtype-insight-extract` (stdlib-only Python; do not use a raw `jq` pipeline here — see below):

```bash
jq -r '.taxonomy[] | "- \(.category): \(.description)"' /tmp/logtype-classification.json
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-insight-extract" \
  --classification-file /tmp/logtype-classification.json \
  --freqs-file FREQS_FILE
```

(pass `--no-freqs` instead of `--freqs-file` when the bootstrap reported `FREQS=UNAVAILABLE`.) It writes `/tmp/logtype-templates-by-category.txt` (paste as TEMPLATES BY CATEGORY) and `/tmp/logtype-query-plan.txt` (one query_plan entry per line, the input to `logtype-query-plan-run` below), and prints a `SCHEMA=`/`TEMPLATES=`/`CATEGORIES=`/`QUERY_PLAN_INVALID=` summary — report those counts to the user. Per category it keeps only the top `--max-per-category` templates (default 25) ranked by the frequencies file, each truncated to `--trunc-chars` (default 180); for the overwhelming majority of apps, whose templates are short and few, this changes nothing observable. It exists because a raw `jq -r '.templates | group_by(.category)[] | ...'` loads and sorts the *entire* templates array with no bound on either count or per-template length: an app that logs large near-duplicate blobs as "distinct" templates (observed: CockroachDB serializing multi-line Pebble stats tables as single messages, one category alone holding 9810 of 11558 total templates, mean template length ~184KB) produces a multi-GB classification file that turns that one `jq` call into a 10+ minute (or effectively hung) step. Never fall back to the raw `jq` pipeline to "avoid a dependency" — `logtype-insight-extract` has no third-party dependencies either, it is simply bounded.

## Repair invalid plan entries

Every query_plan entry carries a structured `match` filter that `kql-build` renders to KQL (grammar in `logtype-classify.md`, step 2 of the subagent prompt), so no model-written KQL string ever runs. When the extract reports `QUERY_PLAN_INVALID=N` above zero, `QUERY_PLAN_INVALID_ENTRIES=` lists entries without a valid `match` — typically a plan cached before plans used `match`, whose entries carry hand-written `kql` strings instead. Repair them before running the plan, or they are recorded as errors without running. Tell the user ("N cached plan entries predate structured filters; rewriting them once and updating the cache"), then:

1. List the invalid entries with the reason for each: `"${CLAUDE_PLUGIN_ROOT}/bin/kql-build" check-plan /tmp/logtype-query-plan.txt | grep ERROR`.
2. Spawn ONE repair subagent (Agent tool), model **haiku**. Give it the schema from the extract's `SCHEMA=` line, the `match` grammar from `logtype-classify.md`, the invalid entries (their lines from `/tmp/logtype-query-plan.txt`) with their `ERROR` reasons, and this instruction: "Rewrite each entry with the same label, method, project, grep, and jq, replacing its filter with an equivalent `match` and dropping any `kql` key. Where a `kql` string mixes AND and OR without parentheses, write the grouping its label means. Write `{"query_plan": [...]}` holding every entry of the plan, in order — the valid ones unchanged — to /tmp/logtype-query-plan-repaired.json, then print DONE."
3. Validate and store — `set-plan` replaces only the plan, leaving the templates and taxonomy untouched; `APP_KEY` comes from the bootstrap. If `check-plan` fails, tell the user and retry the subagent once with `sonnet`, appending the `ERROR` lines to its prompt:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/kql-build" check-plan /tmp/logtype-query-plan-repaired.json || exit 1
   "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" set-plan --key "$APP_KEY" < /tmp/logtype-query-plan-repaired.json
   jq -c '.query_plan[]' /tmp/logtype-query-plan-repaired.json > /tmp/logtype-query-plan.txt
   ```

Report the repaired entries' rendered KQL (the `check-plan` `OK` lines) to the user. The next run of this app reads the repaired plan from the cache, so the repair happens once.

## Execute the query plan, with progress

Run the plan yourself, before spawning the subagent, with `logtype-query-plan-run`. It renders each entry's `match` with `kql-build` — every value quoted and escaped, every group parenthesized — sends the KQL through `clp-s-search-kql`, prints each entry's result as soon as it finishes, and records it in `/tmp/logtype-query-results.ndjson`, one JSON line per entry: `label`, `method`, the rendered `kql`, the exact `command`, `status`, `count`, `pct`, `elapsed_s`, a few `samples` for projecting methods, and `error` for failures. An entry without a valid `match` is recorded as an error without running.

Run it in batches of at most 5 entries, one Bash call per batch (Bash timeout 600000; at the default 100 s per-query limit, a batch of 5 stays under it), and keep going until the last entry has run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run" --entries 1-5 <archive-dir>
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run" --entries 6-10 <archive-dir>
```

The first batch also counts the archive's records (`TOTAL_RECORDS=`) so each result carries a percentage; later batches reuse that count. Recorded results for the same archive and plan are kept across calls, so re-running one entry (`--entries 3`) replaces only that entry. The `PLAN_STATUS` line after each batch summarizes every recorded entry.

After each batch, post one line per entry to the user: its number, label, and result — count and percentage, or the status — plus elapsed time. Bash output is not reliably shown to the user, so this narration is how they follow the run. Each entry ends in one of these statuses:

- `ok` — ran and matched records.
- `zero` — ran and matched nothing. Every entry is derived from a template that exists, so a zero usually means the KQL does not express the template it came from.
- `error` — a stage exited non-zero, and `error` holds its stderr; or the entry has no valid `match`, and `error` says why.
- `timeout` — killed after `--timeout` seconds (default 100), typically a projecting entry over a very large match set.

A `non_selective` flag marks an entry matching at least 90% of the records: either its filter is too broad to isolate its category, or that category makes up most of the log.

When the last batch is done, print the table and show it to the user verbatim. It is the record of which queries ran and how well each worked:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run" --print-table
```

Below the table, call out each `error`, `timeout`, `zero`, or `non_selective` entry in one line. Do not fix and re-run them yourself: the subagent retries each failed entry once and logs the retry, so the table keeps showing the plan as written.

## Spawn the insight subagent

Spawn ONE insight subagent (Agent tool), model **haiku**; if the report comes back unusable, tell the user before re-spawning with `sonnet`. Replace `SEARCH_WRAPPER` with the **resolved absolute path** of `${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql` — the subagent does not inherit `${CLAUDE_PLUGIN_ROOT}`, so the literal variable will not work there. Likewise pass `FREQS_FILE` as the absolute path the bootstrap printed, and `RESULTS_FILE` as the absolute path the plan runner printed.

## Insight subagent prompt template

Fill in `ARCHIVE`, `GOAL`, `FREQS_FILE` (the bootstrap's `FREQS_FILE=` path, or `unavailable` when it reported `FREQS=UNAVAILABLE`), `RESULTS_FILE` (the plan runner's `RESULTS_FILE=` path), the schema fields, the extracted taxonomy / templates-by-category, and the `--print-table` output:

```
Analyze this CLP archive: ARCHIVE
Search wrapper: SEARCH_WRAPPER
Template frequencies: FREQS_FILE
Query plan results: RESULTS_FILE
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

QUERY PLAN RESULTS (the plan was already executed by logtype-query-plan-run,
each entry's rendered match filter; RESULTS_FILE holds one JSON line per entry
with its label, method, kql, command, status, count, pct, elapsed_s,
samples, error, and the archive's total_records):
<PASTE the --print-table output>

Method (follow strictly — avoid grep/jq over full record scans wherever
possible; the archive can hold millions of records and messages can be
large, so a per-record grep pass is the slowest option available):
1. Do NOT re-run the query plan: use each entry's recorded count, and read
   RESULTS_FILE for its samples. For an entry whose status is "error" or
   "timeout", you may run ONE corrected query (e.g. quote a wildcard value
   that contains spaces, `<message>:"*a b*"`; narrow a timed-out projection
   or turn it into a count) and list it in the Query Log under the entry's
   number. For the queries you run yourself, pick the method that fits.
   For a count: run the KQL with `--count`
   (in-engine aggregation, cannot be combined with --projection), never
   `--projection ... | grep -c '^{'`. Its cost barely depends on how many
   records match (measured ~6-7s whether a filter matched 92 or 16.5M
   records of a 16.5M-record archive). It prints one
   {"archive_id":...,"count":N} line per archive and NOTHING when zero
   records match; treat empty output as a real zero, not a failed command.
   For "project+grep": fold the target into the KQL as `<message>:"*text*"`
   alongside the given `kql` filter; for a keyword alternation, OR the
   wildcards in the same query (`<message>:"*a*" OR <message>:"*b*"`). Pipe to
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
   almost always needs a substring wildcard: `<message>:"*term*"`. Prefer that
   over project+grep for a template's distinctive STATIC text. Combine with
   a scalar field in one compound query when you can:
     <severity>:<value> AND <message>:"*term*"
     <logger>:"*<substr>*" AND <message>:"*term*"
   Fall back to project+grep only when the distinctive text needs a regex the
   wildcard syntax can't express.
3. Per-template FREQUENCIES for the whole archive (the count baseline) are
   already computed from the counts clp-s stored at compression time. The
   frequencies file holds {"count":N,"logtype":"..."} NDJSON, most frequent
   first; read the top entries with `head -20 FREQS_FILE`. Report the
   dominant templates by count as "top repeated messages". Never recompute
   frequencies by projecting and counting messages. If the frequencies are
   "unavailable", say so in the Logtype Baseline section and omit counts.
4. Total records: `total_records` in RESULTS_FILE (already counted; do not
   recount). Severity breakdown: `--count` per severity
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
   semantic("...") AND <logger>:"*<substr>*". Never run an unscoped
   semantic(). Discard any query that returns nothing or only
   generic/meaningless logtypes — do not include it in the report.

Efficiency rules:
- Compound KQL, not many separate queries.
- Quote every wildcard value, `<field>:"*text*"`, even a single word — the
  search wrapper rejects an unquoted one that contains a space.
- Project aggressively; omit --projection only when you need the full record.
- Do NOT use --tge/--tle unless the schema says the timestamp is epoch.
- `<message>:term` is an exact match, so it correctly returns 0 unless a
  message equals exactly `term`. Exact match is faster, so use it when you
  know a field's full value; message content is free text and almost always
  needs a substring wildcard — `<message>:"*term*"`. A keyword alternation
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
9. Query Log — every query YOU ran (exact KQL + flags), its result (count,
   rows, or the error text), and whether the report uses it, including
   queries that returned nothing and corrected re-runs of failed plan
   entries (labelled with the entry's number). Do not repeat the plan's own
   entries; their table is already shown.
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
9. **Query Log** — every query the subagent ran beyond the plan, with its result, including empty ones and corrected re-runs of failed plan entries. The plan's own entries are not repeated here; their table was shown when the plan finished.
