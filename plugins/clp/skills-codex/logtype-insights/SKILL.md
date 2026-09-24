---
name: logtype-insights
description: App-agnostic logtype-baseline log analysis with CLP. Dump the archive's logtype dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
---

# Logtype Insights (App-Agnostic, Logtype-Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — the stronger-model fallback when a subagent returns unusable output at steps 6–7 — is part of the task and still applies.)

End-to-end analysis of **any** CLP archive using the **logtype baseline** method: dump the archive's logtype dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

The classification is a property of the **application**, not the capture, so it is cached (keyed by `sha256` of the sorted template set, each template capped at a character limit — 500 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely. The skill reports the archive's logtype count.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs first, use `compress-folder`.

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- Raw log files or folders — compress first (compression is the one app-specific step), as the `compress-folder` skill describes: `clp-detect-logs` shows what the first 128 KiB of each file holds (JSON structure and timestamp field, or text lines), you pick the flags from that report (`--timestamp-key <field>` for JSON, `--structurize` for vLLM text, a parser you write for other text), and `clp-s-compress-folder --path ...` compresses. Then point this skill at the archive.
- If nothing was provided, ask for an archive or folder path.

## Workflow

Each shell invocation is independent — shell variables do not persist between steps. Re-declare them or run dependent commands together in one call.

**Keep the user posted at every step.** Before each command, say in one short line what you are about to do; after it, report the key numbers it produced. Never chain steps silently — steps 5–7 run long, and without your narration the user sees no progress at all. Say the expected duration when you announce a command that can run over a minute (the bootstrap on a multi-GB archive, plan batches, a subagent); run such commands in the background and post a one-line status at least once a minute until they finish, so a slow step is never indistinguishable from a stuck one. When a cache hit or recorded results let you skip steps or plan entries, say which ones and why before skipping them, not afterward.

1. Determine the input: archive path → use it; log files or folders → detect, then compress, as the `compress-folder` skill describes (tell the user in a line what the detector found and which flags you chose); nothing → ask.

2. Report compression stats when you compressed: `Raw input bytes`, `Archive bytes`, `Compression ratio`, `File size reduction`, `Input files`, `Archives dir`, `Archive metadata`.

3. **Bootstrap.** Tell the user you are analyzing and classifying the log shape — then run the one command that does all of it (it also reads the per-template frequencies that clp-s stored in the archive):

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-insights-bootstrap <archive-dir>
   ```

From its `KEY=VALUE` output record:
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the clp-string field — high distinct-count prose), payload leaves if any. Low-distinct fields are severity/logger-like; note their value vocabularies from the DIST lines.
   - `LOGTYPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"logtype":"..."}` NDJSON, most frequent first. Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-logtype counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `logtype-cluster` and `logtype-cache` so their fingerprints match.

Then tell the user what the bootstrap found, in 2–3 lines: the logtype count, the schema you picked, whether per-template frequencies are available, and the cache mode.

4. **Branch on `CACHE_MODE`** — and announce the branch to the user: UPTODATE → "cached classification found; skipping straight to the insight pass"; GROWTH → "N of M templates are new; classifying only those"; NEW → "first capture of this app; classifying all N templates".
   - **UPTODATE** — the cached plan was already fetched to `/tmp/logtype-classification.json`. Verify its `.schema` matches step 3; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/logtypes.ndjson`).
   - **GROWTH** — only the new templates in `/tmp/logtypes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/logtype-base-classification.json`. Continue to step 5.
   - **NEW** — classify all of `/tmp/logtypes-to-classify.ndjson`. Continue.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 500 by default), de-duplicates the results, and merges semantically similar templates so you classify one representative per cluster, not every template (in step 4's schema-mismatch case, pass `--input /tmp/logtypes.ndjson` instead):

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-cluster cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/logtypes-to-classify.ndjson
   ```

Stdout prints a summary then one `{"id","count","representative"}` line per cluster (ids `c1..cN`, largest first). Full memberships go to `/tmp/logtype-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. If the user then asks you to continue without clustering, classify `/tmp/logtypes-to-classify.ndjson` directly using the OLD contract: a `templates` array with each logtype copied **byte-exact**, no `assignments`, no `expand` — pipe your JSON straight into `put-merged --max-chars "$MAX_CHARS"` (this replaces step 6's validate/expand block; after `put-merged`, run `logtype-cache get "$APP_KEY" > /tmp/logtype-classification.json` and continue at step 7). Report the reduction to the user (`TEMPLATES=N` → `EMBEDDED=K` → `CLUSTERS=M`).

6. **Classify the clusters (GROWTH / NEW only) — inline, ids only.** Tell the user you are classifying the M representatives (the longest step) before you start. Assign EACH cluster id (judging by its representative) the best-fitting category. Use this GENERIC default taxonomy, AND for GROWTH the existing base categories (reuse where one fits; add new only if none fits), AND for NEW any APP-SPECIFIC categories the representatives suggest (e.g. Mongo: workload/operations, replication/election, sharding, indexing, storage; vLLM: worker-health, kv-cache, model-loading). Generic defaults:

   - errors / exceptions / failures
   - warnings
   - performance (latency / throughput / timing)
   - config / startup / initialization
   - network / connectivity / timeout
   - resource (memory / disk / file-descriptors / storage pressure)
   - lifecycle / state-transitions (start/stop/election/stepdown/restart)
   - security / auth / access
   - other (note but don't deep-search)

Build a QUERY PLAN: targeted queries derived from the representatives, expressed in the discovered field names. Per entry: `label`, the filter as a structured `match` object, the `project` columns, and the `method`. Never write a KQL string: the plan runner renders `match` to KQL itself, quoting and escaping every value and parenthesizing every group, and an entry carrying a `kql` key is rejected. `match` grammar (nest freely): `{"all":[F,...]}` (AND), `{"any":[F,...]}` (OR), `{"not":F}`, `{"field":"<f>","eq":V}` (exact value — fastest, for a scalar field whose full value is known; on the message field it matches only a message equal to V, so it correctly returns 0 otherwise), `{"field":"<f>","contains":"text"}` (substring — what message content almost always needs; the text is literal, spaces and quotes included), `{"field":"<f>","contains":["a","b"]}` (substrings in order, e.g. a template's static fragments around its `<*>`), `{"field":"<f>","prefix":"text"}`, `{"field":"<f>","exists":true}`, `{"field":"<f>","gt":N}` (also `gte`/`lt`/`lte`), and `{"semantic":"text"}` (only inside an `all` beside a concrete filter; never alone, never under `not`). Methods: `count` (run with `--count`) / `project+grep` (fold the text into `match` as an `any` of `contains` nodes; set `grep` only for real regex features) / `project+jq` (with `jq`) / `semantic`. For GROWTH, add entries only for genuinely new signals. Example (Mongo): `{"label":"Slow queries","match":{"field":"attr.durationMillis","exists":true},"project":"t.$date,attr.durationMillis,msg","jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}`. Example (vLLM): `{"label":"Memory or OOM warnings","match":{"all":[{"field":"level","eq":"WARNING"},{"any":[{"field":"message","contains":"memory"},{"field":"message","contains":"OOM"},{"field":"message","contains":"oom-killer"}]}]},"project":"timestamp,level,message","method":"project+grep"}`.

Write `/tmp/logtype-class.json` with this shape — `assignments` must contain EVERY cluster id exactly once, with ONLY ids, never logtype text (members are re-attached mechanically); omit `schema` for GROWTH:
   ```
   {
     "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
     "taxonomy": [{"category":"<name>","description":"<one line>"}],
     "assignments": [{"id":"c1","category":"<name>"}],
     "query_plan": [{"label":"...","match":{...},"project":"...","grep":"...","jq":"...","method":"..."}]
   }
   ```

Then validate, expand ids to every member template (byte-exact by construction), and store — GROWTH merges into the base entry, NEW stores fresh. Use `MODE`/`APP_KEY`/`BASE_KEY`/`MAX_CHARS` from the bootstrap output (`--max-chars` must match the bootstrap's, or the stored fingerprint won't match the next run):
   ```bash
   BIN=~/.codex/marketplaces/yscope/plugins/clp/bin
   # Fields must be ARRAYS (a bare `.assignments` test passes for a scalar,
   # which would poison the cache entry):
   jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
     /tmp/logtype-class.json >/dev/null || exit 1
   # Every query_plan entry needs a valid `match` filter: one "[i] OK <kql>" or
   # "[i] ERROR <label>: <why>" line per entry, exit 1 on any ERROR — fix
   # those entries and re-run; do NOT store in that case:
   "$BIN"/kql-build check-plan /tmp/logtype-class.json || exit 1
   # Exits 2 and writes NOTHING on missing/unknown/duplicate ids — fix the
   # assignments and re-run; do NOT store in that case:
   "$BIN"/logtype-cluster expand --clusters /tmp/logtype-clusters.json \
     --classification /tmp/logtype-class.json --output /tmp/logtype-expanded.json
   if [[ "$MODE" == "GROWTH" ]]; then
     # Guard: an empty BASE_KEY would silently store ONLY the new templates.
     [[ -n "$BASE_KEY" ]] || { echo "error: GROWTH with empty BASE_KEY" >&2; exit 1; }
     "$BIN"/logtype-cache put-merged --max-chars "$MAX_CHARS" --base-key "$BASE_KEY" --key "$APP_KEY" < /tmp/logtype-expanded.json
   else
     "$BIN"/logtype-cache put-merged --max-chars "$MAX_CHARS" --key "$APP_KEY" < /tmp/logtype-expanded.json
   fi
   "$BIN"/logtype-cache get "$APP_KEY" > /tmp/logtype-classification.json   # full plan for step 7
   ```

After storing, report the taxonomy you produced and that the classification is now cached for future runs.

7. **Run the insight pass** (inline, from `/tmp/logtype-classification.json`). Announce it first ("running the insight pass — executing the K planned queries; the runner sizes how many run at once from free memory"). Extract the plan with the bounded extractor, which writes `/tmp/logtype-query-plan.txt` and `/tmp/logtype-templates-by-category.txt` (the top templates per category by frequency). A raw `jq` over the classification file can take minutes when an app logs large near-duplicate blobs. Then execute the whole plan with one plan-runner command; it decides how many searches run at once from free memory (a search holds its whole segment in memory) and prints each entry as it finishes:

   ```bash
   BIN=~/.codex/marketplaces/yscope/plugins/clp/bin
   "$BIN"/logtype-insight-extract          # add --no-freqs when FREQS=UNAVAILABLE
   # Only when the extract printed QUERY_PLAN_INVALID= above zero — see below:
   "$BIN"/kql-build check-plan /tmp/logtype-query-plan.txt | grep ERROR
   "$BIN"/kql-build check-plan /tmp/logtype-query-plan-repaired.json || exit 1
   "$BIN"/logtype-cache set-plan --key "$APP_KEY" < /tmp/logtype-query-plan-repaired.json
   jq -c '.query_plan[]' /tmp/logtype-query-plan-repaired.json > /tmp/logtype-query-plan.txt
   # Add the severity/logger baseline (samples the archive for a few seconds; SCHEMA= is the extract's line):
   "$BIN"/logtype-baseline-plan --archive <archive-dir> --schema-json '<SCHEMA= line>'
   # Then run the whole plan as one query pool:
   "$BIN"/logtype-query-plan-run --retry-failed <archive-dir>
   "$BIN"/logtype-query-plan-run --print-table
   ```

   `QUERY_PLAN_INVALID=` above zero means entries without a valid `match` filter — typically a plan cached before plans used `match`, whose entries carry hand-written `kql` strings (`QUERY_PLAN_INVALID_ENTRIES=` lists them). Repair them once before running the plan: tell the user, list the reasons with `check-plan ... | grep ERROR`, and write `/tmp/logtype-query-plan-repaired.json` as `{"query_plan":[...]}` holding every entry in order — the valid ones unchanged, each invalid one with the same label, method, project, grep, and jq, its filter rewritten as an equivalent `match` (for a `kql` string that mixes AND and OR without parentheses, the grouping its label means), and no `kql` key. Validate it with `check-plan`, store it with `set-plan` (it replaces only the plan; templates and taxonomy stay), and refresh `/tmp/logtype-query-plan.txt` as shown. The next run reads the repaired plan from the cache.

   The runner renders each entry's `match` to KQL (values quoted, groups parenthesized) and records that KQL, the result, status (`ok` / `zero` / `error` / `timeout`, plus a `non_selective` flag at 90% or more of the records), elapsed time, and a few samples in `/tmp/logtype-query-results.ndjson`; the run also records the archive's total record count. As entries finish, give one line per entry: its number, label, result or status, and elapsed time. The baseline entries (`origin: "baseline"`) give the severity and logger breakdown; when a rare-severity residual is small, a follow-up entry fetches those records, and its `samples` are the errors and warnings themselves. `/tmp/logtype-category-totals.json` holds the exact records per category, so report those instead of a keyword probe's count. Then run `"$BIN"/logtype-insight-facts --schema-json '<SCHEMA= line>' --results-file /tmp/logtype-query-results.ndjson --freqs-file <FREQS_FILE>` (add `--freqs-file none --category-totals none` when frequencies are unavailable): it writes `/tmp/logtype-insight-facts.md` with every number of the report computed in code, so quote figures from that file and never add up or derive your own. Before presenting the report, save it and run `"$BIN"/logtype-report-check <report file> --also <results table file>`; fix or remove any figure it flags. When the pool is done, show the `--print-table` output verbatim and call out the entries that failed, matched nothing, or matched nearly everything. Do not re-run plan entries; for an `error` or `timeout` entry, run ONE corrected query (e.g. quote a wildcard value that contains spaces, `<message>:"*a b*"`) and log it in the Query Log. Then give one line with the top templates by frequency, and "queries done, writing the report" before step 8. For the queries you run yourself, pick the method that fits:
   - `count`: run the KQL with `--count` (in-engine; cannot be combined with `--projection`), never `--projection ... | grep -c '^{'`. It prints one `{"archive_id":...,"count":N}` line per archive and nothing when zero records match; treat empty output as a real zero.
   - `project+grep`: fold the target into the KQL as `<message>:"*text*"`, and OR the wildcards for a keyword alternation (`<message>:"*a*" OR <message>:"*b*"`). Only when the target needs real regex features (anchors, character classes, backreferences), run the KQL with `--projection`, then `grep '^{' | jq -r '.<message>' | grep -Ei '<grep>'`. Add `--limit N` when a few example records are enough.
   - `project+jq`: run the KQL with `--projection`, then `grep '^{' | jq -r '<jq>'`.
   - `semantic`: run `semantic("...") AND <kql>` with `--projection`.

MANDATORY semantic pass — in addition to any query_plan entries whose method is `semantic`, always run at least one scoped `semantic()` query derived from the goal or the dominant templates, e.g. `semantic("...") AND <severity>:<value>` or `semantic("...") AND <logger>:"*<substr>*"`. Never run an unscoped `semantic()`. Discard any query that returns nothing or only generic/meaningless logtypes — do not include it in the report.

Then:
   - **Per-template frequencies** (the count baseline): read the bootstrap's `FREQS_FILE`, already sorted most frequent first — `head -20 /tmp/logtype-freqs.ndjson`. Never recompute them by projecting and counting messages. With `FREQS=UNAVAILABLE`, say so in the Logtype Baseline section and omit counts.
   - **Total records**: `total_records` in `/tmp/logtype-query-results.ndjson` (already counted; do not recount). **Severity/logger breakdowns**: the bootstrap DIST lines cover only the sampled records; for exact totals run `--count` per value, including the dominant one (it costs the same as a rare one). List unknown values first with `--unique <field>` (it still scans the matching records). **Group totals**: sum `count` over the matching templates in `/tmp/logtype-freqs.ndjson` instead of scanning records. **Time span**: project the timestamp field and use `head`/`tail` (chronological; do NOT sort), or `--tge`/`--tle` if the timestamp is a real epoch.
   - `<message>:term` is an exact match, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so use it when you know a field's full value; message content is free text and almost always needs a substring wildcard — `<message>:"*term*"`. Combine with a scalar filter in one compound query when you can (`<severity>:<value> AND <message>:"*term*"`, `<logger>:"*<substr>*" AND <message>:"*term*"`). Fall back to projecting message + grep only when the match needs real regex features, never for a plain keyword alternation.

8. Present a Markdown Logtype Insights Report:
   1. **Summary** — total records, severity counts, time span, top logger/component.
   2. **Logtype Baseline** — distinct template count, top N templates by frequency, the discovered category breakdown. The spine of the report.
   3. **Issues & Warnings** — error/warning counts, top 3 warning *templates* (grounded, not guessed), actionable problems; semantic-only findings if any.
   4. **Notable Categories** — per category of interest, counts + representative templates and what they indicate.
   5. **Performance Signals** — timing/throughput/slow-operation templates and counts (if any); semantic-only findings if any.
   6. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
   7. **Semantic Search Coverage** — mandatory (the semantic pass always runs), but report only meaningful findings — matches that classification missed or confirmed, with their queries; drop empty/no-hit queries. If nothing meaningful surfaced, one line saying so.
   8. **Follow-up queries** — 2–3 concrete queries derived from templates.
   9. **Query Log** — every query you ran beyond the plan (exact KQL + flags), its result, and whether the report uses it, including empty ones and corrected re-runs of failed plan entries. The plan's own entries are not repeated; their table was shown when the plan finished.

9. Offer to drill deeper on a finding, note that re-running on the same application skips classification (cached plan reused), or decompress: `~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress <archives-dir> <out-dir>`.

## The message field needs the same wildcard rule as any field

The message field (`message` structurized, `msg` native Mongo, …) is stored as a CLP-string (logtype template + encoded variables — what makes `stats.log_shapes` and the compression work). That storage is irrelevant to searching it: `<message>:term` is an exact match, same as `<field>:term` on any field, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so prefer it whenever you know the full field value; wildcard only for a substring match — `<message>:"*term*"` — which is what message content almost always needs, since it's free text. Prefer a direct wildcard search on the message field over project+grep:

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

Semantic search (`semantic("…")`) also reads the logtypes directly and is a good complement to wildcard search for concept-shaped questions. The insight pass (step 7) always runs one mandatory scoped semantic cross-check; beyond that, use it only for an ambiguous template, grouping similar templates, or a conceptual user question — always scoped: `semantic("…") AND <severity>:<value>`. Flags: `--semantic-top-k` (default 5) and `--semantic-threshold` (default 0.3; raise for precision).

## Classification cache notes

- `app_key = sha256(sorted set of distinct logtype strings, each capped at `MAX_CHARS` characters)` — the fingerprint of the *embedded* vocabulary, since the same limit is applied before embedding; cache dir `~/.config/yscope-clp-plugin/logtype-cache/` (`$CLP_LOGTYPE_CACHE_DIR` or `--cache-dir` to override). Entries store `schema`, `taxonomy`, `templates` (FULL, byte-exact), `query_plan`, plus `max_chars`, `classified_at`, and `grown_from` lineage.
- `diff` modes: **UPTODATE** (reuse, no classifying — but verify the cached schema; a full template differing only past the character limit is appended with the category of the truncated form it shares), **GROWTH** (classify only the new templates, `put-merged` unions them into the base entry), **NEW** (classify all, store fresh). The bootstrap runs `diff` for you and fetches the relevant entries.
- GROWTH matching compares byte-exact full templates; the subset test behind it uses the truncated sets. Byte-exactness is guaranteed when you go through `logtype-cluster expand` (it copies members verbatim); on the no-clusterer path, paste logtypes verbatim from the normalized NDJSON.
- Inspect: `logtype-cache list` (shows lineage), `logtype-cache show <APP_KEY>`.
