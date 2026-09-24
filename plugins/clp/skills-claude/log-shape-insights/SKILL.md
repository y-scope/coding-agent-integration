---
name: log-shape-insights
description: App-agnostic log-shape-baseline log analysis with CLP. Dump the archive's log shape dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows; reports the archive's log shape count. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
allowed-tools:
  - "Agent"
  - "AskUserQuestion"
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

# Log Shape Insights (App-Agnostic, Log-Shape Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — re-running a subagent once when it returns unusable output at steps 6 and 9 — is part of the task and still applies.)

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — the baseline query pool running in the background from step 4, the single classification subagent at step 6 with the cache store running in the background after it, the plan pool running in the background from step 7 while the user picks a focus, and at step 9 the single report-writer subagent, each spawned only where it is called for — and fan the queries or templates out across more agents only if the user asks for it.

End-to-end analysis of **any** CLP archive using the **log shape baseline** method: dump the archive's log shape dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

The run stays interactive while the slow work happens in the background. While the classifier runs, ask the user what they already know about these logs. When it returns, summarize what it found, start the core queries in priority order, and ask what to focus on. The answer queues deeper queries ahead of the rest and tells the report writer what to lead with. Nobody to ask (a headless run) → the whole picture, with no questions.

The classification is a property of the **application**, not the capture, so it is cached (keyed by a fingerprint of the template set, capped at a character limit — 500 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely. The cache is one SQLite database that stores templates by hash, never by text, so it stays small and fast even for apps whose templates are hundreds of KB each.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs first, use `compress-folder`.

## References — read on demand, not up front

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` — read at step 6 (classification subagent prompt, cluster/expand contract, merge and cache store commands).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-insight.md` — read at step 7 (the questions, summary, extract, query pool and focus commands, report-writer prompt, report format).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-baseline.md` — read only if the dump/fallback misbehaves or when drilling into individual templates (stats.log_shapes encodings, CLP-string limitation, retrieve/count/analysis patterns, semantic-search flags).

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- Raw log files or folders — compress first, then point this skill at the resulting archive. Compression is the one app-specific step, and the `compress-folder` skill does it: `clp-detect-logs` shows what the first 128 KiB of each file holds (JSON structure and timestamp field, or text lines), you pick the flags from that report (`--timestamp-key <field>` for JSON, `--structurize` for vLLM text, a parser you write for other text), and `clp-s-compress-folder --path ...` compresses.
- If nothing was provided, ask for an archive, a log file, or a folder.

## Workflow

Each Bash call runs in its own shell, so shell variables do not persist between steps — re-declare them or run dependent commands together in one call.

**Keep the user posted at every step.** Before each command or subagent, say in one short line what you are about to do; after it, report the key numbers it produced. Never chain steps silently — steps 5–9 run long, and without your narration the user sees only a spinner. Say the expected duration when you announce a command that can run over a minute (the bootstrap on a large archive, whose estimate step 3 gives; the query pool; a subagent); run such commands in the background and post a one-line status at least once a minute until they finish, so a slow step is never indistinguishable from a stuck one. When a cache hit or recorded results let you skip steps or plan entries, say which ones and why before skipping them, not afterward.

1. **Determine the input.** Archive path → use it. Log files or folders → detect, then compress, as the `compress-folder` skill describes (tell the user in a line what the detector found and which flags you chose). Nothing → ask.

2. **Report compression stats** when you compressed: `Raw input bytes`, `Archive bytes`, `Compression ratio`, `File size reduction`, `Input files`, `Archives dir`, `Archive metadata`.

3. **Bootstrap.** Tell the user in one line what the bootstrap does and how long it should take. It reads the field names and value distributions from a sample of up to 20,000 records, gets the per-template counts stored in the archive, then checks the classification cache. It classifies nothing (that is step 6), and it doesn't sample the dictionary: every template is counted. The first time it sees an archive, it dumps the full log shape dictionary and stores each template's counts in the cache database, which takes about 1 minute per 300 MiB of archive (`Archive bytes` from step 2, or `du -sh <archive-dir>`): 1 s for a 1.6 MB vLLM archive, about 1 minute for a 357 MiB CockroachDB archive (9.8 GiB of raw logs). A later run on the same archive reads the stored counts instead, and takes about as long as the sample (15 s for that CockroachDB archive). Then run it:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-insights-bootstrap" <archive-dir>
   ```

   Its first line is its own estimate (`[bootstrap] archive 357.1 MB; expect about 2 min`, or `archive 357.1 MB, analyzed before; expect under a minute`). After that it prints a `[bootstrap]` line as each of its three stages starts and ends, and a heartbeat every 30 s while one runs. On an archive over 300 MiB, run it as a background Bash call (`run_in_background`, no trailing `&`). Read its output file about every 30 s and relay the newest `[bootstrap]` line, so the user never waits on a silent dump. It ends with `BOOTSTRAP_TIMINGS`; when the total is far from the estimate, say so in a line.

From its `KEY=VALUE` output record:
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the clp-string field — high distinct-count prose), payload leaves if any. Low-distinct fields are severity/logger-like; note their value vocabularies from the DIST lines.
   - `LOG_SHAPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"hash":"...","length":N,"log_shape":"..."}` NDJSON, most frequent first, where `log_shape` is the template's first `MAX_CHARS` characters (all of it when `length` is no longer). Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `SHAPES_SOURCE=stored` → this archive was analyzed before, so its counts came from the cache database and the dictionary was not dumped; `LOG_SHAPES_FILE` (the full template text) is then not written. `SHAPES_SOURCE=dump` → the dictionary was dumped and the archive stored for next time.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-log-shape counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `log-shape-cluster` and `log-shape-cache` so their fingerprints match.

