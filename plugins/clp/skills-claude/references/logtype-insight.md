# Logtype insight reference (logtype-insights step 7)

Read this when a classification exists (`/tmp/logtype-classification.json`, either fresh from step 6 or fetched from the cache on UPTODATE). It covers executing the query plan with per-entry progress, adding the baseline queries, building the report writer's prompt, and the report format.

## Build the prompt from the classification

Extract the pieces with `logtype-insight-extract` (stdlib-only Python; do not use a raw `jq` pipeline here — see below):

```bash
jq -r '.taxonomy[] | "- \(.category): \(.description)"' /tmp/logtype-classification.json
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-insight-extract" \
  --classification-file /tmp/logtype-classification.json \
  --freqs-file FREQS_FILE
```

(pass `--no-freqs` instead of `--freqs-file` when the bootstrap reported `FREQS=UNAVAILABLE`.) It writes `/tmp/logtype-templates-by-category.txt` (TEMPLATES BY CATEGORY), `/tmp/logtype-category-totals.json` (with frequencies: exact records per category, the stored per-template counts summed, no search needed) and `/tmp/logtype-query-plan.txt` (one query_plan entry per line, the input to `logtype-query-plan-run` below), and prints a `SCHEMA=`/`TEMPLATES=`/`CATEGORIES=`/`QUERY_PLAN_INVALID=` summary — report those counts to the user. Per category it keeps only the top `--max-per-category` templates (default 25) ranked by the frequencies file, each truncated to `--trunc-chars` (default 180); for the overwhelming majority of apps, whose templates are short and few, this changes nothing observable. It exists because a raw `jq -r '.templates | group_by(.category)[] | ...'` loads and sorts the *entire* templates array with no bound on either count or per-template length: an app that logs large near-duplicate blobs as "distinct" templates (observed: CockroachDB serializing multi-line Pebble stats tables as single messages, one category alone holding 9810 of 11558 total templates, mean template length ~184KB) produces a multi-GB classification file that turns that one `jq` call into a 10+ minute (or effectively hung) step. Never fall back to the raw `jq` pipeline to "avoid a dependency" — `logtype-insight-extract` has no third-party dependencies either, it is simply bounded.

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

## Add the baseline queries

The severity and logger breakdown, the records behind any rare severity, and one scoped semantic scan need nothing but the schema, so they join the plan as entries instead of being left to the report writer. Run the planner once, after any plan repair; it samples the head of the archive (a few seconds), appends its entries to `/tmp/logtype-query-plan.txt` marked `origin: "baseline"`, and replaces its own earlier entries, so a second run is harmless:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-baseline-plan" --archive <archive-dir> --schema-json '<the SCHEMA= line from the extract>'
```

Per low-cardinality field (the schema's severity and logger) it adds a `count` per common value and a `count` for the residual (everything else, where rare severities and unexpected loggers hide). A residual of a few hundred records or fewer carries a `then` rule, so the pool fetches those records itself once the count is in. The semantic entry is scoped by that residual. It never uses `--unique`, which scans every record. Report `BASELINE_ENTRIES=` and the sampled vocabulary to the user. Pass `--no-semantic` if the semantic endpoint is unavailable.

## Execute the query plan, with progress

Run the plan yourself, before spawning the report writer, with `logtype-query-plan-run`, a query pool: it holds the plan's entries and the baseline entries together and runs as many at once as memory allows. It renders each entry's `match` with `kql-build` — every value quoted and escaped, every group parenthesized — sends the KQL through `clp-s-search-kql`, prints each entry's result as soon as it finishes, and records it in `/tmp/logtype-query-results.ndjson`, one JSON line per entry: `label`, `method`, the rendered `kql`, the exact `command`, `status`, `count`, `pct`, `elapsed_s`, a few `samples` for projecting methods, and `error` for failures. An entry without a valid `match` is recorded as an error without running.

Run it once over the whole plan, as a background Bash call (`run_in_background: true`, no trailing `&`, or the harness reports it finished at once), and read its output file about every 30 seconds until `PLAN_STATUS` appears:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run" --retry-failed <archive-dir>
```

