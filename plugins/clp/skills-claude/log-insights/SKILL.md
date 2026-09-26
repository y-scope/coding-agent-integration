---
name: log-insights
description: App-agnostic log-shape-baseline log analysis with CLP. Dump the archive's log shape dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows; reports the archive's log shape count. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
allowed-tools:
  - "Agent"
  - "AskUserQuestion"
  - "Artifact"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-detect-logs:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cache:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-insights-bootstrap:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-insight-extract:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-query-plan-run:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-focus:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/log-shape-report-save:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/kql-build:*)"
  - "Bash(jq:*)"
  - "Bash(grep:*)"
  - "Bash(sort:*)"
  - "Bash(uniq:*)"
  - "Bash(head:*)"
  - "Bash(tail:*)"
  - "Bash(cat:*)"
  - "Bash(wc:*)"
  - "Bash(sed:*)"
  - "Bash(cut:*)"
  - "Bash(printf:*)"
  - "Bash(echo:*)"
---

# Log Insights (App-Agnostic, Log-Shape Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — re-running a subagent once when it returns unusable output at steps 6 and 9 — is part of the task and still applies.)

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — the baseline query pool running in the background from step 4, the single classification subagent at step 6 with the cache store running in the background after it, the plan pool running in the background from step 7 while the user picks a focus, and at step 9 the single report-writer subagent, each spawned only where it is called for — and fan the queries or templates out across more agents only if the user asks for it.

End-to-end analysis of **any** CLP archive using the **log shape baseline** method: dump the archive's log shape dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

The run stays interactive while the slow work happens in the background. While the classifier runs, ask the user what they already know about these logs. When it returns, summarize what it found, start the core queries in priority order, and ask what to focus on. The answer queues deeper queries ahead of the rest and tells the report writer what to lead with. While the writer works, ask where to save the report and in which formats. Nobody to ask (a headless run) → the whole picture, with no questions.

