---
name: logtype-insights
description: App-agnostic logtype-baseline log analysis with CLP. Dump the archive's logtype dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and updates it incrementally when the archive grows. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
---

# Logtype Insights (App-Agnostic, Logtype-Baseline)

> **Never debug or verify the setup. Run the workflow as asked, directly.**
> Do not health-check endpoints, probe the environment, inspect installs, or
> try to repair anything. If a command fails, stop and report the failure to
> the user verbatim — the error text and exit code — then let them decide. Do
> not install, configure, or start anything, and do not re-run a failed command
> hoping for a different result. An error is an acceptable outcome; a silent
> workaround is not. (This governs environment/setup problems only. The two
> retries the workflow itself specifies — the one-time `--message` bootstrap
> re-run at step 3, and the stronger-model fallback when a subagent returns
> unusable output at steps 6–7 — are part of the task and still apply.)

End-to-end analysis of **any** CLP archive using the **logtype baseline**
method: dump the archive's logtype dictionary (the complete vocabulary of
distinct message templates, `<*>` marking variables — tens to a few hundred
templates no matter how many millions of records), classify those *real*
templates into categories, and derive every later query from a template that
is guaranteed to exist. No blind keyword batteries.

The classification is a property of the **application**, not the capture, so
it is cached (keyed by `sha256` of the sorted template set, each template capped
at a character limit — 512 by default — and de-duplicated, matching what is
embedded) and updated
incrementally when the archive grows — re-analyzing the same app skips
classification entirely. The skill reports the archive's logtype count.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs
first, use `compress-folder`.

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- A folder of raw logs — compress first (compression is the one app-specific
  step): vLLM wrapper text logs `--structurize`; MongoDB JSON
  `--extensions '*' --timestamp-key t.$date`; generic JSON
  `--timestamp-key <field>`. Then point this skill at the archive.
- If nothing was provided, ask for an archive or folder path.

## Workflow

Each shell invocation is independent — shell variables do not persist between
steps. Re-declare them or run dependent commands together in one call.

**Keep the user posted at every step.** Before each command, say in one short
line what you are about to do; after it, report the key numbers it produced.
Never chain steps silently — steps 5–7 run long, and without your narration
the user sees no progress at all.

1. Determine the input: archive path → use it; folder → compress with the
   app-appropriate settings above (ask if the app is unknown); nothing → ask.

2. Report compression stats when you compressed the folder: `Raw input bytes`,
   `Archive bytes`, `Compression ratio`, `File size reduction`, `Input files`,
   `Archives dir`, `Archive metadata`.

