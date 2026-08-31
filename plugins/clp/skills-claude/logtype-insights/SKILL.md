---
name: logtype-insights
description: App-agnostic logtype-baseline log analysis with CLP. Dump the archive's logtype dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification and dynamically updates it when the archive grows (classifies only new templates, merges into the existing entry), and reports the archive's logtype count. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
allowed-tools:
  - "Agent"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache:*)"
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

End-to-end analysis of **any** CLP archive — structurized text logs (vLLM
wrapper logs → `timestamp/logger/level/message`), native JSON logs (MongoDB →
`t.$date/s/c/msg/attr`), or other JSON — using the **logtype baseline** method:
dump the archive's logtype dictionary first, classify those *real* message
templates into categories, and derive every later query from a template that is
guaranteed to exist. No blind keyword batteries, no queries wasted on keywords
that aren't there.

The logtype method is **not application-specific**: the dictionary dump, the
generic category taxonomy, the classification cache, and the project+grep
retrieval pattern all work on any archive. The only thing that changes between
applications is the set of templates — which the skill reads from the archive
itself rather than guessing.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs
first, use `compress-folder`.

## Why a logtype baseline beats blind search

A CLP logtype is a message template with variables replaced by `<*>`, e.g.
`Triton not installed or not compatible; certain GPU-related functions ...` or
MongoDB's `Slow query`, `attr.durationMillis=<*>`. The logtype dictionary is the
**complete vocabulary** of distinct message shapes in the archive — for a
typical run, tens to a few hundred templates, no matter how many millions of
records. Dumping it gives you, in one cheap pass that reads the dictionary
rather than every record:

- Every kind of event the run actually produced (no guessing keywords).
- The static tokens of each template, which you turn into queries that always
  match — so counts are exact and zero queries return zero by surprise.
- A natural unit for "top repeated messages": frequency per template.

The blind variants run a fixed battery of hardcoded
queries; on an unfamiliar archive many return nothing. This skill runs **1 dump
+ schema discovery + a handful of targeted queries**, each grounded in a real
template.

## Why the classification is cached

Classifying the templates into categories and deriving a query plan is the one
expensive step, and it is a property of the **application**, not the individual
capture: the same app build emits the same message templates on every run, so
the same classification applies. This skill caches the classification keyed by a
fingerprint of the template set (`sha256` of the sorted logtypes). On a cache
hit (same app), classification is skipped entirely and the skill goes straight
to the insight pass with the pre-made plan — so re-analyzing the same
application costs only the cheap Haiku insight pass, not the classification.
When the archive **grows** (new logs add new message templates), the cache is
updated **dynamically**: only the newly-appearing templates are classified and
merged into the existing entry, instead of reclassifying the whole dictionary
(see "Classification cache details"). The skill also reports the number of
logtypes in the current archive.

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- A folder of raw logs — compress first with the app-appropriate settings, since
  compression is the one app-specific step:
  - vLLM wrapper text logs: `--structurize` (produces `timestamp/logger/level/message`).
  - MongoDB JSON: `--extensions '*' --timestamp-key t.$date` (native).
  - Generic JSON with a known timestamp field: `--timestamp-key <field>`.
  - Then point this skill at the resulting archive.
- If nothing was provided, ask for an archive or folder path.

## Workflow

Each Bash call runs in its own shell, so shell variables do not persist between
steps. Re-declare `ARCHIVE`, `SEARCH`, and `CACHE` (and, on the GROWTH path,
`MODE`/`APP_KEY`/`BASE_KEY`) in any command that uses them, or run the dependent
commands together in one call.

1. Determine the input:
   - If the user provided an archive path, use it.
   - If the user provided a folder, compress it with the app-appropriate
     settings (above) and use the resulting archive. If the app is unknown, ask
     the user how the logs should be compressed (structurize vs native
     `--timestamp-key`), or have them compress first and pass the archive.
   - If nothing was provided, ask for an archive or folder path.

2. Report compression stats when you compressed the folder:
   - `Raw input bytes`, `Archive bytes`, `Compression ratio`,
     `File size reduction`, `Input files`, `Archives dir`, `Archive metadata`.