The runner decides how many searches run at once. A search holds its whole segment in memory (about 9 GiB for a 10 GB log), so it runs one search alone, measures its peak memory, and starts another only while free memory can take one more; it keeps sampling and pauses a search if memory runs short. Do not pin `--jobs` unless the user asks. It counts the archive's records first (`TOTAL_RECORDS=`) so each result carries a percentage. Recorded results for the same archive and plan are kept across calls, so re-running one entry (`--entries 3`) replaces only that entry. Results print as entries finish, in completion order. An entry's `then` rule can add a follow-up to the pool once its result is in (the baseline uses this to fetch the records behind a rare severity); those results carry `origin: "follow-up of N"`. `PEAK_CONCURRENCY=` and `PLAN_STATUS` close the run.

As entries finish, post one line per entry to the user: its number, label, and result — count and percentage, or the status — plus elapsed time. Bash output is not reliably shown to the user, so this narration is how they follow the run. Each entry ends in one of these statuses:

- `ok` — ran and matched records.
- `zero` — ran and matched nothing. Every entry is derived from a template that exists, so a zero usually means the KQL does not express the template it came from.
- `error` — a stage exited non-zero, and `error` holds its stderr; or the entry has no valid `match`, and `error` says why.
- `timeout` — killed after `--timeout` seconds (default 100), typically a projecting entry over a very large match set.

A `non_selective` flag marks an entry matching at least 90% of the records: either its filter is too broad to isolate its category, or that category makes up most of the log.

When the plan is done, print the table and show it to the user verbatim. It is the record of which queries ran and how well each worked:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run" --print-table
```

Below the table, call out each `error`, `timeout`, `zero`, or `non_selective` entry in one line. Do not fix and re-run them yourself: `--retry-failed` already retried each `error` or `timeout` entry once (marked `retried`), and the subagent may run one corrected query for a loose entry and log it.

## Compute the facts

Every number of the report is computed in code, because a small model asked to add up a table or pick the right count gets them wrong (in a trial: 49 warnings for 92, 5,370 templates for 11,558, and a 9.5-minute span for a 74-hour log). Once the pool is done:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/logtype-insight-facts" --schema-json '<the SCHEMA= line from the extract>' \
  --results-file <RESULTS_FILE> --freqs-file <FREQS_FILE>   # --freqs-file none and --category-totals none when frequencies were unavailable
```

It writes `/tmp/logtype-insight-facts.md` in well under a second: total records and templates; the severity and logger breakdowns, each with a check line showing whether it sums to the total; the category table with its sum and the records no template accounts for; the top templates overall (each with its category) and within each category, from `/tmp/logtype-top-templates.json`, which the extract writes; the fetched records grouped by message shape with counts and first/last timestamps; the semantic entries; and the flagged queries. The archive's time span is reported as unavailable unless the archive has a timestamp index. The report writer may quote these figures and no others.

## Spawn the report writer

Every query has run and every number is in the facts file, so the last step only puts them into words. The writing is where a stronger model pays off: a small writer drifts into derived figures (sums, rounded shares) and unsupported causes, and each one costs a correction round later (in a trial with haiku: 18 flagged lines and 15 edits, over three minutes). Spawn ONE subagent (Agent tool), model **opus**; if the Agent tool rejects `opus` as unavailable, use `sonnet`, and tell the user which model is writing. It runs no searches and does no arithmetic. Hand it absolute file paths (it does not inherit `${CLAUDE_PLUGIN_ROOT}`), the schema, the taxonomy, and the results table (or its path). It writes the report itself to `/tmp/logtype-insight-report.md` and replies only `DONE`, so the report is never regenerated just to be saved. If the file is missing or unusable, tell the user and re-spawn the writer once. Announce it ("facts computed; the report writer turns them into the report, about two minutes").

## Check the report

