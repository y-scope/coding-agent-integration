---
name: logtype-insights
description: App-agnostic logtype-baseline log analysis with CLP. Dump the archive's logtype dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows; reports the archive's logtype count. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
allowed-tools:
  - "Agent"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-insights-bootstrap:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-cluster:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-insight-extract:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-query-plan-run:*)"
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

# Logtype Insights (App-Agnostic, Logtype-Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — the stronger-model fallback when a subagent returns unusable output at steps 6–7 — is part of the task and still applies.)

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — the single classification subagent at step 6, and at step 7 the single report-writer subagent followed by the single verifier subagent, each spawned only where it is called for — and fan the queries or templates out across more agents only if the user asks for it.

End-to-end analysis of **any** CLP archive using the **logtype baseline** method: dump the archive's logtype dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

The classification is a property of the **application**, not the capture, so it is cached (keyed by a fingerprint of the template set, capped at a character limit — 512 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs first, use `compress-folder`.

## References — read on demand, not up front

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/logtype-classify.md` — read at step 6 (classification subagent prompt, cluster/expand contract, cache store commands).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/logtype-insight.md` — read at step 7 (extract, baseline-planner and query-pool commands, report-writer prompt, report format).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/logtype-baseline.md` — read only if the dump/fallback misbehaves or when drilling into individual templates (stats.log_shapes encodings, CLP-string limitation, retrieve/count/analysis patterns, semantic-search flags).

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- A folder of raw logs — compress first (compression is the one app-specific step), then point this skill at the resulting archive:
  - vLLM wrapper text logs: `--structurize`.
  - MongoDB JSON: `--extensions '*' --timestamp-key t.$date` (native).
  - Generic JSON with a known timestamp field: `--timestamp-key <field>`.
- If nothing was provided, ask for an archive or folder path.

## Workflow

Each Bash call runs in its own shell, so shell variables do not persist between steps — re-declare them or run dependent commands together in one call.

**Keep the user posted at every step.** Before each command or subagent, say in one short line what you are about to do; after it, report the key numbers it produced. Never chain steps silently — steps 5–7 run long, and without your narration the user sees only a spinner. Say the expected duration when you announce a command that can run over a minute (the bootstrap on a multi-GB archive, the query pool, a subagent); run such commands in the background and post a one-line status at least once a minute until they finish, so a slow step is never indistinguishable from a stuck one. When a cache hit or recorded results let you skip steps or plan entries, say which ones and why before skipping them, not afterward.

1. **Determine the input.** Archive path → use it. Folder → compress with the app-appropriate settings above (ask the user if the app is unknown). Nothing → ask.

2. **Report compression stats** when you compressed the folder: `Raw input bytes`, `Archive bytes`, `Compression ratio`, `File size reduction`, `Input files`, `Archives dir`, `Archive metadata`.

3. **Bootstrap.** Tell the user you are analyzing and classifying the log shape — then run the one command that does all of it:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/logtype-insights-bootstrap" <archive-dir>
   ```

From its `KEY=VALUE` output record:
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the clp-string field — high distinct-count prose), payload leaves if any. Low-distinct fields are severity/logger-like; note their value vocabularies from the DIST lines.
   - `LOGTYPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"logtype":"..."}` NDJSON, most frequent first. Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-logtype counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `logtype-cluster` and `logtype-cache` so their fingerprints match.

Then tell the user what the bootstrap found, in 2–3 lines: the logtype count, the schema you picked, whether per-template frequencies are available, and the cache mode.