3. **Discover the schema** (cheap; do this in the parent). A no-projection
   search returns the full original record, so one sample line reveals the
   field names:

   ```bash
   ARCHIVE=<archive-dir>
   SEARCH="${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql"

   # One full record (reveals the JSON keys / structurized fields):
   "$SEARCH" "$ARCHIVE" '*' 2>/dev/null | grep '^{' | head -1
   ```

   Identify and record, as `schema`, the field names for:
   - **timestamp** — e.g. `timestamp` (vLLM structurized) or `t.$date` (Mongo).
     If it is a real epoch (native JSON), `--tge`/`--tle` work; if it is a
     structurized string, they do not.
   - **severity** — e.g. `level` (vLLM) or `s` (Mongo).
   - **logger/component** — e.g. `logger` (vLLM) or `c` (Mongo).
   - **message** — the clp-string field whose logtypes appear in
     `stats.logtypes` — e.g. `message` (vLLM) or `msg` (Mongo).
   - **payload** (optional) — e.g. `attr` (Mongo); note the useful leaf paths
     (e.g. `attr.durationMillis`, `attr.host`).
   Also note the distinct values of the severity and logger fields (one count
   query each) so the classifier and insight pass can use the real vocabularies:
   ```bash
   "$SEARCH" --projection <severity> "$ARCHIVE" '*' | grep '^{' | jq -r '.<severity>' | sort | uniq -c | sort -rn
   "$SEARCH" --projection <logger>   "$ARCHIVE" '*' | grep '^{' | jq -r '.<logger>'  | sort | uniq -c | sort -rn
   ```

4. **Dump the logtype baseline** (cheap; reads the dictionary, not every record):

   ```bash
   # Canonical template dictionary — one JSON object per logtype:
   #   {"id":0,"logtype":"Triton not installed ... <*> functions ..."}
   # The wrapper prints archive-metadata header lines to stdout, so filter to
   # JSON records with grep '^{' before jq — jq errors on the header otherwise.
   "$SEARCH" "$ARCHIVE" 'stats.logtypes' 2>/tmp/logtypes.err \
     | grep '^{' > /tmp/logtypes.ndjson

   # Summary: how many distinct templates, and the templates themselves.
   jq -s 'length' /tmp/logtypes.ndjson
   jq -r '.logtype' /tmp/logtypes.ndjson
   ```

   **Fallback if `stats.logtypes` emits no NDJSON** (some builds only print a
   `[stats]` dictionary-size summary to stderr). If `/tmp/logtypes.ndjson` has
   zero JSON lines, build an approximate baseline by projecting the message
   field for all records and templatizing the variable runs (O(records), but
   produces templates AND counts in one pass):

   ```bash
   MSG=<message-field>   # e.g. message (vLLM) or msg (Mongo)
   "$SEARCH" --projection "$MSG" "$ARCHIVE" '*' \
     | grep '^{' | jq -r --arg f "$MSG" '.[$f]' \
     | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
     | sort | uniq -c | sort -rn > /tmp/logtype-freqs.txt
   ```

   (The wrapper prints archive-metadata header lines to stdout, so `grep '^{'`
   filters to JSON records before `jq` — same idiom as `grep -c '^{'` for
   counting.)

