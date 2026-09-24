---
name: log-insights
description: App-agnostic log-shape-baseline log analysis with CLP. Dump the archive's log shape dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
---

# Log Insights (App-Agnostic, Log-Shape Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — the stronger-model fallback when a subagent returns unusable output at steps 6–7 — is part of the task and still applies.)

End-to-end analysis of **any** CLP archive using the **log shape baseline** method: dump the archive's log shape dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

The classification is a property of the **application**, not the capture, so it is cached (keyed by `sha256` of the sorted template set, each template capped at a character limit — 500 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely. The cache is one SQLite database that stores templates by hash, never by text, so it stays small and fast even for apps whose templates are hundreds of KB each. The skill reports the archive's log shape count.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs first, use `compress-folder`.

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- Raw log files or folders — compress first (compression is the one app-specific step), as the `compress-folder` skill describes: `clp-detect-logs` shows what the first 128 KiB of each file holds (JSON structure and timestamp field, or text lines), you pick the flags from that report (`--timestamp-key <field>` for JSON, `--structurize` for vLLM text, a parser you write for other text), and `clp-s-compress-folder --path ...` compresses. Then point this skill at the archive.
- If nothing was provided, ask for an archive or folder path.

## Talking to the user

The user sees your messages, not the tools' output. Keep every message professional and short, and make each one tell the user something they did not know.

- **Five phases, numbered.** Every run has the same five: `[1/5] Compress`, `[2/5] Read the log vocabulary` (the bootstrap), `[3/5] Classify`, `[4/5] Run the checks`, `[5/5] Write the report`. Open each with one line: what it does and, when it can take over 30 s, how long. Close it with one line: what it found. A phase this run does not need gets one line saying why, before it is skipped (`[3/5] Classify: skipped, this app was classified on an earlier run`).
- **Lead with what was learned, not what ran.** "100 of 16.5M records are warnings or errors; fetching them all", not "Baseline #2: `NOT severity:"INFO"` → 100".
- **Progress only when it is news.** No line per stage, per query or per heartbeat. On a step that runs over a minute, post one status line each time a minute passes without news (`12 of 20 checks done`), so a slow step never looks stuck.
- **Keep the plumbing out.** Never mention monitors, output files, task IDs, background shells, or `KEY=VALUE` names. Report a failure when it changes the result, and say what it costs ("the OPS-channel check failed; its count still comes from the quick checks").
- **Use the user's words.** "Quick checks" (the baseline), "standard checks" (the core plan), "deeper checks" (drill entries), "matches almost everything" (non-selective), "reused from an earlier run" (UPTODATE). Query numbers such as "plan #4" belong in the report's Query Log only.
- **Each figure once.** The total record count and the category table appear once, in the phase 4 summary; later messages refer back to them instead of repeating them.
- **Ask only what changes the run,** and make every option's description literally true: say what choosing it queues, and when it queues nothing extra, say so.
- **No scripted pleasantries or apologies.** An estimate ("~2 min") already tells the user the wait is expected.

## Workflow

Each shell invocation is independent — shell variables do not persist between steps. Re-declare them or run dependent commands together in one call.

Run anything that can take over a minute (the bootstrap on a large archive, the query pools) in the background, so you can post the status line while it runs.

1. Determine the input: archive path → use it; log files or folders → detect, then compress, as the `compress-folder` skill describes (open phase 1 with one line: what the detector found and the flags you chose); nothing → ask.

2. Close phase 1 in one line when you compressed: raw size → archive size, the ratio, the elapsed time, and the archives directory (`9.8 GiB → 357 MiB (28×) in 43 s; archive: <dir>`). This replaces the `compress-folder` skill's full stats list; give the full list only when asked. Input already an archive → phase 1 is one line saying so.

3. **Bootstrap.** Open phase 2 with one line: what the bootstrap does, in plain words, and its estimate. It reads the field names and value distributions from a sample of up to 20,000 records, gets the per-template counts stored in the archive, then checks the classification cache. It classifies nothing (that is step 6), and it doesn't sample the dictionary: every template is counted. The first time it sees an archive, it dumps the full log shape dictionary and stores each template's counts in the cache database, which takes about 1 minute per 300 MiB of archive (`Archive bytes` from step 2, or `du -sh <archive-dir>`): 1 s for a 1.6 MB vLLM archive, about 1 minute for a 357 MiB CockroachDB archive (9.8 GiB of raw logs). A later run on the same archive reads the stored counts instead, and takes about as long as the sample (15 s for that CockroachDB archive). Then run it:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/log-shape-insights-bootstrap <archive-dir>
   ```

   Its first line is its own estimate (`[bootstrap] archive 357.1 MB; expect about 2 min`, or `archive 357.1 MB, analyzed before; expect under a minute`). After that it prints a `[bootstrap]` line as each of its three stages starts and ends, and a heartbeat every 30 s while one runs. Post only when a minute passes with no news, as one plain line (`still reading the vocabulary: 40 s of about 1 min`); never relay the raw `[bootstrap]` lines. It ends with `BOOTSTRAP_TIMINGS`; when the total is far from the estimate, say so in a line.

From its `KEY=VALUE` output record:
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the clp-string field — high distinct-count prose), payload leaves if any. Low-distinct fields are severity/logger-like; note their value vocabularies from the DIST lines.
   - `LOG_SHAPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"hash":"...","length":N,"log_shape":"..."}` NDJSON, most frequent first, where `log_shape` is the template's first `MAX_CHARS` characters (all of it when `length` is no longer). Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `SHAPES_SOURCE=stored` → this archive was analyzed before, so its counts came from the cache database and the dictionary was not dumped; `LOG_SHAPES_FILE` (the full template text) is then not written. `SHAPES_SOURCE=dump` → the dictionary was dumped and the archive stored for next time.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-log-shape counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `log-shape-cluster` and `log-shape-cache` so their fingerprints match.

Close phase 2 in one or two lines: the template count and what it means ("16.5M records reduce to 11,558 message templates"), and the cache outcome in plain words (reused from an earlier run, N new templates to classify, or a first run for this app). Mention the schema only when the choice was not obvious, and frequencies only when they are unavailable; field names are for your queries, not for the user.

4. **Branch on `CACHE_MODE`**, and open phase 3 with the branch: UPTODATE → `[3/5] Classify: skipped, this app was classified on an earlier run`; GROWTH → `[3/5] Classifying the N new templates; the other M are already classified`; NEW → `[3/5] Classifying N templates, a first run for this app`.
   - **UPTODATE** — the cached plan was already fetched to `/tmp/log-shape-classification.json`. Verify its `.schema` matches step 3; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/log-shapes.ndjson`; with `SHAPES_SOURCE=stored`, first re-run the bootstrap with `--dump` to write it).
   - **GROWTH** — only the new templates in `/tmp/log-shapes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/log-shape-base-classification.json`. Continue to step 5.
   - **NEW** — classify all of `/tmp/log-shapes-to-classify.ndjson`. Continue.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 500 by default), de-duplicates the results, and merges semantically similar templates so you classify one representative per cluster, not every template (in step 4's schema-mismatch case, pass `--input /tmp/log-shapes.ndjson` instead):

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/log-shape-cluster cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/log-shapes-to-classify.ndjson
   ```

Stdout prints a summary then one `{"id","count","representative"}` line per cluster (ids `c1..cN`, largest first). Full memberships go to `/tmp/log-shape-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. Keep the reduction (`TEMPLATES=N` → `CLUSTERS=M`) for the announcement in step 6.

6. **Classify the clusters (GROWTH / NEW only) — inline, ids only.** Announce it in one line before you start (`Classifying 146 groups covering 11,558 templates; the longest step`). Assign EACH cluster id (judging by its representative) the best-fitting category. Use this GENERIC default taxonomy, AND for GROWTH the existing base categories (reuse where one fits; add new only if none fits), AND for NEW any APP-SPECIFIC categories the representatives suggest (e.g. Mongo: workload/operations, replication/election, sharding, indexing, storage; vLLM: worker-health, kv-cache, model-loading). Generic defaults:

   - errors / exceptions / failures
   - warnings
   - performance (latency / throughput / timing)
   - config / startup / initialization
   - network / connectivity / timeout
   - resource (memory / disk / file-descriptors / storage pressure)
   - lifecycle / state-transitions (start/stop/election/stepdown/restart)
   - security / auth / access
   - other (note but don't deep-search)

Build a QUERY PLAN: targeted queries derived from the representatives, expressed in the discovered field names. Per entry: `label`, the filter as a structured `match` object, the `project` columns, and the `method`. Never write a KQL string: the plan runner renders `match` to KQL itself, quoting and escaping every value and parenthesizing every group, and an entry carrying a `kql` key is rejected. `match` grammar (nest freely): `{"all":[F,...]}` (AND), `{"any":[F,...]}` (OR), `{"not":F}`, `{"field":"<f>","eq":V}` (exact value — fastest, for a scalar field whose full value is known; on the message field it matches only a message equal to V, so it correctly returns 0 otherwise), `{"field":"<f>","contains":"text"}` (substring — what message content almost always needs; the text is literal, spaces and quotes included), `{"field":"<f>","contains":["a","b"]}` (substrings in order, e.g. a template's static fragments around its `<*>`), `{"field":"<f>","prefix":"text"}`, `{"field":"<f>","exists":true}`, `{"field":"<f>","gt":N}` (also `gte`/`lt`/`lte`), and `{"semantic":"text"}` (only inside an `all` beside a concrete filter; never alone, never under `not`). Methods: `count` (run with `--count`) / `project+grep` (fold the text into `match` as an `any` of `contains` nodes; set `grep` only for real regex features) / `project+jq` (with `jq`) / `semantic`. For GROWTH, add entries only for genuinely new signals.

Then RANK what you found, by what matters for the application rather than for this one capture (the classification is cached and reused for every later capture). Give every taxonomy category a `priority` — `high` (problems, or what tells whether the application is healthy and doing its job: errors, failures, request outcomes, latency; at most a handful), `medium` (useful context), or `low` (routine or uninformative) — and a one-line `why`: what a reader learns from it. Give every query_plan entry its `category` (a taxonomy category), a `priority` on the same scale, and a `stage`: `core` entries run on every analysis (the overview: counts and the key probes); `drill` entries run only when the user focuses on their category — write 1–3 per high or medium category, the next question a reader would ask once that category matters (the records behind a count, a narrower failure signal, the slow or failed subset). Example (Mongo): `{"label":"Slow queries","match":{"field":"attr.durationMillis","exists":true},"project":"t.$date,attr.durationMillis,msg","jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}`. Example (vLLM): `{"label":"Memory or OOM warnings","match":{"all":[{"field":"level","eq":"WARNING"},{"any":[{"field":"message","contains":"memory"},{"field":"message","contains":"OOM"},{"field":"message","contains":"oom-killer"}]}]},"project":"timestamp,level,message","method":"project+grep"}`.

Write `/tmp/log-shape-class.json` with this shape — `assignments` must contain EVERY cluster id exactly once, with ONLY ids, never log shape text (members are re-attached mechanically); omit `schema` for GROWTH:
   ```
   {
     "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
     "taxonomy": [{"category":"<name>","description":"<one line>","priority":"<high|medium|low>","why":"<one line>"}],
     "assignments": [{"id":"c1","category":"<name>"}],
     "query_plan": [{"label":"...","match":{...},"project":"...","grep":"...","jq":"...","method":"...","category":"<name>","priority":"<high|medium|low>","stage":"<core|drill>"}]
   }
   ```

Then validate, expand ids to every member template (by hash, exact by construction), merge — GROWTH merges into the base entry, NEW passes through — and store. Use `MODE`/`APP_KEY`/`BASE_KEY`/`MAX_CHARS` from the bootstrap output (`--max-chars` must match the bootstrap's, or the stored fingerprint won't match the next run):
   ```bash
   BIN=~/.codex/marketplaces/yscope/plugins/clp/bin
   # Fields must be ARRAYS (a bare `.assignments` test passes for a scalar,
   # which would poison the cache entry):
   jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
     /tmp/log-shape-class.json >/dev/null || exit 1
   # Every query_plan entry needs a valid `match` filter and its ranking
   # (category, priority, stage), and every taxonomy entry its priority and
   # why: one "[i] OK <kql>" or "[i] ERROR <label>: <why>" line per entry,
   # "TAXONOMY ERROR" per unranked category, exit 1 on any error — fix them
   # and re-run; do NOT store in that case. On GROWTH add
   # --categories-from /tmp/log-shape-base-classification.json, since the new
   # entries may use the base's categories:
   "$BIN"/kql-build check-plan /tmp/log-shape-class.json || exit 1
   # Exits 2 and writes NOTHING on missing/unknown/duplicate ids — fix the
   # assignments and re-run; do NOT store in that case:
   "$BIN"/log-shape-cluster expand --clusters /tmp/log-shape-clusters.json \
     --classification /tmp/log-shape-class.json --output /tmp/log-shape-expanded.json
   if [[ "$MODE" == "GROWTH" ]]; then
     # Guard: an empty BASE_KEY would silently keep ONLY the new templates.
     [[ -n "$BASE_KEY" ]] || { echo "error: GROWTH with empty BASE_KEY" >&2; exit 1; }
     "$BIN"/log-shape-cache merge --base-key "$BASE_KEY" < /tmp/log-shape-expanded.json > /tmp/log-shape-classification.json
   else
     "$BIN"/log-shape-cache merge < /tmp/log-shape-expanded.json > /tmp/log-shape-classification.json
   fi
   # Store it for the next run (milliseconds; step 7 reads the file above):
   "$BIN"/log-shape-cache put --key "$APP_KEY" --max-chars "$MAX_CHARS" < /tmp/log-shape-classification.json
   ```

After storing, close phase 3 in one line: how many categories you found and that the classification is cached for later runs. The category table waits for the summary in step 7.

7. **Summarize, ask, and stop.** Extract the plan with the bounded extractor (a raw `jq` over the classification file can take minutes when an app logs large near-duplicate blobs). It writes the core plan (`/tmp/log-shape-query-plan.txt`, the `core` entries, high priority first), the drill entries (`/tmp/log-shape-drill-plan.txt`), `/tmp/log-shape-templates-by-category.txt` (the top templates per category by frequency) and `/tmp/log-shape-category-totals.json` (exact records per category, with priority and why), and empties the focus inbox:

   ```bash
   BIN=~/.codex/marketplaces/yscope/plugins/clp/bin
   "$BIN"/log-shape-insight-extract          # add --no-freqs when FREQS=UNAVAILABLE
   ```

   `QUERY_PLAN_INVALID=` is 0 for any classification `log-shape-cache` produced; if it is not, report it and stop. Then open phase 4 with a short summary, the one place the category table appears — the log shape count, the categories as a small table (records, templates, priority, from the extract's `CATEGORY` lines), the `why` of each high-priority category, and the severity split as the bootstrap's DIST lines sampled it — and ask three questions in the same message (run `"$BIN"/log-shape-report-save --list-formats` first, so the third offers only formats this machine can produce: PDF only when it prints a `PDF_ENGINE` path):

   - **What do you already know about these logs?** Chasing a problem (what: a symptom, a time, a component), checking something specific, or just exploring.
   - **What should the report focus on?** Offer the high-priority categories by name, each with its record count and how many deeper checks choosing it queues (the extract's `drill=` count); "everything", described truthfully as the standard checks alone, which already cover every category, with no extra queries; or their own question.
   - **Where should I save the report, and as what?** Formats, one or more: HTML (a styled page for any browser; recommend it), Markdown, and PDF when available. Location, with the real paths: this directory (`<cwd>/log-insights-<name>-<YYYYmmdd-HHMM>.<ext>`, recommended), next to the logs (their directory, or the archive's parent when the input was an archive), `/tmp`, or a folder or file name of their own (its `.md`/`.html`/`.pdf` extension is replaced by each format's own). `<name>` is the source log file or folder's name when this run compressed it, else the archive directory's name.

   Then **end your turn and wait for the answer.** The questions come after classification because the focus options are its categories; asking once keeps it to one stop. In a non-interactive run (`codex exec`, or when told not to ask), skip the questions and treat the answer as "everything, no context", with the report left at `/tmp/log-shape-insight-report.md`.

8. **Queue the focus, then run the queries.** Run `log-shape-focus` ONCE with the answer — even for "everything", since it closes the inbox the pool reads:

   ```bash
   "$BIN"/log-shape-focus --category <C> [--category <C2>] [--entries-file /tmp/log-shape-focus-entries.ndjson] \
     --context '<what the user said they know, verbatim, or empty>' --question '<their own question, or empty>'
   "$BIN"/log-shape-focus --everything --context '<...>'           # the whole picture
   ```

   A category queues its drill entries; `NO_DRILL=<C>` means it has none. For the user's own question, a category with no drill entries, or context that names something specific (a component, a symptom, an error text), write 1–3 entries to `/tmp/log-shape-focus-entries.ndjson`, one per line, shaped like plan entries (`label`, `match`, `method`, `project` for projecting methods, `category` when one fits), derived from templates in `/tmp/log-shape-templates-by-category.txt`; `log-shape-focus` validates them and queues nothing if one is invalid (fix it and re-run). A time the user mentions cannot be a filter (`match` has no time range); keep it for the report. Never fold the answer into the classification: it is cached per app, and the answer is about this capture. Then run the baseline and the plan — the plan's pool takes the focus entries from the inbox ahead of the core plan, so they run first:

   ```bash
   # The severity/logger baseline, as its own plan and pool (samples the archive
   # for a few seconds; SCHEMA= is the extract's line):
   "$BIN"/log-shape-baseline-plan --archive <archive-dir> --schema-json '<SCHEMA= line>'
   "$BIN"/log-shape-query-plan-run --retry-failed --query-plan-file /tmp/log-shape-baseline-plan.txt \
     --results-file /tmp/log-shape-baseline-results.ndjson <archive-dir>
   # Then the core plan, with the focus from the inbox first:
   "$BIN"/log-shape-query-plan-run --retry-failed --inbox /tmp/log-shape-focus-inbox.ndjson <archive-dir>
   # Both tables, each numbered from 1 (cite "baseline #N" or "plan #N"; focus entries marked):
   "$BIN"/log-shape-query-plan-run --print-table --results-file /tmp/log-shape-baseline-results.ndjson | tee /tmp/log-shape-baseline-table.md
   "$BIN"/log-shape-query-plan-run --print-table | tee /tmp/log-shape-plan-table.md
   ```

   The runner renders each entry's `match` to KQL (values quoted, groups parenthesized) and records that KQL, the result, status (`ok` / `zero` / `error` / `timeout`, plus a `non_selective` flag at 90% or more of the records), elapsed time, and a few samples in its results file (`/tmp/log-shape-baseline-results.ndjson` for the baseline, `/tmp/log-shape-query-results.ndjson` for the plan); the run also records the archive's total record count. Do not give a line per entry; post one status line each time a minute passes without news (`12 of 20 checks done`). The baseline entries (`origin: "baseline"`) give the severity and logger breakdown; when a rare-severity residual is small, a follow-up entry fetches those records, and its `samples` are the errors and warnings themselves. `/tmp/log-shape-category-totals.json` holds the exact records per category, so report those instead of a keyword probe's count. Then run `"$BIN"/log-shape-insight-facts --schema-json '<SCHEMA= line>' --freqs-file <FREQS_FILE>` (it reads both results files and `/tmp/log-shape-focus.json`) (add `--freqs-file none --category-totals none` when frequencies are unavailable): it writes `/tmp/log-shape-insight-facts.md` with every number of the report computed in code, the user's focus and context first, so quote figures from that file and never add up or derive your own. Before presenting the report, save it to `/tmp/log-shape-insight-report.md` and run `"$BIN"/log-shape-report-check /tmp/log-shape-insight-report.md --also /tmp/log-shape-baseline-table.md --also /tmp/log-shape-plan-table.md`; fix or remove any figure it flags. When the pools are done, close phase 4 in one or two lines: how many checks ran, and each that failed or matched nothing, with what it costs the report. Keep both tables for the report's Query Log instead of pasting them into the chat. Do not re-run plan entries; for an `error` or `timeout` entry, run ONE corrected query (e.g. quote a wildcard value that contains spaces, `<message>:"*a b*"`) and log it in the Query Log. Then give 3–5 lines of early numbers from the facts file, the focus first, and open phase 5 (`[5/5] Writing the report`) before step 9. For the queries you run yourself, pick the method that fits:
   - `count`: run the KQL with `--count` (in-engine; cannot be combined with `--projection`), never `--projection ... | grep -c '^{'`. It prints one `{"archive_id":...,"count":N}` line per archive and nothing when zero records match; treat empty output as a real zero.
   - `project+grep`: fold the target into the KQL as `<message>:"*text*"`, and OR the wildcards for a keyword alternation (`<message>:"*a*" OR <message>:"*b*"`). Only when the target needs real regex features (anchors, character classes, backreferences), run the KQL with `--projection`, then `grep '^{' | jq -r '.<message>' | grep -Ei '<grep>'`. Add `--limit N` when a few example records are enough.
   - `project+jq`: run the KQL with `--projection`, then `grep '^{' | jq -r '<jq>'`.
   - `semantic`: run `semantic("...") AND <kql>` with `--projection`.

MANDATORY semantic pass — in addition to any query_plan entries whose method is `semantic`, always run at least one scoped `semantic()` query derived from the goal or the dominant templates, e.g. `semantic("...") AND <severity>:<value>` or `semantic("...") AND <logger>:"*<substr>*"`. Never run an unscoped `semantic()`. Discard any query that returns nothing or only generic/meaningless log shapes — do not include it in the report.

Then:
   - **Per-template frequencies** (the count baseline): read the bootstrap's `FREQS_FILE`, already sorted most frequent first — `head -20 /tmp/log-shape-freqs.ndjson`. Never recompute them by projecting and counting messages. With `FREQS=UNAVAILABLE`, say so in the Log Shape Baseline section and omit counts.
   - **Total records**: `total_records` in `/tmp/log-shape-query-results.ndjson` (already counted; do not recount). **Severity/logger breakdowns**: the bootstrap DIST lines cover only the sampled records; for exact totals run `--count` per value, including the dominant one (it costs the same as a rare one). List unknown values first with `--unique <field>` (it still scans the matching records). **Group totals**: sum `count` over the matching templates in `/tmp/log-shape-freqs.ndjson` instead of scanning records. **Time span**: `timeRange` in the archive's `.yscope-clp-archive.json` (`begin`/`end`, the earliest and latest timestamp across every record, recorded at compression). When it is absent or null, say the span is unavailable and why (no `--timestamp-key`, no record has the key, or compressed before time ranges were recorded; recompress to get one); never estimate it from fetched records.
   - `<message>:term` is an exact match, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so use it when you know a field's full value; message content is free text and almost always needs a substring wildcard — `<message>:"*term*"`. Combine with a scalar filter in one compound query when you can (`<severity>:<value> AND <message>:"*term*"`, `<logger>:"*<substr>*" AND <message>:"*term*"`). Fall back to projecting message + grep only when the match needs real regex features, never for a plain keyword alternation.

9. Present a Markdown Log Insights Report, leading with the focus. The user's context is their account, not a finding: say whether the records support it, contradict it, or say nothing about it, quoting the lines that decide it.
   1. **Summary** — total records, severity counts, time span, top logger/component.
   2. **Focus** — what the user asked for, answered first: the focus categories' records and templates, the focus queries' results, and whether the records bear out the user's context.
   3. **Log Shape Baseline** — distinct template count, top N templates by frequency, the discovered category breakdown. The spine of the report.
   4. **Issues & Warnings** — error/warning counts, top 3 warning *templates* (grounded, not guessed), actionable problems; semantic-only findings if any.
   5. **Notable Categories** — per category of interest, counts + representative templates and what they indicate.
   6. **Performance Signals** — timing/throughput/slow-operation templates and counts (if any); semantic-only findings if any.
   7. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
   8. **Semantic Search Coverage** — mandatory (the semantic pass always runs), but report only meaningful findings — matches that classification missed or confirmed, with their queries; drop empty/no-hit queries. If nothing meaningful surfaced, one line saying so.
   9. **Follow-up queries** — 2–3 concrete queries derived from templates.
   10. **Query Log** — both results tables verbatim (baseline and plan; the chat does not show them), then every query you ran beyond the plan (exact KQL + flags), its result, and whether the report uses it, including empty ones and corrected re-runs of failed plan entries.

10. Save the report as the user chose, every format in one run: `"$BIN"/log-shape-report-save --format html,pdf --dest <folder-or-file> --name <name>`. It prints `SAVED_<FORMAT>=<path>` per file, never overwrites a file (it adds `-2`, `-3`, ...), and creates missing folders. `PDF_ERROR=` means that one format failed: say why in one line and keep the other files; never install a browser to get PDF. Then close with a short message: three to five findings, most important first, with inferences labelled; the caveats that change how to read them; and where the report is: each saved path. Do not restate the report. Then offer at most three next steps, one line each: drill deeper on a finding or another category's drill entries, note that re-running on the same application skips classification (cached plan reused), or decompress: `~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress <archives-dir> <out-dir>`.

## The message field needs the same wildcard rule as any field

The message field (`message` structurized, `msg` native Mongo, …) is stored as a CLP-string (log shape + encoded variables — what makes `stats.log_shapes` and the compression work). That storage is irrelevant to searching it: `<message>:term` is an exact match, same as `<field>:term` on any field, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so prefer it whenever you know the full field value; wildcard only for a substring match — `<message>:"*term*"` — which is what message content almost always needs, since it's free text. Prefer a direct wildcard search on the message field over project+grep:

```bash
S=~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql
"$S" --projection <timestamp>,<severity>,<message> <archive-dir> \
  '<severity>:WARNING AND <message>:"*StaticText*"'
```

Fall back to projecting the message field and grepping/jq-filtering only when the distinctive text needs a regex the wildcard syntax can't express:

```bash
"$S" --projection <timestamp>,<severity>,<message> <archive-dir> '<severity>:WARNING' \
  | grep '^{' | jq -rc 'select(.<message>|test("StaticText";"i"))'
```

Semantic search (`semantic("…")`) also reads the log shapes directly and is a good complement to wildcard search for concept-shaped questions. The insight pass (step 8) always runs one mandatory scoped semantic cross-check; beyond that, use it only for an ambiguous template, grouping similar templates, or a conceptual user question — always scoped: `semantic("…") AND <severity>:<value>`. Flags: `--semantic-top-k` (default 5) and `--semantic-threshold` (default 0.3; raise for precision).

## Classification cache notes

- `app_key = sha256(sorted set of distinct log shape strings, each capped at `MAX_CHARS` characters)` — the fingerprint of the *embedded* vocabulary, since the same limit is applied before embedding; the cache is `cache.sqlite` in `~/.config/yscope-clp-plugin/log-shape-cache/` (`$CLP_LOG_SHAPE_CACHE_DIR` or `--cache-dir` to override). Entries store `schema`, `taxonomy`, `query_plan`, `max_chars`, `classified_at`, `grown_from` lineage, and per template its `hash` (full text), `prefix_hash` (first `MAX_CHARS` characters) and `category` — never the text, which stays in the archive's dictionary dump and which `log-shape-insight-extract` joins on the hash.
- `diff` modes: **UPTODATE** (reuse, no classifying — but verify the cached schema; a template differing from a cached one only past the character limit takes its category through the shared prefix hash), **GROWTH** (classify only the new templates; `merge` unions them into the base entry), **NEW** (classify all). The bootstrap runs `diff` for you and fetches the relevant entries. An entry stored before classifications were ranked (no `priority`) is never reused: `diff` warns and reports NEW or GROWTH as if it were absent, and `put` replaces it. If `diff` warns that entries are in the old JSON format, tell the user they are ignored and can be deleted.
- GROWTH matching compares hashes of the full templates; the subset test behind it uses the prefix hashes. Both are exact when you go through `log-shape-cluster expand`, which hashes the members straight from the cluster file.
- Inspect: `log-shape-cache list` (shows lineage), `log-shape-cache show <APP_KEY>`.