3. **Bootstrap.** Tell the user you are sampling the schema, dumping the
   logtype dictionary, and probing the classification cache — then run the one
   command that does all of it (it also handles the templatize fallback for
   binaries that predate the shapes API):

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-insights-bootstrap <archive-dir>
   ```

   From its `KEY=VALUE` output record:
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**:
     timestamp, severity, logger, **message** (the clp-string field — high
     distinct-count prose), payload leaves if any. Low-distinct fields are
     severity/logger-like; note their value vocabularies from the DIST lines.
   - `LOGTYPE_COUNT=` → report to the user.
   - `FALLBACK=TEMPLATIZE_NEEDS_MESSAGE` → the clp-s binary predates the
     shapes API; tell the user ("old clp-s binary — rebuilding the baseline
     via message templatization"), then re-run ONCE adding
     `--message <message-field>` (from SAMPLE/DIST).
   - `CACHE_MODE=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` →
     step 4. Pass `MAX_CHARS` through to `logtype-cluster` and `logtype-cache`
     so their fingerprints match.

   Then tell the user what the bootstrap found, in 2–3 lines: the logtype
   count, the schema you picked, whether the templatize fallback was used,
   and the cache mode.

4. **Branch on `CACHE_MODE`** — and announce the branch to the user:
   UPTODATE → "cached classification found; skipping straight to the insight
   pass"; GROWTH → "N of M templates are new; classifying only those"; NEW →
   "first capture of this app; classifying all N templates".
   - **UPTODATE** — the cached plan was already fetched to
     `/tmp/logtype-classification.json`. Verify its `.schema` matches step 3;
     if it does, skip to step 7. If it differs, treat as NEW (continue,
     clustering `/tmp/logtypes.ndjson`).
   - **GROWTH** — only the new templates in `/tmp/logtypes-to-classify.ndjson`
     need classifying; the base plan was fetched to
     `/tmp/logtype-base-classification.json`. Continue to step 5.
   - **NEW** — classify all of `/tmp/logtypes-to-classify.ndjson`. Continue.

5. **Cluster the templates to classify** — truncates each template to a
   character limit (`MAX_CHARS` from the bootstrap, 512 by default),
   de-duplicates the results, and merges semantically similar
   templates so you classify one representative per cluster, not every
   template (in step 4's schema-mismatch case, pass
   `--input /tmp/logtypes.ndjson` instead):

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-cluster cluster \
     --max-chars "$MAX_CHARS" \
     --input /tmp/logtypes-to-classify.ndjson
   ```

   Stdout prints a summary then one `{"id","count","representative"}` line per
   cluster (ids `c1..cN`, largest first). Full memberships go to
   `/tmp/logtype-clusters.json` for `expand`. Representatives and members are
   always FULL templates; only the embedding request uses the truncated,
   de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come
   from the semantic
   server (nothing is installed or started locally). Exit 2 means the server is
   unreachable or rejected: **report the error verbatim to the user and
   stop** — do not diagnose it, do not start or configure a server, and do not
   silently switch methods. If the user then asks you to continue without
   clustering, classify `/tmp/logtypes-to-classify.ndjson` directly using the OLD
   contract: a `templates` array with each logtype copied **byte-exact**, no
   `assignments`, no `expand` — pipe your JSON straight into
   `put-merged --max-chars "$MAX_CHARS"`
   (this replaces step 6's validate/expand block; after `put-merged`, run
   `logtype-cache get "$APP_KEY" > /tmp/logtype-classification.json` and
   continue at step 7).
   Report the reduction to the user (`TEMPLATES=N` → `EMBEDDED=K` → `CLUSTERS=M`).