Two checkers read the saved report, and neither edits it. `logtype-report-check` catches figures mechanically. A **haiku** verifier subagent catches what a script cannot: a figure that is in the facts but attached to the wrong thing, a claim in words that no line supports, an inference stated as fact, a follow-up query that filters on a category. It also rules on the script's flags, dismissing the ones the facts support. Checking a report against given files is narrow and fast, so haiku is enough.

1. Run the script (under a second) and keep its output for the verifier:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/logtype-report-check" /tmp/logtype-insight-report.md \
     --also <the results table file> > /tmp/logtype-report-flags.txt
   ```

   It flags a figure that is in neither the facts nor the results table (with the two listed figures it sums to, if it does), a percentage the inputs never print as a percentage, a count whose only occurrences in the inputs sit next to different wording, a timestamp the inputs do not contain, and a KQL filter on a field the archive does not have. Exit 0 means nothing flagged; exit 1 means `FLAG` lines.

2. Spawn ONE verifier subagent (Agent tool), model **haiku**, with the verifier prompt below and the script's output. Announce it ("report written; a fast model is checking every claim against the facts, under a minute"). It writes `/tmp/logtype-report-issues.json` and replies `ISSUES=<n>`. If the JSON is missing or malformed, tell the user and re-spawn it once with `sonnet`.

3. If `ISSUES` is above zero, send the writer (SendMessage, same agent) the path `/tmp/logtype-report-issues.json` once, with this instruction: "Fix each issue in `/tmp/logtype-insight-report.md` in place with Edit: use the figure exactly as the facts give it, reword the claim to what the files show, label it "inference", or remove it. Derive nothing. Reply DONE."

4. Re-check once: run the script again and re-spawn the verifier on the corrected report. Do not run a third round. Any issue still listed goes to the user in a short "unverified" note beside the report, one line each, rather than being hidden.

## Verifier prompt template

Fill in `RESULTS_TABLE` and paste the script's output (or "none"):

```
Verify a Logtype Insights Report against the files it was written from. Do NOT
edit or rewrite the report and do NOT run searches: only list what is wrong.

