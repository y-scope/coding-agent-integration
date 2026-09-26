# Log shape insight reference (log-insights steps 6–9)

Read this when a classification exists (`/tmp/log-shape-classification.json`, either fresh from step 6 or fetched from the cache on UPTODATE); the context question below is asked at step 6, before it does. It covers the three questions to the user, the summary, building the insight inputs, the core plan's pool and the focus queued into it, the facts, the report writer's prompt, saving the report, and the report format.

## Ask what the user already knows (step 6)

Ask right after spawning the classifier, so the user answers while it runs (on UPTODATE, ask it together with the focus question at step 8). One AskUserQuestion, header "Context", not multi-select:

- question: "While I classify the N templates: what do you already know about these logs?" (on UPTODATE, with nothing to classify: "What do you already know about these logs?")
- "Chasing a problem" — something went wrong; the automatic "Other" field is where they say what (a symptom, a time, a component).
- "Checking something specific" — a question they want answered.
- "Just exploring" — no background; the whole picture is what they want.

Keep the answer, verbatim, for the focus question and `clp-insights focus --context`. When the user picks "Chasing a problem" or "Checking something specific" without saying what, ask what in the focus question's "Other" field; do not ask a third question. Never pass it to the classifier: the classification is cached per app and reused for every later capture, while the answer is about this one. When no one can answer (a headless run, or AskUserQuestion unavailable), skip both questions.

## Build the insight inputs (step 7)

Extract the pieces with `clp-insights extract` (stdlib-only Python; do not use a raw `jq` pipeline here — see below):

```bash
jq -r '.taxonomy[] | "- \(.category) [\(.priority)]: \(.description) -- \(.why)"' /tmp/log-shape-classification.json
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" extract \
  --classification-file /tmp/log-shape-classification.json \
  --freqs-file FREQS_FILE
```

(pass `--no-freqs` instead of `--freqs-file` when the bootstrap reported `FREQS=UNAVAILABLE`; it then reads the template texts from `/tmp/log-shapes.ndjson`.) The classification names templates by hash, not by text; the extract streams the frequencies file, whose lines carry each template's hash, and joins it to its category, so it never holds every template at once. It writes:

- `/tmp/log-shape-templates-by-category.txt` (TEMPLATES BY CATEGORY);
- `/tmp/log-shape-category-totals.json` (with frequencies: exact records per category, the stored per-template counts summed, no search needed, with each category's `priority` and `why`);
- `/tmp/log-shape-query-plan.txt`, the core plan: the `core` entries, one per line, high priority first — the input to the pool below;
- `/tmp/log-shape-drill-plan.txt`, the `drill` entries, which run only when the user focuses on their category;

and it empties the focus inbox, `/tmp/log-shape-focus-inbox.ndjson`, and removes the previous run's `/tmp/log-shape-focus.json`. It prints `SCHEMA=`/`TEMPLATES=`/`CATEGORIES=`/`QUERY_PLAN=`/`DRILL_PLAN=`/`QUERY_PLAN_INVALID=`, `UNCLASSIFIED=` when a template matched no classified one, and a `CATEGORY <name> <templates> records=<n> priority=<p> drill=<k>` line per category, largest first — the summary below is built from them. `QUERY_PLAN_INVALID` is 0 for any classification `log-shape-cache` produced, since it stores no entry without a valid `match` and ranking; if it is not, report it and stop. Per category it keeps only the top `--max-per-category` templates (default 25) ranked by the frequencies file, each truncated to `--trunc-chars` (default 180). That bound matters for apps that log large near-duplicate blobs as "distinct" templates (observed: CockroachDB serializing multi-line Pebble stats tables as single messages, one category alone holding 9810 of 11558 total templates, mean template length ~184KB); for the overwhelming majority of apps, whose templates are short and few, it changes nothing observable.

## The baseline queries

The severity and logger breakdown, the records behind any rare severity, and one scoped semantic scan need nothing but the schema, so step 4 of the skill already wrote them to their own plan, `/tmp/log-shape-baseline-plan.txt`, and started their pool in the background, with results in `/tmp/log-shape-baseline-results.ndjson`:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" baseline-plan --archive <archive-dir> \
  --schema-json '{"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>"}'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" run --retry-failed \
  --query-plan-file /tmp/log-shape-baseline-plan.txt \
  --results-file /tmp/log-shape-baseline-results.ndjson <archive-dir>
```

Per low-cardinality field (the schema's severity and logger) the planner adds a `count` per common value and a `count` for the residual (everything else, where rare severities and unexpected loggers hide). A residual of a few hundred records or fewer carries a `then` rule, so the pool fetches those records itself once the count is in. The semantic entry is scoped by that residual. It never uses `--unique`, which scans every record. Pass `--no-semantic` if the semantic endpoint is unavailable. If the baseline pool has not exited when the plan below is ready, wait for it before starting the plan's pool: each pool sizes itself from free memory, and two at once would both count the same memory.

## Start the core plan, and summarize (step 7)

Run the plan yourself, before spawning the report writer, with `clp-insights run`, a query pool: it holds the plan's entries and runs as many at once as memory allows. It renders each entry's `match` with `kql-build` — every value quoted and escaped, every group parenthesized — sends the KQL through `clp-s-search-kql`, prints each entry's result as soon as it finishes, and records it in `/tmp/log-shape-query-results.ndjson`, one JSON line per entry: `label`, `method`, the rendered `kql`, the exact `command`, `status`, `count`, `pct`, `elapsed_s`, a few `samples` for projecting methods, and `error` for failures. An entry without a valid `match` is recorded as an error without running.

Run it once over the core plan, as a background Bash call (`run_in_background: true`, no trailing `&`, or the harness reports it finished at once), with the focus inbox, and follow its output file with the Monitor tool until `PLAN_STATUS` appears: `tail -n +1 -F <output-file> | grep --line-buffered -E '^\[[0-9]+/[0-9]+\]|^INBOX|PLAN_STATUS|Traceback|rror'`, `timeout_ms` at its maximum (re-arm it if it expires first), stopped with TaskStop when the harness reports the call exited. Never wait with `sleep` between reads; the harness blocks a foreground `sleep N; <command>`:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" run --retry-failed \
  --inbox /tmp/log-shape-focus-inbox.ndjson <archive-dir>
```

With `--inbox` the pool also takes entries from the inbox while it runs: each goes ahead of every core entry not yet started, so the user's focus runs next even on an archive where each search takes minutes. The pool does not exit until the inbox is closed — `clp-insights focus` closes it — and after its core plan it prints `INBOX waiting ...` until then. With no close line it gives up after `--inbox-timeout` seconds (default 900; `INBOX=timed-out`), so a question left unanswered does not hold it forever.

Then post the **summary**, while the pool runs. Keep it to about ten lines, every figure from the bootstrap, the baseline results and the extract's `CATEGORY` lines. It is the one place the total record count and the category table appear; later messages refer back to them:

- total records and the severity split (the baseline's counts);
- the categories as a small table — records, templates, priority — largest first, with the low-priority ones folded into one line;
- the classifier's `why` for each high-priority category, one line each;
- when the user gave context, which categories it points at and why, in one line.

## Ask for the focus, and queue it (step 8)

Ask in one AskUserQuestion, header "Focus", not multi-select (on UPTODATE, the context question above goes in the same call as its first question):

- question: "What should the report focus on?"
- first option, marked "(Recommended)": the categories the user's context points at, when it points at any ("request-handling + service-discovery — matches 'requests dropping'"); otherwise "Everything".
- one option per remaining high-priority category, largest first, its description the classifier's `why`, its record count, and how many deeper checks choosing it queues (the extract's `drill=` count; for 0, "I'll write one to three checks from its templates") — up to the four options AskUserQuestion allows;
- "Everything", if the first option is not already it, described truthfully: "The standard checks already cover every category; no extra queries."

The automatic "Other" takes the user's own question. Then run `clp-insights focus` ONCE — even for "Everything", since it is what closes the inbox:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" focus --category <C> [--category <C2>] \
  [--entries-file /tmp/log-shape-focus-entries.ndjson] \
  --context '<the context answer, verbatim, or empty>' --question '<the user\'s own question, or empty>'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" focus --everything --context '<...>'            # the whole picture
```

- A **category** queues its drill entries from `/tmp/log-shape-drill-plan.txt`. `NO_DRILL=<C>` means it has none: write entries for it as below.
- The user's **own question**, or **context** that names something specific (a component, a symptom, an error text), gets 1–3 entries you write to `/tmp/log-shape-focus-entries.ndjson`, one JSON entry per line, in the same shape as a plan entry: `label`, `match` (the grammar in `log-shape-classify.md`), `method`, `project` for a projecting method, and `category` when one fits. Derive each from templates in `/tmp/log-shape-templates-by-category.txt` that exist, as the classifier does; for a concept rather than a phrase, use a `semantic` node inside an `all` beside a concrete filter. `clp-insights focus` checks every entry and queues nothing if one is invalid (exit 1, inbox left open): fix it and run it again.
- A **time** the user mentions ("around 10:12") cannot be a filter — `match` has no time range — so keep it for the writer: it is in the context, and the fetched records carry timestamps.

`clp-insights focus` prints each queued entry with its KQL, then `FOCUS=` and `FOCUS_ENTRIES=`; tell the user in one plain line what was queued ("Queued 2 deeper checks on slow SQL transactions; they run next"), or, for `FOCUS_ENTRIES=0`, that the standard checks already cover it. Then follow the pool.

## Follow the pool (step 8)

The runner decides how many searches run at once. A search holds its whole segment in memory (about 9 GiB for a 10 GB log), so it runs one search alone, measures its peak memory, and starts another only while free memory can take one more; it keeps sampling and pauses a search if memory runs short. Do not pin `--jobs` unless the user asks. It counts the archive's records first (`TOTAL_RECORDS=`) so each result carries a percentage. Recorded results for the same archive and plan are kept across calls, so re-running one entry (`--entries 3`) replaces only that entry. Results print as entries finish, in completion order. An entry's `then` rule can add a follow-up to the pool once its result is in (the baseline uses this to fetch the records behind a rare severity); those results carry `origin: "follow-up of N"`. `PEAK_CONCURRENCY=` and `PLAN_STATUS` close the run.

Do not post a line per entry. While the pool runs, post one status line each time a minute passes without news (`12 of 20 checks done`); Bash output is not reliably shown to the user, so this line is how they know the run is alive. Each entry ends in one of these statuses:

- `ok` — ran and matched records.
- `zero` — ran and matched nothing. Every entry is derived from a template that exists, so a zero usually means the KQL does not express the template it came from.
- `error` — a stage exited non-zero, and `error` holds its stderr; or the entry has no valid `match`, and `error` says why.
- `timeout` — killed after `--timeout` seconds (default 100), typically a projecting entry over a very large match set.

A `non_selective` flag marks an entry matching at least 90% of the records: either its filter is too broad to isolate its category, or that category makes up most of the log.

The focus entries (`origin: "focus"`, marked "(focus)" in the table) are numbered after the core plan's last entry. When the pool is done, print both tables and save them: the report check reads them, and the report's Query Log reproduces them. Do not paste them into the chat. Each is numbered from 1; cite an entry as "baseline #N" or "plan #N" in the report only:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" run --print-table \
  --results-file /tmp/log-shape-baseline-results.ndjson | tee /tmp/log-shape-baseline-table.md
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" run --print-table | tee /tmp/log-shape-plan-table.md
```

Then close phase 4 in one or two lines: how many checks ran, and each `error`, `timeout` or `zero` entry with what it costs the report. Mention a `non_selective` entry only when a value that dominates the log does not explain it. Do not fix and re-run them yourself: `--retry-failed` already retried each `error` or `timeout` entry once (marked `retried`), and the subagent may run one corrected query for a loose entry and log it.

## Compute the facts, and post the early numbers (step 9)

Every number of the report is computed in code, because a small model asked to add up a table or pick the right count gets them wrong (in a trial: 49 warnings for 92, 5,370 templates for 11,558, and a 9.5-minute span for a 74-hour log). Once the pool is done:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-insights" facts --schema-json '<the SCHEMA= line from the extract>' \
  --archive-dir <archive-dir> \
  --schema-tree-file /tmp/log-shape-schema-tree.json \
  --freqs-file <FREQS_FILE>   # --freqs-file none and --category-totals none when frequencies were unavailable
```

It reads both results files (`--baseline-results-file`, `--results-file`; the defaults are the paths above) and `clp-insights focus`'s `/tmp/log-shape-focus.json`, and writes `/tmp/log-shape-insight-facts.md` in well under a second: first the user's focus — its categories with their records and the classifier's `why`, the user's question and context verbatim, and the focus queries' results with samples — then total records and templates; the severity and logger breakdowns, each with a check line showing whether it sums to the total; the category table with its sum and the records no template accounts for; the top templates overall (each with its category) and within each category, from `/tmp/log-shape-top-templates.json`, which the extract writes; the fetched records grouped by message shape with counts and first/last timestamps; the semantic entries; and the flagged queries. The archive's time span comes from `timeRange` in its `.yscope-clp-archive.json`: the earliest and latest timestamp across every record, which `clp-s-compress-folder` and `clp-s-compress-session` record when they compress with a timestamp key. Without one the span is given as unavailable, with the reason. A sample or example shows what its record says: the strings at the schema's message field, stepping through arrays (a list of content blocks), else at the nearest ancestor of that field the record has, preferring text over ids and enum values; a record with no message (a duration or status record) is shown by its other fields as `key=value`. The report writer may quote these figures and no others.

Then post the **early numbers**: 3 to 5 lines quoted from the facts file, the focus first — the focus queries' counts, what their samples show, then the one or two figures that matter most elsewhere. The writer takes about two minutes; this way the user has the headline while it works. Quote figures as the facts file gives them, and draw no conclusions the writer has not been asked to check.

## Spawn the report writer

Every query has run and every number is in the facts file, so the last step only puts them into words. The writing is where a stronger model pays off: a small writer drifts into derived figures (sums, rounded shares) and unsupported causes, and each one costs a correction round later (in a trial with haiku: 18 flagged lines and 15 edits, over three minutes). Spawn ONE subagent (Agent tool), model **opus**; if the Agent tool rejects `opus` as unavailable, use `sonnet`, and tell the user which model is writing. It runs no searches and does no arithmetic. Hand it absolute file paths (it does not inherit `${CLAUDE_PLUGIN_ROOT}`), the schema, the taxonomy, the focus and the user's context (both also in the facts file's first section), and the results table (or its path). It writes the report itself to `/tmp/log-shape-insight-report.md` and replies only `DONE`, so the report is never regenerated just to be saved. If the file is missing or unusable, tell the user and re-spawn the writer once. Before spawning, open phase 5 with one line naming the model and the time (`[5/5] Writing the report with opus (~2 min)`); the estimate tells the user the wait is expected. Right after spawning it, ask where to save the report (next section).

## Ask where to save the report (step 9)

Ask right after spawning the writer, so the user answers while it works. First list what this machine can produce (instant; it only looks for a browser to print PDF with):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-report" save --list-formats
```

It prints `FORMATS=` and `PDF_ENGINE=` (a browser's path, or `none`). Then one AskUserQuestion with two questions. Fill in the real paths: `<name>` is the source log file or folder's name when this run compressed it, else the archive directory's name, and `<stamp>` is `YYYYmmdd-HHMM`.

1. header "Format", **multi-select**: "Which formats should I save the report in?" Offer only what can be produced:
   - "HTML (Recommended)": "A styled page for any browser, light or dark, with readable tables."
   - "Markdown": "The report as written: plain text that renders on GitHub and in editors."
   - "PDF", only when `PDF_ENGINE` is not `none`: "Printed from the HTML page, for attaching or printing."
   - "claude.ai page", only when the Artifact tool is in this session's tool list: "Published to claude.ai as a private page you can share by link. It quotes lines from these logs (hosts, paths)."
2. header "Location", single-select: "Where should I save the report file?"
   - "This directory (Recommended)": "`<cwd>/log-insights-<name>-<stamp>.<ext>`"
   - "Next to the logs": "`<the source logs' directory>/log-insights-<name>-<stamp>.<ext>`". When the input was an archive, use the archive directory's parent and label it "Next to the archive".
   - "Temporary folder": "`/tmp/log-insights-<name>-<stamp>.<ext>`, for a quick look; /tmp may be cleared on reboot."

   The automatic "Other" takes a folder or a file name. A file name's `.md`, `.html` or `.pdf` extension is replaced by each chosen format's own; when it names a format the user did not tick, add that format.

When the user picks only "claude.ai page", the location answer is not used. When no one can answer, skip the question: the report stays at `/tmp/log-shape-insight-report.md` and nothing is saved or published.

## Check the report

`clp-report check` reads the saved report and flags figures mechanically; it never edits the report. There is no verifier subagent: the writer rules on the flags itself in one fix round.

1. Run the script (under a second):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-report" check /tmp/log-shape-insight-report.md \
     --also /tmp/log-shape-baseline-table.md --also /tmp/log-shape-plan-table.md \
     --schema-tree-file /tmp/log-shape-schema-tree.json > /tmp/log-shape-report-flags.txt
   ```

   It flags a figure that is in neither the facts nor the results table (with the two listed figures it sums to, if it does), a percentage the inputs never print as a percentage, a count whose only occurrences in the inputs sit next to different wording, a timestamp the inputs do not contain, and a KQL filter on a field the archive does not have. Exit 0 means nothing flagged; exit 1 means `FLAG` lines.

2. If it exits 1, send the writer (SendMessage, same agent) the path `/tmp/log-shape-report-flags.txt` once, with this instruction: "Rule on each FLAG line in `/tmp/log-shape-insight-report.md`. Leave the figure only when it is not a statistic (part of a path, an ID, or text quoted from a template) or the facts show it attached to the same thing the report says. Otherwise fix it in place with Edit: use the figure exactly as the facts give it, reword the claim to what the files show, label it "inference", or remove it. Derive nothing. Reply with one line per flag you left, giving the line number and why, then DONE."

3. Re-run the script once on the corrected report. Do not run a second fix round. Any flag still listed that the writer did not justify goes to the user in a short "unverified" note beside the report, one line each, rather than being hidden.

## Save the report (step 10)

After the check, save every chosen file format in one run. `--dest` is the chosen folder or file name (a folder gets the default name inside it), and `--name` is the `<name>` from the question:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-report" save --format html,pdf --dest <folder-or-file> --name <name>
```

It prints `SAVED_<FORMAT>=<path>` per file. It never overwrites a file (it adds `-2`, `-3`, ... instead) and creates missing folders. PDF is printed by a headless Chrome, Chromium or Edge without web fonts, so it needs no network. `PDF_ERROR=` means that one format failed (exit 1): tell the user the reason in one line and keep the other files. Never install a browser to get PDF.

For a claude.ai page, add `artifact` to `--format` (or run it alone with `--format artifact`). It writes `/tmp/log-shape-insight-report.artifact.html`, a finished page that already follows the Artifact page contract (both themes, phone width, tables that scroll on their own). Publish it as-is with the Artifact tool: `file_path` that file, `icon` `"report"`, and a one-sentence `description` naming the logs and the headline figure ("Log insights for cockroach.node1.log: 16.5M records, 11,558 templates"). Do not rewrite or restyle the page, and declare no capabilities. Give the user the link the publish returns; the page is private until they share it.

## Report writer prompt template

Fill in `ARCHIVE`, `GOAL`, `FOCUS` (the chosen categories, the user's own question, or "everything"), `USER_CONTEXT` (the context answer verbatim, or "none"), `FACTS_FILE` (`/tmp/log-shape-insight-facts.md`), `TEMPLATES_FILE` (`/tmp/log-shape-templates-by-category.txt`), `RESULTS_TABLE` (the two saved tables, `/tmp/log-shape-baseline-table.md` and `/tmp/log-shape-plan-table.md`), the schema fields, and the taxonomy:

```
Write the Log Insights Report for this CLP archive: ARCHIVE
Goal: GOAL
Focus the user chose: FOCUS
What the user said they already know: USER_CONTEXT

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
    user's focus and context with the focus queries' results (first), the
    totals, the severity and logger breakdowns, the category table (with its
    sum and the records no template accounts for), the top templates overall
    (each with its category) and within each category, the fetched warnings and
    errors grouped by message shape, the semantic entries, and the flagged
    queries.
  TEMPLATES_FILE: longer template texts per category, for describing what a
    category does; <*> marks variables, " <NL> " an embedded newline, a
    trailing "…" a template cut for length. Take every count from the facts,
    never from this file.
  RESULTS_TABLE: two tables, the baseline queries and the plan's keyword
    probes, each with its counts and numbered from 1 (cite "baseline #N" or
    "plan #N").
    The probes are loose keyword filters: prefer the facts' category records
    over a probe's count, and say so when a probe is flagged non-selective.
    Entries marked "(focus)" ran for the user's focus.

Rules:
1. Every number, percentage, count and timestamp in the report must appear
   verbatim in FACTS_FILE (or in RESULTS_TABLE for a query's own count). If a
   figure you want is not there, leave it out; never derive one.
2. The archive's time span is the facts' "Time span" line: quote it as it
   is, and where it says unavailable, say it is unavailable. The timestamps
   in the grouped records cover those records only; say "first/last seen
   among the fetched records", never present them as the archive's span.
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
10. Lead with the focus. The user's context is their account, not a finding:
   say whether the files support it, contradict it, or say nothing about it,
   and quote the lines that decide it. Never restate it as a fact.

Write ONLY the Markdown Log Insights Report to
/tmp/log-shape-insight-report.md (Write tool), then reply DONE and nothing else.
The report has these sections:
1. Summary -- total records, severity counts, top logger/component, what the
   application appears to be doing (from the dominant templates).
2. Focus -- the answer to what the user asked for: the focus categories'
   records and templates, the focus queries' results, and, when the user gave
   context, whether the records support it, contradict it, or say nothing
   about it. For a focus of "everything", the high-priority categories.
3. Log Shape Baseline -- distinct templates, the top templates by frequency, the
   category table (priority, templates and records per category), and the
   records no template accounts for. Flag a category the facts mark as one
   template per record as near-duplicate blobs, not that many behaviours.
4. Issues & Warnings -- error and warning counts and the top templates from
   the grouped records, with actionable problems (labelled inference where
   they are).
5. Notable Categories -- per category of interest outside the focus, records
   and representative templates, and what they indicate. Keep this short
   when the focus is narrow.
6. Performance Signals -- timing, throughput and slow-operation templates and
   the counts the facts give.
7. Configuration & Startup.
8. Semantic Search Coverage.
9. Top 3 follow-up KQL queries, derived from templates (mix keyword and
   semantic), leaning toward the focus.
10. Query Log -- both tables from RESULTS_TABLE, verbatim (the chat does not
   show them), then every flagged query with a one-line note.
```

## Report format (present in this order)

1. **Summary** — total records, severity counts, archive span, top logger/component.
2. **Focus** — what the user asked for, answered first: the focus categories and queries, and whether the records bear out the user's context.
3. **Log Shape Baseline** — distinct template count, top templates by frequency with counts, the discovered category breakdown. The spine of the report. Flag a category whose true count dwarfs the templates shown for it as a likely large-near-duplicate-blob artifact, not genuine behavioral diversity.
4. **Issues & Warnings** — errors, warnings, top 3 warning *templates* (grounded, not guessed), actionable problems; semantic-only findings if any.
5. **Notable Categories** — per discovered category of interest, counts + representative templates and what they indicate.
6. **Performance Signals** — timing/throughput/slow-operation templates and counts (if the app produces any); semantic-only findings if any.
7. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
8. **Semantic Search Coverage** — mandatory (the semantic pass always runs), but report only meaningful findings — matches that template-classification missed or confirmed, with their queries; drop empty/no-hit queries. If nothing meaningful surfaced, one line saying so.
9. **Follow-up queries** — 2–3 concrete queries derived from templates.
10. **Query Log** — both results tables verbatim (baseline and plan; the chat does not show them), then every flagged query with a one-line note, and any query run beyond the plan with its result.