5. **Report the logtype count and probe the cache for dynamic update.** The
   archive's logtype count and the cache state are determined in one step:

   ```bash
   CACHE="${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache"

   # Number of distinct logtypes in the current archive (report this to the user):
   LOGTYPE_COUNT=$("$CACHE" count --logtypes-file /tmp/logtypes.ndjson)
   echo "Logtypes in this archive: $LOGTYPE_COUNT"

   # Dynamic-update probe. Emits a header line then NDJSON {"logtype":"..."} for
   # the templates that need classifying (empty on UPTODATE):
   #   UPTODATE\t<app_key>\t<count>                       -> reuse cache, nothing to classify
   #   GROWTH  \t<app_key>\t<base_key>\t<count>\t<new_n>   -> archive grew from <base_key>;
   #                                                         classify the <new_n> new templates
   #   NEW     \t<app_key>\t<count>                       -> no base; classify all <count>
   "$CACHE" diff --logtypes-file /tmp/logtypes.ndjson > /tmp/lt-diff.out
   HEADER="$(head -1 /tmp/lt-diff.out)"
   MODE="$(printf '%s' "$HEADER" | cut -f1)"
   APP_KEY="$(printf '%s' "$HEADER" | cut -f2)"
   BASE_KEY="$(printf '%s' "$HEADER" | cut -f3)"   # only set when MODE=GROWTH
   # `|| true` because grep exits 1 when there is nothing to classify, which is
   # the normal UPTODATE (cache-hit) case — not an error.
   grep '^{' /tmp/lt-diff.out > /tmp/logtypes-to-classify.ndjson || true
   ```

   - **UPTODATE:** the archive's template set is unchanged since the cached
     classification — reuse it. Verify the cached `schema` matches the schema you
     discovered in step 3; if it matches, load it and skip to step 7:
     `"$CACHE" get "$APP_KEY" > /tmp/logtype-classification.json`. If the schema
     differs, treat as NEW (reclassify all).
   - **GROWTH:** the archive grew from a previous capture (`base_key`). Only the
     `<new_n>` new logtypes need classifying — the existing templates keep their
     cached categories. Save the base classification for the classifier to reuse:
     `"$CACHE" get "$BASE_KEY" > /tmp/logtype-base-classification.json` where
     `BASE_KEY="$(printf '%s' "$HEADER" | cut -f3)"`. Then classify the new
     templates (step 6) and merge.
   - **NEW:** first capture of this application (no compatible base). Classify
     all templates (step 6) and store.