4. **Branch on `CACHE_MODE`** — and announce the branch to the user: UPTODATE → "cached classification found; skipping straight to the insight pass"; GROWTH → "N of M templates are new; classifying only those"; NEW → "first capture of this app; classifying all N templates".
   - **UPTODATE** — the cached plan was already fetched to `/tmp/logtype-classification.json` (`CLASSIFICATION_FILE=`). Verify its `.schema` matches step 3's schema; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/logtypes.ndjson`).
   - **GROWTH** — only the new templates in `/tmp/logtypes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/logtype-base-classification.json`. Continue to step 5.
   - **NEW** — first capture of this app; classify all of `/tmp/logtypes-to-classify.ndjson`. Continue to step 5.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 512 by default), de-duplicates the results, and merges semantically similar templates so the classification subagent sees one representative per cluster instead of every template (in step 4's schema-mismatch case, pass `--input /tmp/logtypes.ndjson` instead):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cluster" cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/logtypes-to-classify.ndjson
   ```

Stdout prints a summary then one `{"id","count","representative"}` line per cluster — paste those lines into the classification prompt. Full memberships go to `/tmp/logtype-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. If the user then asks you to continue without clustering, use the raw-NDJSON last resort in `references/logtype-classify.md`. Report the reduction to the user (`TEMPLATES=N` → `EMBEDDED=K` → `CLUSTERS=M`).

6. **Classify (GROWTH/NEW only).** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/logtype-classify.md` NOW — it has the full subagent prompt and the validate → expand → store commands. In short: spawn ONE classification subagent (Agent tool, model **haiku**; fall back to `sonnet` if the output fails validation, telling the user before retrying) with the schema, severity/logger vocabularies, and the cluster lines from step 5 (plus base taxonomy/plan labels on GROWTH). It returns id-based `assignments` — never logtype text — and a query plan whose filters are structured `match` objects — never KQL strings; `kql-build check-plan` rejects any entry without a valid `match` before anything is stored. Then `logtype-cluster expand` propagates categories to every member verbatim and `logtype-cache put-merged` stores the result. Announce the subagent before spawning it ("classifying M representatives covering N templates with a fast model — the longest step"), and when it returns, report the taxonomy it produced and that the classification is now cached.

7. **Insight pass.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/logtype-insight.md` NOW — it has the extract, plan-repair, baseline-planner, query-pool and facts commands, the report-writer prompt, and the report format. If the extract reports `QUERY_PLAN_INVALID=` above zero (a plan cached before plans used `match` filters), repair those entries first, as the reference describes: one haiku subagent rewrites them as `match`, `kql-build check-plan` validates them, and `logtype-cache set-plan` stores them, so the repair happens once per app. Then two parts, in order:
   1. **Add the baseline queries, then run the query pool yourself, with progress.** Run `logtype-baseline-plan` once (it samples the archive for a few seconds and appends the severity/logger breakdown, the fetch of any rare-severity records, and a scoped semantic scan to the plan). Announce the pool ("executing the K planned queries plus the baseline; the runner sizes how many run at once from free memory"). Run `logtype-query-plan-run` once over the whole plan, as a background Bash call (`run_in_background`, no trailing `&`), and read its output file about every 30 seconds. It renders each entry's `match` to KQL (values quoted, groups parenthesized) and records that KQL, the result, status (`ok` / `zero` / `error` / `timeout`, plus a non-selective flag), and elapsed time; an entry's `then` rule can add a follow-up query to the pool once its result is in. As entries finish, post one line per entry to the user. When the pool is done, show the user the `--print-table` output verbatim and call out the entries that failed, matched nothing, or matched nearly everything.
   2. **Compute the facts, then spawn the report writer.** Run `logtype-insight-facts` (under a second): it computes every number of the report in code (totals, severity and logger breakdowns, the category table with its sum and gap, top templates, the fetched warnings and errors grouped by message shape) into `/tmp/logtype-insight-facts.md`. Then spawn the writer (model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, taxonomy, the results table, and the paths `/tmp/logtype-insight-facts.md` and `/tmp/logtype-templates-by-category.txt`. Every query has run and every figure is computed, so it runs no searches and does no arithmetic: it puts the facts into words, may quote no figure that is not in the facts file, and writes the report itself to `/tmp/logtype-insight-report.md`. Announce it before spawning, naming the model ("facts computed; the report writer turns them into the report — about two minutes"). Never read or tail a subagent's transcript. Then check the report (the reference has the loop): `logtype-report-check` flags figures mechanically, and ONE verifier subagent (model **haiku**) checks every claim against the facts and rules on the script's flags, writing `/tmp/logtype-report-issues.json`. If either finds anything, the same writer fixes the issues in one round, then both checks run once more; any issue still listed goes to the user as "unverified" rather than being dropped silently.

8. **Present the report.** Offer to:
   - Drill deeper on a specific template/finding (patterns in `references/logtype-baseline.md`).
   - Note that re-running on the same application skips classification (cache).
   - Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" <archives-dir> <out-dir>`.