The classification is a property of the **application**, not the capture, so it is cached (keyed by a fingerprint of the template set, capped at a character limit — 500 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely. The cache is one SQLite database that stores templates by hash, never by text, so it stays small and fast even for apps whose templates are hundreds of KB each.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs first, use `compress-folder`.

## References — read on demand, not up front

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` — read at step 6 (classification subagent prompt, cluster/expand contract, merge and cache store commands).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-insight.md` — read at step 7 (the questions, summary, extract, query pool and focus commands, report-writer prompt, the save question and commands, report format).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-baseline.md` — read only if the dump/fallback misbehaves or when drilling into individual templates (stats.log_shapes encodings, CLP-string limitation, retrieve/count/analysis patterns, semantic-search flags).

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- Raw log files or folders — compress first, then point this skill at the resulting archive. Compression is the one app-specific step, and the `compress-folder` skill does it: `clp-detect-logs` shows what the first 128 KiB of each file holds (JSON structure and timestamp field, or text lines), you pick the flags from that report (`--timestamp-key <field>` for JSON, `--structurize` for vLLM text, a parser you write for other text), and `clp-s-compress-folder --path ...` compresses.
- If nothing was provided, ask for an archive, a log file, or a folder.

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

Each Bash call runs in its own shell, so shell variables do not persist between steps — re-declare them or run dependent commands together in one call.

Run anything that can take over a minute (the bootstrap on a large archive, the query pools, a subagent) in the background, so you can post the status line while it runs.

1. **Determine the input.** Archive path → use it. Log files or folders → detect, then compress, as the `compress-folder` skill describes (open phase 1 with one line: what the detector found and the flags you chose). Nothing → ask.

2. **Close phase 1 in one line** when you compressed: raw size → archive size, the ratio, the elapsed time, and the archives directory (`9.8 GiB → 357 MiB (28×) in 43 s; archive: <dir>`). This replaces the `compress-folder` skill's full stats list; give the full list only when asked. Input already an archive → phase 1 is one line saying so.

3. **Bootstrap.** Open phase 2 with one line: what the bootstrap does, in plain words, and its estimate. It reads the field names and value distributions from a sample of up to 20,000 records, gets the per-template counts stored in the archive, then checks the classification cache. It classifies nothing (that is step 6), and it doesn't sample the dictionary: every template is counted. The first time it sees an archive, it dumps the full log shape dictionary and stores each template's counts in the cache database, which takes about 1 minute per 300 MiB of archive (`Archive bytes` from step 2, or `du -sh <archive-dir>`): 1 s for a 1.6 MB vLLM archive, about 1 minute for a 357 MiB CockroachDB archive (9.8 GiB of raw logs). A later run on the same archive reads the stored counts instead, and takes about as long as the sample (15 s for that CockroachDB archive). Then run it:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-insights-bootstrap" <archive-dir>
   ```

   Its first line is its own estimate (`[bootstrap] archive 357.1 MB; expect about 2 min`, or `archive 357.1 MB, analyzed before; expect under a minute`). After that it prints a `[bootstrap]` line as each of its three stages starts and ends, and a heartbeat every 30 s while one runs. On an archive over 300 MiB, run it as a background Bash call (`run_in_background`, no trailing `&`). Follow its output with the Monitor tool (`tail -n +1 -F <output-file> | grep --line-buffered -E '^\[bootstrap\]|BOOTSTRAP_TIMINGS|rror'`, `timeout_ms` at its maximum; stop it with TaskStop when the harness reports the call exited), never with `sleep` between reads, which the harness blocks. Post only when a minute passes with no news, as one plain line (`still reading the vocabulary: 40 s of about 1 min`); never relay the raw `[bootstrap]` lines. It ends with `BOOTSTRAP_TIMINGS`; when the total is far from the estimate, say so in a line.

From its `KEY=VALUE` output record:
   - `FIELD path=... type=... records=N [values=V templates=K]` + `TEXT_FIELDS=` + `SCHEMA_TREE_FILE=` → every field of the whole archive, from its merged schema tree (no sampling): the KQL path, the type and the records that carry it; `shown=` marks array elements with `[]` (`message.content[].name` is queried as `message.content.name`). `ClpString` fields are the templated text; with `values`/`templates` the archive counts log shapes per field. The FIELD lines are the most common fields, then every text field; the full list is in `SCHEMA_TREE_FILE`.
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the text field with the most templates that is not ruled by field in step 5), payload leaves if any. The DIST fields are the most common scalar fields from the tree; their value vocabularies come from the sample, so a low-distinct field there is severity/logger-like.
   - `TEMPLATE_FIELDS_FILE=` → which fields each template's values came from (`{"hash","fields":{path:N}}`); step 5 uses it. `TEMPLATE_FIELDS=UNAVAILABLE` → the archive was compressed by a clp-s build that does not count log shapes per field; step 5 then clusters every template.
   - `LOG_SHAPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"hash":"...","length":N,"log_shape":"..."}` NDJSON, most frequent first, where `log_shape` is the template's first `MAX_CHARS` characters (all of it when `length` is no longer). Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `SHAPES_SOURCE=stored` → this archive was analyzed before, so its counts came from the cache database and the dictionary was not dumped; `LOG_SHAPES_FILE` (the full template text) is then not written. `SHAPES_SOURCE=dump` → the dictionary was dumped and the archive stored for next time.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-log-shape counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `ARCHIVE_LOG_SHAPES=` + `ARCHIVE_VARS=` → the archive's own exact counts, read in a fraction of a second before anything is dumped, so phase 2 can open with the real template count instead of an estimate from bytes. `UNAVAILABLE` on a `clp-s` too old to answer; then say nothing about it.
   - `RECORD_FAMILY_COUNT=` / `RECORD_FAMILY_RESIDUAL=` / `RECORD_FAMILIES_FILE=` / `RECORD_FAMILY_METHOD=` → the records split into kinds, each with an exact count and a KQL predicate. **This is a partition**: the families plus the residual account for every record, so these shares may be read as shares of the whole. `METHOD=value field=<f> coverage=<pct>` split on that field's values and is exact; `METHOD=existence` found no field universal enough and split on field presence, which is approximate — say which you got whenever the residual is big enough to matter. The residual is records the partition does not name; report it rather than rounding it away.
   - `FIELD_COUNT_GROUPS=` / `FIELD_COUNTS_FILE=` → per-field record counts, where fields sharing a count are carried by the same records. **These overlap and must never be summed**: on one real archive the 43 lines total 725%. They tell you which fields travel together, nothing about shares of the whole. Use `RECORD_FAMILY_*` for that.
   - `TYPE_DRIFT_COUNT=` / `TYPE_DRIFT_FILE=` → field paths holding more than one type across records, most records first, each type named and counted. Treat drift on a field you are about to query as a hazard: a filter or projection written for one type silently misses the records carrying the other. On one archive `toolUseResult` is a `ClpString` on a failed tool call and an `Object` on a success, 116 against 4,525, so a query for either shape quietly answers about a subset. Mention drift to the user only where it changes a query or a finding.
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `log-shape-cluster` and `log-shape-cache` so their fingerprints match.