6. **(GROWTH / NEW only) Classify the templates diff emitted.** Spawn a
   **classification subagent** with the Agent tool, model `sonnet` (fall back to
   `haiku`). It classifies ONLY the templates in
   `/tmp/logtypes-to-classify.ndjson` (the new ones for GROWTH, all of them for
   NEW), in the discovered field names, and writes the result as structured JSON
   to `/tmp/logtype-new-class.json`. (On UPTODATE this step is skipped.)

   Classification subagent prompt template (fill in `ARCHIVE`, the `schema`, and
   paste the templates to classify from step 5; for GROWTH also paste the base
   taxonomy/plan so existing categories are reused):

   ```
   You are classifying the logtype templates listed below for a CLP archive, so a
   later insight pass can run targeted queries. Do NOT write the final report —
   only the classification JSON. Classify ONLY the templates listed below (not any
   others) — for an incremental update these are the NEW templates; for a first
   run they are all of them.

   Archive: ARCHIVE
   Discovered schema (field names in this archive):
     timestamp: <TS>
     severity:  <SEV>
     logger:    <LOGGER>
     message:   <MSG>          (the clp-string field whose logtypes these are)
     payload:   <PAYLOAD>      (leaf paths if any, e.g. attr.durationMillis)
   Severity values seen: <e.g. I,W,E,F,D1..D5 or INFO,DEBUG,WARN,ERROR>
   Logger values seen:   <e.g. NETWORK,CONTROL,REPL,... or sflow.task.vllm_worker_3,...>

   TEMPLATES TO CLASSIFY (one {"logtype":"..."} per line; classify each):
   <PASTE /tmp/logtypes-to-classify.ndjson HERE>

   [Only for GROWTH] Existing categories from the previous classification — REUSE
   these where a template fits; add a new category only if none fits. Existing
   query-plan labels (do not duplicate): <paste base taxonomy categories and
   base query_plan labels from /tmp/logtype-base-classification.json>

   Method:
   1. Classify EACH template above into the best-fitting category. Use this GENERIC
      default taxonomy, AND any APP-SPECIFIC categories already in use (GROWTH) or
      that you discover from the templates (NEW), e.g. for MongoDB:
      workload/operations (slow query, write-concern waits), replication/election,
      sharding, indexing, WiredTiger/storage; for vLLM: worker-health, kv-cache,
      model-loading. Generic defaults:
        - errors / exceptions / failures
        - warnings
        - performance (latency / throughput / timing / "took <*> ms")
        - config / startup / initialization
        - network / connectivity / timeout
        - resource (memory / disk / file-descriptors / storage pressure)
        - lifecycle / state-transitions (start/stop/election/stepdown/restart)
        - security / auth / access
        - other (note but don't deep-search)
   2. Build a QUERY PLAN: a list of targeted queries, each derived from one or
      more of the templates above, expressed in the discovered field names. For
      each plan entry give: label, the KQL filter (using the searchable scalar
      fields — severity/logger/payload leaves; NOT message:term which is a
      clp-string and returns 0), the columns to --projection, and the method:
        - "count"              -> count matches via `... | grep -c '^{'`
        - "project+grep"       -> project the message field and grep its static text
        - "project+jq"         -> project message/payload and jq-filter (e.g. a
                                  numeric threshold on a payload leaf)
        - "semantic"           -> semantic("...") AND <scalar filter>, ONLY for an
                                  ambiguous template or to group similar ones
      Example plan entry (Mongo schema):
        {"label":"Slow queries","kql":"attr.durationMillis:*",
         "project":"t.$date,attr.durationMillis,msg",
         "jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}
      Example plan entry (vLLM schema):
        {"label":"Memory warnings","kql":"level:WARNING",
         "project":"timestamp,level,message","grep":"memory|OOM|KV",
         "method":"project+grep"}
      For GROWTH, reuse existing plan labels where the new templates fit an
      existing category; add new plan entries only for genuinely new signals.
   3. Remember: the message field is a clp-string. KQL `message:term` /
      `msg:term` and `message:*term*` / `msg:*term*` return 0. Only the scalar
      fields (severity, logger, payload leaves) are KQL-searchable. Retrieve
      message content by projecting the message field and grepping.

   Write the result as valid JSON to /tmp/logtype-new-class.json with EXACTLY
   this shape, then print "DONE" and nothing else:
     {
       "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
       "taxonomy": [{"category":"<name>","description":"<one line>"}],
       "templates": [{"logtype":"<template>","category":"<category>"}],
       "query_plan": [{"label":"<...>","kql":"<...>","project":"<...>","grep":"<...>","jq":"<...>","method":"<count|project+grep|project+jq|semantic>"}]
     }
   Use only the keys each entry needs (omit null/empty keys). Include EVERY
   template you were given (the diff-emitted set) in `templates` — for GROWTH
   this is only the new templates (they will be merged with the base
   classification); for NEW it is all of them. Use the discovered field names
   verbatim in `kql` and `project`.
   ```

   After the classification subagent returns, validate and store. For GROWTH the
   new templates are merged into the base entry (templates/taxonomy/query_plan
   unioned, `grown_from` recorded); for NEW it is stored fresh:
   ```bash
   # Validate shape before storing: the fields must be ARRAYS. A bare
   # `.templates` test passes for a scalar, which would poison the cache entry.
   jq -e '(.taxonomy|type=="array") and (.templates|type=="array") and (.query_plan|type=="array")' \
     /tmp/logtype-new-class.json >/dev/null || exit 1
   if [[ "$MODE" == "GROWTH" ]]; then
     # Guard: an empty BASE_KEY would silently store a fresh entry containing
     # ONLY the new templates, discarding every cached classification.
     [[ -n "$BASE_KEY" ]] || { echo "error: GROWTH with empty BASE_KEY" >&2; exit 1; }
     "$CACHE" put-merged --base-key "$BASE_KEY" --key "$APP_KEY" < /tmp/logtype-new-class.json
   else
     "$CACHE" put-merged --key "$APP_KEY" < /tmp/logtype-new-class.json
   fi
   "$CACHE" get "$APP_KEY" > /tmp/logtype-classification.json   # the merged/full plan for step 7
   ```
   (On the next run, if the archive is unchanged `diff` returns UPTODATE and this
   step is skipped; if it grew again, only the newly-added templates are
   classified and merged.)