Then tell the user what the bootstrap found, in 2–3 lines: the log shape count, the schema you picked, whether per-template frequencies are available, and the cache mode.

4. **Start the baseline queries, then branch on `CACHE_MODE`.** The baseline (the severity and logger breakdown, the fetch of any rare-severity records, and a scoped semantic scan) needs nothing but step 3's schema, so it runs now, in the background, while the templates are clustered and classified. Write its plan (a few seconds; it samples the archive), then start its pool as a background Bash call (`run_in_background`, no trailing `&`) and go on without waiting:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-baseline-plan" --archive <archive-dir> \
     --schema-json '{"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>"}'
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-query-plan-run" --retry-failed \
     --query-plan-file /tmp/log-shape-baseline-plan.txt \
     --results-file /tmp/log-shape-baseline-results.ndjson <archive-dir>
   ```

   Report `BASELINE_ENTRIES=` and the sampled vocabulary, and post one line per baseline entry as it finishes, between the other steps. Then announce the branch: UPTODATE → "cached classification found; skipping straight to the insight pass"; GROWTH → "N of M templates are new; classifying only those"; NEW → "first capture of this app; classifying all N templates".
   - **UPTODATE** — the cached plan was already fetched to `/tmp/log-shape-classification.json` (`CLASSIFICATION_FILE=`). Verify its `.schema` matches step 3's schema; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/log-shapes.ndjson`; with `SHAPES_SOURCE=stored`, first re-run the bootstrap with `--dump` to write it).
   - **GROWTH** — only the new templates in `/tmp/log-shapes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/log-shape-base-classification.json`. Continue to step 5.
   - **NEW** — first capture of this app; classify all of `/tmp/log-shapes-to-classify.ndjson`. Continue to step 5.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 500 by default), de-duplicates the results, and merges semantically similar templates so the classification subagent sees one representative per cluster instead of every template (in step 4's schema-mismatch case, pass `--input /tmp/log-shapes.ndjson` instead):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/log-shape-cluster" cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/log-shapes-to-classify.ndjson
   ```

Stdout prints a summary then one `{"id","count","representative"}` line per cluster — paste those lines into the classification prompt. Full memberships go to `/tmp/log-shape-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. Report the reduction to the user (`TEMPLATES=N` → `EMBEDDED=K` → `CLUSTERS=M`).

6. **Classify (GROWTH/NEW only), and ask for context meanwhile.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` NOW — it has the full subagent prompt and the validate → expand → merge → store commands. In short: spawn ONE classification subagent (Agent tool, model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, severity/logger vocabularies, and the cluster lines from step 5 (plus base taxonomy/plan labels on GROWTH). The classification is cached per app, so its quality is paid for once and reused on every later run. It returns id-based `assignments` — never log shape text — a ranked taxonomy (each category's `priority` and `why`), and a query plan whose filters are structured `match` objects — never KQL strings — each entry carrying its `category`, `priority`, and `stage` (`core` runs every time; `drill` runs only when the user focuses on its category). `kql-build check-plan` rejects any entry without a valid `match` or ranking. Announce the subagent before spawning it, naming the model ("classifying M representatives covering N templates with opus — the longest step; the baseline queries run meanwhile").

   **Right after spawning it, ask the context question** (AskUserQuestion; the wording is in `log-shape-insight.md`, "Ask what the user already knows"): whether the user is chasing a known problem, has something specific to check, or is just exploring. The subagent and the baseline pool keep running while the user answers. Keep the answer for steps 8–9. Never pass it to the classifier: the classification is cached per app and reused for every later capture, while the answer is about this capture. When the classifier returns: `kql-build check-plan` (on GROWTH with `--categories-from /tmp/log-shape-base-classification.json`), `log-shape-cluster expand` gives every member template its category, by hash, and `log-shape-cache merge` writes `/tmp/log-shape-classification.json`, which the insight pass reads at once. Storing it in the cache (`log-shape-cache put`) is for the next run, not this one: start it as a background Bash call and go on without waiting. When the background store finishes, say in a line that the classification is cached.

   On UPTODATE there is no classifier to wait for, so the context question is asked together with the focus question at step 8.

7. **Summarize, and start the core plan.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-insight.md` NOW — it has the questions, the summary, the extract, query-pool, focus and facts commands, the report-writer prompt, and the report format. Run `log-shape-insight-extract`: it writes the core plan high priority first, the drill entries apart, and empties the focus inbox. The baseline pool started in step 4; if it is still running, wait for it to exit first (say so), since two pools at once would each size themselves from the same free memory. Then start the core plan as ONE background Bash call (`run_in_background`, no trailing `&`) with `--inbox /tmp/log-shape-focus-inbox.ndjson`: the pool runs the core plan, takes the focus entries ahead of whatever has not started, and exits only once the focus is queued. Then post the summary (the reference has its shape): records and the severity split, the category table with each category's priority, why the classifier ranked the top categories high, and, when the user gave context, which categories it points at.

8. **Ask for the focus, and queue it.** Ask in one AskUserQuestion (on UPTODATE, together with the context question): the categories the context points at (or else the whole picture) marked "(Recommended)", the other high-priority categories, and "Everything"; the automatic "Other" takes the user's own question. Then run `log-shape-focus` ONCE, even for "Everything": it queues the chosen categories' drill entries, plus any entries you write from the user's question or context (`--entries-file`, `match` filters checked by `kql-build`), records the focus and the context verbatim, and closes the inbox so the pool can finish. When no one can answer (a headless run, or AskUserQuestion unavailable), run `log-shape-focus --everything` at once. Say what was queued, then post one line per plan entry as it finishes, as the reference describes.

9. **Facts, the early numbers, and the report writer.** When the pool prints `PLAN_STATUS`, show the user both tables verbatim — the baseline's (`--print-table --results-file /tmp/log-shape-baseline-results.ndjson`) and the plan's (`--print-table`, focus entries marked) — and call out the entries that failed, matched nothing, or matched nearly everything. Run `log-shape-insight-facts` (under a second): it computes every number of the report in code, with the user's focus and context in a section of their own at the top, into `/tmp/log-shape-insight-facts.md`. Post the early numbers — 3 to 5 lines quoted from the facts file, the focus first — so the wait for the writer is not dead time. Then spawn the writer (model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, taxonomy, the focus, the user's context, both results tables, and the paths `/tmp/log-shape-insight-facts.md` and `/tmp/log-shape-templates-by-category.txt`. Every query has run and every figure is computed, so it runs no searches and does no arithmetic: it puts the facts into words, leads with the focus, treats the user's context as a claim to check against the records, may quote no figure that is not in the facts file, and writes the report itself to `/tmp/log-shape-insight-report.md`. Before spawning, kindly tell the user that this step takes a couple of minutes, because the writer is summarizing everything gathered so far into the report, and that the wait is expected, not a stall. Name the model ("All the facts are computed. The report writer (opus) is now summarizing everything gathered so far into the report; this usually takes a couple of minutes, so thanks for bearing with it."). Never read or tail a subagent's transcript. Then check the report (the reference has the loop): `log-shape-report-check` flags figures mechanically. If it flags anything, the same writer rules on each flag and fixes the real ones in one round, then the script runs once more; any flag still listed that the writer did not justify goes to the user as "unverified" rather than being dropped silently. There is no verifier subagent.

10. **Present the report.** Offer to:
   - Drill deeper on a specific template/finding, or on another category's drill entries (patterns in `references/log-shape-baseline.md`).
   - Note that re-running on the same application skips classification (cache).
   - Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" <archives-dir> <out-dir>`.