Close phase 2 in one or two lines: the template count and what it means ("16.5M records reduce to 11,558 message templates"), the record kinds ("the records are 14 kinds; the two biggest are attachments at 34.9% and assistant turns at 26.2%"), and the cache outcome in plain words (reused from an earlier run, N new templates to classify, or a first run for this app). Mention the schema only when the choice was not obvious, frequencies only when they are unavailable, the residual only when it is large enough to matter, and drift only where it will change a query; field names are for your queries, not for the user.

4. **Start the baseline queries, then branch on `CACHE_MODE`.** The baseline (the severity and logger breakdown, the fetch of any rare-severity records, and a scoped semantic scan) needs nothing but step 3's schema, so it runs now, in the background, while the templates are clustered and classified. Write its plan (a few seconds; it samples the archive), then start its pool as a background Bash call (`run_in_background`, no trailing `&`) and go on without waiting:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-baseline-plan" --archive <archive-dir> \
     --schema-json '{"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>"}'
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-query-plan-run" --retry-failed \
     --query-plan-file /tmp/log-shape-baseline-plan.txt \
     --results-file /tmp/log-shape-baseline-results.ndjson <archive-dir>
   ```

   Do not narrate the quick checks: their results go into the phase 4 summary. Then open phase 3 with the branch: UPTODATE → `[3/5] Classify: skipped, this app was classified on an earlier run`; GROWTH → `[3/5] Classifying the N new templates; the other M are already classified`; NEW → `[3/5] Classifying N templates, a first run for this app`.
   - **UPTODATE** — the cached plan was already fetched to `/tmp/log-shape-classification.json` (`CLASSIFICATION_FILE=`). Verify its `.schema` matches step 3's schema; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/log-shapes.ndjson`; with `SHAPES_SOURCE=stored`, first re-run the bootstrap with `--dump` to write it).
   - **GROWTH** — only the new templates in `/tmp/log-shapes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/log-shape-base-classification.json`. Continue to step 5.
   - **NEW** — first capture of this app; classify all of `/tmp/log-shapes-to-classify.ndjson`. Continue to step 5.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 500 by default), de-duplicates the results, and merges semantically similar templates so the classification subagent sees one representative per cluster instead of every template (in step 4's schema-mismatch case, pass `--input /tmp/log-shapes.ndjson` instead):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/log-shapes-to-classify.ndjson
   ```

   With field rules (below), add `--template-fields /tmp/log-shape-template-fields.ndjson --field-rules /tmp/log-shape-field-rules.json`.

   **Rule whole fields first** when the bootstrap printed `TEMPLATE_FIELDS_FILE=`, and let the ratio propose the rules rather than picking them by eye:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" fields \
     --template-fields /tmp/log-shape-template-fields.ndjson --freqs-file /tmp/log-shape-freqs.ndjson \
     --propose-rules /tmp/log-shape-field-rules.json
   ```

   It prints one line per text field, most templates first, with its values and its most frequent templates, and writes a proposed `field_rules` file. **A field whose template count approaches its value count is free text**: every value is its own template, so clustering it is meaningless and embedding it is waste. That is a mechanical test, not a judgement — a field at ratio 1.00 has nothing to cluster, while a field with 9,444 records and one template is boilerplate the application injects. The proposal takes any field over `--free-text-ratio` (0.8), plus any with more than `--free-text-templates` (500) above a lower floor, because a field of diff lines sits near 0.68 — plainly free text that merely shares common lines — and a pure ratio test misses it. Fields under `--free-text-min-templates` (5) are left alone: ruling one saves nothing to embed and still costs a category to review, and on data-used-as-keys it invents dozens of junk categories.

   On a real agent-log archive this takes **25,483 templates down to 879 left to embed with 39 rules**, where hand-picked rules left 8,278. So read the proposal, do not just accept it: each rule carries `proposed_by`, its ratio and both counts, the category name is mechanical (`free-text-message-content-thinking`) and meant to be renamed, and you may drop a rule whose templates you actually want told apart. Then pass it to `cluster` with both flags. A template whose values sit in ruled fields (at least 90% of them) takes the category of the ruled field holding most of them and is neither embedded nor shown to the classifier (`FIELD_RULED=N`); any other template is clustered. The classifier must rank every rule category (step 6), and `expand` refuses a rule whose category is missing from the taxonomy. The cache stores each template's category by hash, so a reused classification needs no rules; a grown archive's new templates are clustered.

Stdout prints a summary then one `{"id","count","representative"}` line per cluster — paste those lines into the classification prompt. Full memberships go to `/tmp/log-shape-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. Keep the reduction (`TEMPLATES=N` → `CLUSTERS=M`) for the classifier announcement in step 6.

6. **Classify (GROWTH/NEW only), and ask for context meanwhile.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` NOW — it has the full subagent prompt and the validate → expand → merge → store commands. In short: spawn ONE classification subagent (Agent tool, model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, severity/logger vocabularies, and the cluster lines from step 5 (plus base taxonomy/plan labels on GROWTH). The classification is cached per app, so its quality is paid for once and reused on every later run. It returns id-based `assignments` — never log shape text — a ranked taxonomy (each category's `priority` and `why`), and a query plan whose filters are structured `match` objects — never KQL strings — each entry carrying its `category`, `priority`, and `stage` (`core` runs every time; `drill` runs only when the user focuses on its category). `kql-build check-plan` rejects any entry without a valid `match` or ranking. Announce it in one line, naming the model (`Classifying 146 groups covering 11,558 templates with opus; the longest step, and the quick checks run meanwhile`).

   **Right after spawning it, ask the context question** (AskUserQuestion; the wording is in `log-shape-insight.md`, "Ask what the user already knows"): whether the user is chasing a known problem, has something specific to check, or is just exploring. The subagent and the baseline pool keep running while the user answers. Keep the answer for steps 8–9. Never pass it to the classifier: the classification is cached per app and reused for every later capture, while the answer is about this capture. When the classifier returns: `kql-build check-plan` (on GROWTH with `--categories-from /tmp/log-shape-base-classification.json`), `log-shape-cluster expand` (on GROWTH also with `--categories-from /tmp/log-shape-base-classification.json`) gives every member template its category, by hash, and `log-shape-cache merge` writes `/tmp/log-shape-classification.json`, which the insight pass reads at once. Storing it in the cache (`log-shape-cache put`) is for the next run, not this one: start it as a background Bash call and go on without waiting. When the background store finishes, mention in your next message that the classification is cached for later runs; it needs no message of its own.

   On UPTODATE there is no classifier to wait for, so the context question is asked together with the focus question at step 8.

7. **Summarize, and start the core plan.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-insight.md` NOW — it has the questions, the summary, the extract, query-pool, focus and facts commands, the report-writer prompt, the save question and commands, and the report format. Run `log-shape-insight-extract`: it writes the core plan high priority first, the drill entries apart, and empties the focus inbox. The baseline pool started in step 4; if it is still running, wait for it to exit first (mention the wait only if it passes 30 s), since two pools at once would each size themselves from the same free memory. Then start the core plan as ONE background Bash call (`run_in_background`, no trailing `&`) with `--inbox /tmp/log-shape-focus-inbox.ndjson`: the pool runs the core plan, takes the focus entries ahead of whatever has not started, and exits only once the focus is queued. Then open phase 4 with the summary (the reference has its shape), the one place the total and the category table appear: records and the severity split, the record kinds and their shares (these are a partition, so they may be read as shares of the whole — say the method and the residual when the partition is approximate), the category table with each category's priority, why the classifier ranked the top categories high, and, when the user gave context, which categories it points at. Record kinds and text categories are two different axes — a kind is what a record *is*, a category is what its text is *about* — so present them as two tables and never merge them into one.

8. **Ask for the focus, and queue it.** Ask in one AskUserQuestion (on UPTODATE, together with the context question): the categories the context points at (or else the whole picture) marked "(Recommended)", the other high-priority categories, and "Everything"; the automatic "Other" takes the user's own question. Each option's description says what choosing it runs: a category queues its deeper checks (say how many; with none, that you will write one to three from its templates); "Everything" queues nothing extra, because the standard checks already cover every category. Then run `log-shape-focus` ONCE, even for "Everything": it queues the chosen categories' drill entries, plus any entries you write from the user's question or context (`--entries-file`, `match` filters checked by `kql-build`), records the focus and the context verbatim, and closes the inbox so the pool can finish. When no one can answer (a headless run, or AskUserQuestion unavailable), run `log-shape-focus --everything` at once. Say in one line what was queued, or that nothing extra was, then stay quiet until the checks finish apart from the once-a-minute status line.

9. **Facts, the early numbers, and the report writer.** When the pool prints `PLAN_STATUS`, save both tables — the baseline's (`--print-table --results-file /tmp/log-shape-baseline-results.ndjson`) and the plan's (`--print-table`, focus entries marked) — for the report check and the report's Query Log, without pasting them into the chat. Close phase 4 in one or two lines: how many checks ran, and each that failed or matched nothing, with what it costs the report. Run `log-shape-insight-facts` (under a second): it computes every number of the report in code, with the user's focus and context in a section of their own at the top, into `/tmp/log-shape-insight-facts.md`. Post the early numbers — 3 to 5 lines quoted from the facts file, the focus first — so the wait for the writer is not dead time. Then spawn the writer (model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, taxonomy, the focus, the user's context, both results tables, and the paths `/tmp/log-shape-insight-facts.md` and `/tmp/log-shape-templates-by-category.txt`. Every query has run and every figure is computed, so it runs no searches and does no arithmetic: it puts the facts into words, leads with the focus, treats the user's context as a claim to check against the records, may quote no figure that is not in the facts file, and writes the report itself to `/tmp/log-shape-insight-report.md`. Before spawning, open phase 5 with one line naming the model and the time (`[5/5] Writing the report with opus (~2 min)`). Never read or tail a subagent's transcript.

   **Right after spawning the writer, ask where to save the report** (AskUserQuestion; the wording is in `log-shape-insight.md`, "Ask where to save the report"). Run `log-shape-report-save --list-formats` first (instant) so every format offered is one this machine can produce: PDF only when it prints a `PDF_ENGINE` path, and a claude.ai page only when the Artifact tool is in this session's tool list. The writer keeps working while the user answers; keep the answer for step 10. Nobody to ask → skip the question; the report stays at `/tmp/log-shape-insight-report.md`.

   Then check the report (the reference has the loop): `log-shape-report-check` flags figures mechanically. If it flags anything, the same writer rules on each flag and fixes the real ones in one round, then the script runs once more; any flag still listed that the writer did not justify goes to the user as "unverified" rather than being dropped silently. There is no verifier subagent.

10. **Save and present the report.** Once the check is done, save the report as the user chose with `log-shape-report-save` (the reference, "Save the report", has the commands): one run writes every chosen file format and never overwrites a file; for a claude.ai page it writes a finished page that you publish with the Artifact tool as-is. A format that fails (`PDF_ERROR=`) gets one line with the reason; the others are still saved. Then close with a short message: three to five findings, most important first, with inferences labelled; the caveats that change how to read them; and where the report is: each saved path, and the claude.ai link. Do not restate the report. Then offer at most three next steps, one line each, from these:
   - Drill deeper on a specific template/finding, or on another category's drill entries (patterns in `references/log-shape-baseline.md`).
   - Note that re-running on the same application skips classification (cache).
   - Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" <archives-dir> <out-dir>`.