7. **Spawn the insight subagent.** Use the Agent tool with model `haiku` (fall
   back to `sonnet`). Paste the discovered `schema`, the `taxonomy`, the
   per-category template groups, and the `query_plan` (from the cache or freshly
   classified) into the subagent prompt. The subagent executes the plan and
   returns only the compact Markdown report.

   Insight subagent prompt template (fill in `ARCHIVE`, `GOAL`, the `schema`, and
   paste the classification's `taxonomy`, templates grouped by category, and
   `query_plan`). Replace `SEARCH_WRAPPER` with the **resolved absolute path** of
   `${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql` — the subagent does not inherit
   `${CLAUDE_PLUGIN_ROOT}`, so the literal variable will not work there:

   ```
   Analyze this CLP archive by executing the provided query plan: ARCHIVE
   Search wrapper: SEARCH_WRAPPER
   Goal: GOAL

   SCHEMA (field names in this archive):
     timestamp: <TS>   severity: <SEV>   logger: <LOGGER>   message: <MSG>
     payload leaves: <...>     time-range flags work: <yes if epoch / no if string>

   TAXONOMY (categories):
   <PASTE taxonomy>

   TEMPLATES BY CATEGORY (every distinct message template; <*> marks variables):
   <PASTE templates grouped by category>

   QUERY PLAN (each entry derived from a real template — execute each):
   <PASTE query_plan>

   Method (follow strictly):
   1. Execute every query_plan entry. For "count": run the KQL and `grep -c
      '^{'`. For "project+grep": run the KQL with --projection, then `grep '^{' |
      jq -r '.<message>' | grep -Ei '<grep>'`. For "project+jq": run the KQL with
      --projection, then `grep '^{' | jq -r '<jq>'`. For "semantic": run
      `semantic("...") AND <kql>` with --projection.
   2. CRITICAL — the message field is a CLP-string: KQL `<message>:term` and
      `<message>:*term*` return 0. Only the scalar fields (severity, logger,
      payload leaves) are KQL-searchable. Retrieve/count records of a template by
      projecting the message field and grepping its distinctive STATIC text.
      Narrow first with a working scalar field when you can:
        <severity>:<value>   (works)  then project message + grep
        <logger>:*<substr>*  (works)  then project message + grep
   3. Per-template FREQUENCIES for the whole archive in one pass (the count
      baseline) — project the message field, templatize, uniq -c:
        clp-s-search-kql --projection <message> ARCHIVE '*' \
          | grep '^{' | jq -r '.<message>' \
          | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
          | sort | uniq -c | sort -rn
      Report the dominant templates by count as "top repeated messages".
   4. Total records: '*'. Severity breakdown: one count per severity value.
      Logger breakdown: project logger + uniq -c. Time span: project the
      timestamp field and use head/tail (records are chronological; do NOT sort),
      OR use --tge/--tle if the schema says time-range flags work.
   5. Run semantic() ONLY for an entry whose method is "semantic" — never as the
      default. Scope it: semantic("...") AND <severity>:<value>.

   Efficiency rules:
   - Compound KQL, not many separate queries.
   - Project aggressively; omit --projection only when you need the full record.
   - Do NOT use --tge/--tle unless the schema says the timestamp is epoch.
   - CRITICAL: the message field is a clp-string — `<message>:term` and
     `<message>:*term*` ALWAYS return 0. Search message content by projecting it
     and grepping/jq-filtering. Only the scalar fields are KQL-searchable.
   - Add --ignore-case when case is uncertain.

   Return ONLY a Markdown Logtype Insights Report with these sections:
   1. Summary — total records, severity counts, time span, top logger/component.
   2. Logtype Baseline — total distinct templates; the top N templates by
      frequency (count + template); the discovered category breakdown
      (errors: K templates, performance: K, ...). This is the spine of the
      report.
   3. Issues & Warnings — error/warning counts, top 3 warning TEMPLATES (not
      substrings), actionable problems; semantic-only findings if any.
   4. Notable Categories — for each discovered category of interest, counts +
      representative templates and what they indicate.
   5. Performance Signals — timing/throughput/slow-operation templates and counts
      (if the app produces any); semantic-only findings if any.
   6. Configuration & Startup — config/init templates grounded in the baseline
      (if any).
   7. Semantic Search Coverage — only if semantic() was used; what it found that
      template-classification missed.
   8. Top 3 follow-up KQL queries (derived from templates, mix keyword+semantic).
   ```

8. Present the subagent's report to the user. Offer to:
   - Drill deeper with another subagent pass on a specific template/finding.
   - Reuse the cache: note that re-running this skill on the same application
     will skip classification (the cached plan is reused).
   - Decompress the archive for raw inspection:
     ```bash
     "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
       /tmp/archive \
       /tmp/archive-decompressed
     ```

## How to retrieve & count records of a logtype template

A logtype is static text with `<*>` where variables were. The message field is
a CLP-string, so **KQL `<message>:term` / `<message>:*term*` return 0** — you
cannot match records by message content through KQL. Instead, project the
message field and grep/jq-filter for the template's distinctive static text.