REPORT:         /tmp/logtype-insight-report.md
FACTS_FILE:     /tmp/logtype-insight-facts.md (computed in code; every figure exact)
RESULTS_TABLE:  RESULTS_TABLE (each query's own count)
TEMPLATES_FILE: /tmp/logtype-templates-by-category.txt (template texts only;
                never a source for counts)
SCRIPT FLAGS (from logtype-report-check; some may be false positives):
<paste /tmp/logtype-report-flags.txt, or "none">

Read the report with line numbers (cat -n) and check every statement:
1. Each number, percentage, count and timestamp appears verbatim in FACTS_FILE
   or RESULTS_TABLE, attached to the same thing the report attaches it to. A
   figure that is a sum, difference, rounding or rate of other figures is an
   issue, even when the arithmetic is right.
2. A time span is stated only as the facts give it. Timestamps of fetched
   records are described as first/last seen among those records, never as the
   archive's span.
3. A cause, recommendation or characterisation of the environment that no
   line in the files shows must be labelled "inference"; stated as fact, it is
   an issue.
4. The top warning and error templates and their counts match the grouped
   records in FACTS_FILE.
5. Follow-up KQL filters only on fields FACTS_FILE lists (or semantic("...")),
   quotes every wildcard value (field:"*term*"), and never uses a category or
   template name as a field.
6. For each SCRIPT FLAG, decide whether it is real. Dismiss it only when
   FACTS_FILE shows the figure attached to the same thing the report says (for
   example a category's own record count). Otherwise it is an issue.

Write JSON to /tmp/logtype-report-issues.json:
  {"issues": [{"line": N, "quote": "<exact report text>",
               "problem": "<what is wrong; cite the file line that shows it>",
               "fix": "<the exact figure or wording from the facts | label as inference | remove>"}],
   "dismissed_flags": [{"line": N, "why": "<the FACTS_FILE line that supports it>"}]}
Then print ISSUES=<number of issues> and nothing else. An empty issues list is
a valid answer; do not invent issues.
```

## Report writer prompt template

Fill in `ARCHIVE`, `GOAL`, `FACTS_FILE` (`/tmp/logtype-insight-facts.md`), `TEMPLATES_FILE` (`/tmp/logtype-templates-by-category.txt`), `RESULTS_TABLE` (the `--print-table` output, or a path to it), the schema fields, and the taxonomy:

```
Write the Logtype Insights Report for this CLP archive: ARCHIVE
Goal: GOAL

Every query has already run and every number has already been computed. Do NOT
run searches and do NOT calculate anything: no sums, no percentages, no rates,
no durations. Read the files below (cat, head) and write the report from them.

SCHEMA (field names in this archive):
  timestamp: <TS>   severity: <SEV>   logger: <LOGGER>   message: <MSG>
  payload leaves: <...>

TAXONOMY (categories):
<PASTE taxonomy>

FILES
  FACTS_FILE: computed in code; every figure in it is exact. It holds the
    totals, the severity and logger breakdowns, the category table (with its
    sum and the records no template accounts for), the top templates overall
    (each with its category) and within each category, the fetched warnings and
    errors grouped by message shape, the semantic entries, and the flagged
    queries.
  TEMPLATES_FILE: longer template texts per category, for describing what a
    category does; <*> marks variables, " <NL> " an embedded newline, a
    trailing "…" a template cut for length. Take every count from the facts,
    never from this file.
  RESULTS_TABLE: the keyword probes and baseline queries with their counts.
    The probes are loose keyword filters: prefer the facts' category records
    over a probe's count, and say so when a probe is flagged non-selective.

Rules:
1. Every number, percentage, count and timestamp in the report must appear
   verbatim in FACTS_FILE (or in RESULTS_TABLE for a query's own count). If a
   figure you want is not there, leave it out; never derive one.
2. Where the facts say the time span is unavailable, say it is unavailable.
   The timestamps in the grouped records cover those records only; say
   "first/last seen among the fetched records", never "the archive spans".
3. Do not state a rate, a duration, or a cause as fact. A cause or a
   recommendation is inference: label it "inference".
4. Name the top warning and error templates from the grouped records, with
   their counts, exactly as the facts list them.
5. Report semantic findings only when they add something to the templates,
   with their kql; otherwise one line saying semantic search surfaced nothing
   beyond the baseline.
6. A count belongs to the one line it is printed on. Quote it as the facts give
   it; never add two counts together, and never give one group's count to
   another group.
7. Records with no value in a field are listed apart from the field's values;
   never nest them under one of the values or its total.
8. A follow-up KQL query may filter only on the fields the facts list, and on
   semantic("..."). Categories, templates and the taxonomy are not fields:
   never write `category:` or similar.
9. Describe only what the files show. No characterisation of the environment
   (for example "production-grade") that no line supports.

Write ONLY the Markdown Logtype Insights Report to
/tmp/logtype-insight-report.md (Write tool), then reply DONE and nothing else.
The report has these sections:
1. Summary -- total records, severity counts, top logger/component, what the
   application appears to be doing (from the dominant templates).
2. Logtype Baseline -- distinct templates, the top templates by frequency, the
   category table (templates and records per category), and the records no
   template accounts for. Flag a category the facts mark as one template per
   record as near-duplicate blobs, not that many behaviours.
3. Issues & Warnings -- error and warning counts and the top templates from
   the grouped records, with actionable problems (labelled inference where
   they are).
4. Notable Categories -- per category of interest, records and representative
   templates, and what they indicate.
5. Performance Signals -- timing, throughput and slow-operation templates and
   the counts the facts give.
6. Configuration & Startup.
7. Semantic Search Coverage.
8. Top 3 follow-up KQL queries, derived from templates (mix keyword and
   semantic).
9. Query Log -- the baseline and follow-up entries by index, kql and count
   (from RESULTS_TABLE), and every flagged query. The plan's own entries are
   already in the table shown to the user.
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