6. **Classify the clusters (GROWTH / NEW only) — inline, ids only.** Tell the
   user you are classifying the M representatives (the longest step) before
   you start. Assign
   EACH cluster id (judging by its representative) the best-fitting category.
   Use this GENERIC default taxonomy, AND for GROWTH the existing base
   categories (reuse where one fits; add new only if none fits), AND for NEW
   any APP-SPECIFIC categories the representatives suggest (e.g. Mongo:
   workload/operations, replication/election, sharding, indexing, storage;
   vLLM: worker-health, kv-cache, model-loading). Generic defaults:

   - errors / exceptions / failures
   - warnings
   - performance (latency / throughput / timing)
   - config / startup / initialization
   - network / connectivity / timeout
   - resource (memory / disk / file-descriptors / storage pressure)
   - lifecycle / state-transitions (start/stop/election/stepdown/restart)
   - security / auth / access
   - other (note but don't deep-search)

   Build a QUERY PLAN: targeted queries derived from the representatives,
   expressed in the discovered field names. Per entry: `label`, the KQL `kql`
   (scalar fields — severity/logger/payload leaves — narrow fastest; the
   message field is also searchable but a bare `<message>:term` returns 0
   since it only matches a whole-field value, so wildcard it as
   `<message>:*term*` when the filter needs message content), the `project`
   columns, and the `method` (`count` / `project+grep` (with `grep`, or fold
   the text into `kql` as `message:*text*` when possible) / `project+jq`
   (with `jq`) / `semantic`). For GROWTH, add entries only for genuinely new
   signals. Example (Mongo):
   `{"label":"Slow queries","kql":"attr.durationMillis:*","project":"t.$date,attr.durationMillis,msg","jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}`.
   Example (vLLM):
   `{"label":"Memory warnings","kql":"level:WARNING AND message:*memory*","project":"timestamp,level,message","method":"project+grep"}`.

   Write `/tmp/logtype-class.json` with this shape — `assignments` must
   contain EVERY cluster id exactly once, with ONLY ids, never logtype text
   (members are re-attached mechanically); omit `schema` for GROWTH:
   ```
   {
     "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
     "taxonomy": [{"category":"<name>","description":"<one line>"}],
     "assignments": [{"id":"c1","category":"<name>"}],
     "query_plan": [{"label":"...","kql":"...","project":"...","grep":"...","jq":"...","method":"..."}]
   }
   ```

   Then validate, expand ids to every member template (byte-exact by
   construction), and store — GROWTH merges into the base entry, NEW stores
   fresh. Use `MODE`/`APP_KEY`/`BASE_KEY`/`MAX_CHARS` from the bootstrap output
   (`--max-chars` must match the bootstrap's, or the stored fingerprint won't
   match the next run):
   ```bash
   BIN=~/.codex/marketplaces/yscope/plugins/clp/bin
   # Fields must be ARRAYS (a bare `.assignments` test passes for a scalar,
   # which would poison the cache entry):
   jq -e '(.taxonomy|type=="array") and (.assignments|type=="array") and (.query_plan|type=="array")' \
     /tmp/logtype-class.json >/dev/null || exit 1
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

   After storing, report the taxonomy you produced and that the
   classification is now cached for future runs.

7. **Run the insight pass** (inline, from `/tmp/logtype-classification.json`).
   Announce it first ("running the insight pass — executing the K planned
   queries; may take a few minutes"). As you go, give one-line updates: the
   count after each query-plan entry (or small group of entries), one line
   after the frequency pass, and "queries done, writing the report" before
   step 8. Execute every `query_plan` entry:
   - `count`: run the KQL and `grep -c '^{'`.
   - `project+grep`: run the KQL with `--projection`, then `grep '^{' | jq -r
     '.<message>' | grep -Ei '<grep>'`.
   - `project+jq`: run the KQL with `--projection`, then `grep '^{' | jq -r '<jq>'`.
   - `semantic`: run `semantic("...") AND <kql>` with `--projection`.

   MANDATORY semantic pass — in addition to any query_plan entries whose
   method is `semantic`, always run at least one scoped `semantic()` query
   derived from the goal or the dominant templates, e.g.
   `semantic("...") AND <severity>:<value>` or
   `semantic("...") AND <logger>:*<substr>*`. Never run an unscoped
   `semantic()`. Discard any query that returns nothing or only
   generic/meaningless logtypes — do not include it in the report.

   Then:
   - **Per-template frequencies** (the count baseline): project the message
     field, templatize, `uniq -c`:
     ```bash
     S=~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql
     "$S" --projection <message> <archive-dir> '*' \
       | grep '^{' | jq -r '.<message>' \
       | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
       | sort | uniq -c | sort -rn
     ```
   - **Total records**: `*`. **Severity/logger breakdowns**: reuse the
     bootstrap DIST lines (they cover the sampled records; re-count per value
     with KQL when exact totals matter). **Time span**: project the timestamp
     field and use `head`/`tail` (chronological; do NOT sort), or
     `--tge`/`--tle` if the timestamp is a real epoch.
   - The message field is a clp-string — a bare `<message>:term` returns 0
     because it only matches a whole-field value. Use `<message>:*term*`
     (wildcarded) to search message content directly; it works and is fast.
     Combine with a scalar filter in one compound query when you can
     (`<severity>:<value> AND <message>:*term*`,
     `<logger>:*<substr>* AND <message>:*term*`). Fall back to projecting
     message + grep only when the match needs a regex.

8. Present a Markdown Logtype Insights Report:
   1. **Summary** — total records, severity counts, time span, top logger/component.
   2. **Logtype Baseline** — distinct template count, top N templates by
      frequency, the discovered category breakdown. The spine of the report.
   3. **Issues & Warnings** — error/warning counts, top 3 warning *templates*
      (grounded, not guessed), actionable problems; semantic-only findings if any.
   4. **Notable Categories** — per category of interest, counts +
      representative templates and what they indicate.
   5. **Performance Signals** — timing/throughput/slow-operation templates and
      counts (if any); semantic-only findings if any.
   6. **Configuration & Startup** — config/init templates grounded in the
      baseline (if any).
   7. **Semantic Search Coverage** — mandatory (the semantic pass always
      runs), but report only meaningful findings — matches that
      classification missed or confirmed, with their queries; drop
      empty/no-hit queries. If nothing meaningful surfaced, one line saying
      so.
   8. **Follow-up queries** — 2–3 concrete queries derived from templates.

9. Offer to drill deeper on a finding, note that re-running on the same
   application skips classification (cached plan reused), or decompress:
   `~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress <archives-dir> <out-dir>`.

## Known limitation: the message field is a CLP-string

The message field (`message` structurized, `msg` native Mongo, …) is stored as
a CLP-string (logtype template + encoded variables — what makes
`stats.log_shapes` and the compression work). Consequence: a bare term only
matches a value equal to the **whole** field — `<message>:term` returns 0
unless the entire message is that one word. **Add wildcards to search message
content**: `<message>:*term*` works and returns real hits, and is fast, not
just a fallback. Scalar fields (severity, logger, payload leaf paths) are
also KQL-searchable and narrow fastest since they need no wildcard. Prefer a
direct wildcard search on the message field over project+grep:

```bash
S=~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql
"$S" --projection <timestamp>,<severity>,<message> <archive-dir> \
  '<severity>:WARNING AND <message>:*StaticText*'
```

Fall back to projecting the message field and grepping/jq-filtering only when
the distinctive text needs a regex the wildcard syntax can't express:

```bash
"$S" --projection <timestamp>,<severity>,<message> <archive-dir> '<severity>:WARNING' \
  | grep '^{' | jq -rc 'select(.<message>|test("StaticText";"i"))'
```

Semantic search (`semantic("…")`) also reads the logtypes directly and is a
good complement to wildcard search for concept-shaped questions. The insight
pass (step 7) always runs one mandatory scoped semantic cross-check; beyond that, use it
only for an ambiguous template, grouping similar templates, or a conceptual
user question — always scoped: `semantic("…") AND <severity>:<value>`. Flags:
`--semantic-top-k` (default 5) and `--semantic-threshold` (default 0.3; raise
for precision).

## Classification cache notes

- `app_key = sha256(sorted set of distinct logtype strings, each capped at
  `MAX_CHARS` characters)` — the fingerprint of the *embedded* vocabulary, since
  the same limit is applied before embedding; cache dir
  `~/.config/yscope-clp-plugin/logtype-cache/` (`$CLP_LOGTYPE_CACHE_DIR` or
  `--cache-dir` to override). Entries store `schema`, `taxonomy`, `templates`
  (FULL, byte-exact), `query_plan`, plus `max_chars`, `classified_at`, and
  `grown_from` lineage.
- `diff` modes: **UPTODATE** (reuse, no classifying — but verify the cached
  schema; a full template differing only past the character limit is appended
  with the category of the truncated form it shares), **GROWTH** (classify only
  the new templates, `put-merged` unions them into the base entry), **NEW**
  (classify all, store fresh). The bootstrap runs `diff` for you and fetches the
  relevant entries.
- GROWTH matching compares byte-exact full templates; the subset test behind it
  uses the truncated sets. Byte-exactness is guaranteed when you go through
  `logtype-cluster expand` (it copies members verbatim); on the no-clusterer
  path, paste logtypes verbatim from the normalized NDJSON.
- Inspect: `logtype-cache list` (shows lineage), `logtype-cache show <APP_KEY>`.