Full retrieve+count pattern (substitute the discovered field names):
```bash
ARCHIVE=<archive-dir>
SEARCH="${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql"
MSG=<message-field>          # message (vLLM) or msg (Mongo)
SEV=<severity-field>         # level (vLLM) or s (Mongo)
# records of one template, with timestamp + severity:
"$SEARCH" --projection <timestamp>,$SEV,$MSG "$ARCHIVE" '*' \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("DistinctiveStaticText";"i"))'
# count of that template:
"$SEARCH" --projection $MSG "$ARCHIVE" '*' \
  | grep '^{' | jq -r --arg f "$MSG" '.[$f]' | grep -c 'DistinctiveStaticText'
```

Narrow the projection with a working scalar field first when you can —
`<severity>:` and `<logger>:` ARE searchable, so slice before grepping:
```bash
# only WARNING records, then grep message — cheaper than scanning all records
"$SEARCH" --projection <timestamp>,$SEV,$MSG "$ARCHIVE" "$SEV:WARNING" \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("StaticText";"i"))'
```

Rules of thumb:
- Pick the rarest distinctive static text in the template — it narrows fastest
  and is least likely to be a variable.
- Stopwords ("the", "for", "a", "in") and pure variables are useless — skip.
- For all-template frequencies at once, use the templatize count pass (insight
  subagent method step 3), not per-template greps.

## Known limitation: the message field is a CLP-string

In CLP archives the message field (`message` for structurized text, `msg` for
native Mongo, etc.) is stored as a CLP-string (a logtype template + encoded
variables — that is what makes `stats.logtypes` work and what gives the
compression). A consequence: **KQL cannot search message content** —
`<message>:term` and `<message>:*term*` always return 0 (verified: even on a
clean archive where the message literally contains the term). Only existence
(`<message>:*`) matches. The scalar fields (severity, logger, and — for native
JSON — payload leaf paths) ARE KQL-searchable.

This is exactly why the logtype-baseline approach matters: `stats.logtypes`
reads the message dictionary directly (bypassing KQL), giving the full template
vocabulary that blind `<message>:*term*` queries cannot reach. To retrieve/count
records of a template, project the message field and grep — do not use
`<message>:` KQL. Semantic search (`semantic("…")`) also searches the logtypes
directly and is the one KQL construct that reaches message content.

## stats.logtypes details

- `stats.logtypes` dumps the logtype dictionary: one JSON object per line,
  `{"id":N,"logtype":"...<*>..."}`. It works on both structurized text archives
  and native-JSON archives (the logtypes are built from the message field).
- You **cannot** filter a stats query by substring (`stats.logtypes:foo` is not
  valid). It always dumps the whole dictionary. Filter the NDJSON downstream:
  ```bash
  jq -r 'select(.logtype|test("failed";"i")) | .logtype' /tmp/logtypes.ndjson
  ```
- `stats.logtypes` gives templates and ids but **not per-template counts**. Get
  counts with the templatize pass (project the message field + sed + `uniq -c`),
  which yields `count \t template` directly in one O(records) scan. For a single
  template, project the message field and `grep -c` its distinctive static text.
- On builds where `stats.logtypes` emits no NDJSON (only `[stats]` summary on
  stderr), use the templatize fallback (step 4) — it produces templates + counts
  in one pass over the message field.

## Classification cache details

- `app_key = sha256(sorted set of distinct logtype strings)` — a fingerprint of
  the application's message vocabulary. Same template set → same key → UPTODATE
  → classification reused. The cache lives at
  `~/.config/yscope-clp-plugin/logtype-cache/` (override with
  `$CLP_LOGTYPE_CACHE_DIR` or `--cache-dir`).
- The cache entry stores the discovered `schema`, the `taxonomy`, the per-template
  `templates` classification, and the `query_plan`, plus `app_key`, `classified_at`
  (stamped on store), and `grown_from` (set when the entry was produced by an
  incremental grow).
- **Dynamic update when the archive grows.** `logtype-cache diff` classifies the
  cache state into three modes:
  - **UPTODATE** — the template set is unchanged since the cached classification;
    reuse it (no subagent). Verify the cached `schema` matches the current
    archive's schema (one sample record); if not, reclassify as NEW.
  - **GROWTH** — the archive grew from a previous capture: a cached entry's
    template set is a subset of the current set. Only the **new** templates are
    classified and merged into that base entry via `put-merged`
    (templates/taxonomy/query_plan unioned; `grown_from` recorded). Existing
    templates keep their cached categories — no reclassification of the bulk.
  - **NEW** — no compatible base (first capture of this application, or a
    different app). Classify all templates and store fresh.
  So re-analyzing a *growing* archive costs only the classification of the newly
  added templates, not the whole dictionary.
- Inspect the cache (`list` shows `grown_from` so you can see the lineage):
  ```bash
  "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" count --logtypes-file /tmp/logtypes.ndjson
  "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" diff  --logtypes-file /tmp/logtypes.ndjson
  "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" list
  "${CLAUDE_PLUGIN_ROOT}/bin/logtype-cache" show <APP_KEY>
  ```
- Caveat: GROWTH detection requires the cached entry's `templates[].logtype` to
  use the **exact** `stats.logtypes` strings (same whitespace and `{var}`/`<*>`
  placeholders). The classification subagent must paste the logtypes verbatim
  from the `stats.logtypes` NDJSON, not a re-templatized or stripped form, or the
  subset match can miss on whitespace.

## When to still use semantic search

With a logtype baseline, semantic search is no longer the default exploratory
tool — the baseline already tells you what exists. Use `semantic()` only for:

| Situation | Why |
| --- | --- |
| A template's category is ambiguous | `semantic("…")` votes on intent |
| Grouping similar templates | cluster the small dictionary conceptually |
| The user's question is conceptual, not template-shaped | "anything about reliability?" |
| Confirming a template-classification miss | run semantic, diff vs the baseline |

Combine with a scalar KQL field for precision:
`semantic("…") AND <severity>:<value>`. Semantic flags (only active when the
query contains `semantic()`; wrapper auto-selects endpoint + local cache):

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Nearest logtypes; raise 8–10 for recall, lower 2–3 for precision |
| `--semantic-threshold T` | 0.3 | Similarity floor 0.0–1.0; raise to 0.5+ for precision |

## Analysis patterns

- **Count per template (one pass, all templates):** project the message field,
  templatize, `uniq -c`:
  ```bash
  clp-s-search-kql --projection <message> ARCHIVE '*' \
    | grep '^{' | jq -r '.<message>' \
    | sed -E 's/\{[^}]+\}/<*>/g; s/\b[0-9]+\b/<*>/g' | sort | uniq -c | sort -rn
  ```
- **Retrieve records of one template** (message is a clp-string — grep, don't KQL):
  ```bash
  clp-s-search-kql --projection <timestamp>,<severity>,<message> ARCHIVE '*' \
    | grep '^{' | jq -rc 'select(.<message>|test("StaticText";"i"))'
  ```
- **Narrow by a searchable scalar first:** `<severity>:<value>` and
  `<logger>:*<substr>*` work in KQL; slice with them, then grep message.
- **Time span:** project the timestamp field, use `head -n 1` / `tail -n 1`; use
  `--tge`/`--tle` only if the timestamp is epoch (native JSON).
- **Scoped semantic:**
  ```bash
  clp-s-search-kql ARCHIVE 'semantic("...") AND <severity>:<value>'
  ```
- **Filter the baseline with jq:**
  ```bash
  jq -r 'select(.logtype|test("error|fail|exception";"i")).logtype' /tmp/logtypes.ndjson
  ```

## Report format

Present subagent results in this order:

1. **Summary** — total records, severity counts, archive span, top logger/component.
2. **Logtype Baseline** — distinct template count, top templates by frequency
   with counts, the discovered category breakdown. The spine of the report.
3. **Issues & Warnings** — errors, warnings, top 3 warning *templates* (grounded,
   not guessed), actionable problems; semantic-only findings if any.
4. **Notable Categories** — per discovered category of interest, counts +
   representative templates and what they indicate.
5. **Performance Signals** — timing/throughput/slow-operation templates and counts
   (if the app produces any); semantic-only findings if any.
6. **Configuration & Startup** — config/init templates grounded in the baseline
   (if any).
7. **Semantic Search Coverage** — only if semantic() was used; what it found
   that template-classification missed.
8. **Follow-up queries** — 2–3 concrete queries derived from templates.