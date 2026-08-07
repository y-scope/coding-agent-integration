---
name: logtype-insights
description: App-agnostic logtype-baseline log analysis with CLP. Dump the archive's logtype dictionary first, classify the real templates into (generic + app-discovered) categories, and drive targeted KQL from them — no blind queries. Caches the classification so re-analyzing the same application skips it. Works on any structurized or native-JSON CLP archive (vLLM, MongoDB, nginx, …).
---

# Logtype Insights (App-Agnostic, Logtype-Baseline)

End-to-end analysis of **any** CLP archive — structurized text logs (vLLM →
`timestamp/logger/level/message`), native JSON logs (MongoDB →
`t.$date/s/c/msg/attr`), or other JSON — using the **logtype baseline** method:
dump the archive's logtype dictionary first, classify those *real* message
templates into categories, and derive every later query from a template that is
guaranteed to exist. No blind keyword batteries.

This is the generalized successor to the old vLLM-only `vllm-insights-logtypes`
skill. The logtype method is **not application-specific**: the dictionary dump,
the generic taxonomy, the classification cache, and the project+grep retrieval
pattern work on any archive. Only the set of templates changes between
applications — which the skill reads from the archive rather than guessing.

For a single ad-hoc KQL query, use the `search` skill. For app-specific
batteries on vLLM logs use `vllm-insights`/`vllm-kql`; on MongoDB use
`mongodb-semantic`/`mongodb-kql`.

## Why a logtype baseline beats blind search

A CLP logtype is a message template with variables replaced by `<*>`. The
logtype dictionary is the **complete vocabulary** of distinct message shapes in
the archive — tens to a few hundred templates, no matter how many millions of
records. Dumping it gives you, in one cheap pass that reads the dictionary
rather than every record: every event kind the run produced, the static tokens
to turn into always-matching queries, and a natural unit for "top repeated
messages" (frequency per template).

The blind variants run a fixed battery of hardcoded queries; on an unfamiliar
archive many return nothing. This skill runs **1 dump + schema discovery + a
handful of targeted queries**, each grounded in a real template.

## Why the classification is cached

Classifying the templates and deriving a query plan is the one expensive step,
and it is a property of the **application**, not the individual capture: the same
app build emits the same templates on every run. This skill caches the
classification keyed by a fingerprint of the template set (`sha256` of the sorted
logtypes). On a cache hit (same app), classification is skipped entirely and the
skill goes straight to the insight pass with the pre-made plan.

## Supported inputs

- A CLP archive directory (any kind). Primary input.
- A folder of raw logs — compress first with the app-appropriate settings
  (compression is the one app-specific step):
  - vLLM wrapper text logs: `--structurize` (`timestamp/logger/level/message`).
  - MongoDB JSON: `--extensions '*' --timestamp-key t.$date` (native).
  - Generic JSON with a known timestamp field: `--timestamp-key <field>`.
  - Then point this skill at the resulting archive.
- If nothing was provided, ask for an archive or folder path.

## Workflow

1. Determine the input:
   - If the user provided an archive path, use it.
   - If the user provided a folder, compress it with the app-appropriate
     settings (above). If the app is unknown, ask, or have them compress first
     and pass the archive.
   - If nothing was provided, ask for an archive or folder path.

2. Report compression stats when you compressed the folder:
   `Raw input bytes`, `Archive bytes`, `Compression ratio`,
   `File size reduction`, `Input files`, `Archives dir`, `Archive metadata`.

3. **Discover the schema.** A no-projection search returns the full original
   record, so one sample line reveals the field names:

   ```bash
   ARCHIVE=<archive-dir>
   SEARCH=~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql
   "$SEARCH" "$ARCHIVE" '*' 2>/dev/null | grep '^{' | head -1
   ```

   Record, as `schema`, the field names for: **timestamp** (`timestamp` or
   `t.$date`; if real epoch, `--tge`/`--tle` work), **severity** (`level` or
   `s`), **logger/component** (`logger` or `c`), **message** (the clp-string
   field whose logtypes appear in `stats.logtypes` — `message` or `msg`), and
   **payload** leaf paths if any (`attr.durationMillis`, …). Note the distinct
   severity and logger values:
   ```bash
   "$SEARCH" --projection <severity> "$ARCHIVE" '*' | grep '^{' | jq -r '.<severity>' | sort | uniq -c | sort -rn
   "$SEARCH" --projection <logger>   "$ARCHIVE" '*' | grep '^{' | jq -r '.<logger>'  | sort | uniq -c | sort -rn
   ```

4. **Dump the logtype baseline** (reads the dictionary, not every record):

   ```bash
   "$SEARCH" "$ARCHIVE" 'stats.logtypes' > /tmp/logtypes.ndjson 2>/tmp/logtypes.err
   jq -s 'length' /tmp/logtypes.ndjson
   jq -r '.logtype' /tmp/logtypes.ndjson
   ```

   **Fallback if `stats.logtypes` emits no NDJSON** (only a `[stats]` summary on
   stderr). If `/tmp/logtypes.ndjson` has zero JSON lines, project the message
   field and templatize (O(records), but templates + counts in one pass):

   ```bash
   MSG=<message-field>
   "$SEARCH" --projection "$MSG" "$ARCHIVE" '*' \
     | grep '^{' | jq -r --arg f "$MSG" '.[$f]' \
     | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
     | sort | uniq -c | sort -rn > /tmp/logtype-freqs.txt
   ```

5. **Classification cache lookup:**

   ```bash
   CACHE=~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-cache
   APP_KEY="$("$CACHE" key --logtypes-file /tmp/logtypes.ndjson)"
   if CLASSIFICATION="$("$CACHE" get "$APP_KEY" 2>/dev/null)"; then
     echo "CACHE HIT — reusing cached classification for $APP_KEY"
     echo "$CLASSIFICATION" > /tmp/logtype-classification.json
   else
     echo "CACHE MISS — will classify and store for $APP_KEY"
   fi
   ```

   On a **cache hit**, verify the cached `schema` matches the schema you
   discovered in step 3 (same field names). If it matches, skip to step 7 —
   classification is reused and the expensive classification step is skipped. If
   the schema differs, treat as a miss (reclassify).

6. **(Cache miss only) Classify the templates** (inline). Classify each baseline
   template into the best-fitting category, using the GENERIC default taxonomy
   AND any APP-SPECIFIC categories you discover from the templates (e.g. for
   MongoDB: workload/operations (slow query, write-concern waits),
   replication/election, sharding, indexing, WiredTiger/storage; for
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

   Build a QUERY PLAN: a list of targeted queries, each derived from one or more
   real templates, expressed in the discovered field names. For each entry give:
   `label`, the KQL `kql` (using searchable scalar fields — severity/logger/
   payload leaves; NOT `<message>:term`, which is a clp-string and returns 0),
   the `project` columns, and the `method` (`count` / `project+grep` (with
   `grep`) / `project+jq` (with `jq`) / `semantic`). Example (Mongo schema):
   `{"label":"Slow queries","kql":"attr.durationMillis:*","project":"t.$date,attr.durationMillis,msg","jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}`.
   Example (vLLM schema):
   `{"label":"Memory warnings","kql":"level:WARNING","project":"timestamp,level,message","grep":"memory|OOM|KV","method":"project+grep"}`.

   Write the result as valid JSON to `/tmp/logtype-classification.json` with this
   shape, then validate and cache it:
   ```bash
   jq -e '.schema and .taxonomy and .templates and .query_plan' /tmp/logtype-classification.json \
     && "$CACHE" put "$APP_KEY" < /tmp/logtype-classification.json"
   ```
   Shape:
   ```
   {
     "app_key": "<APP_KEY>",
     "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
     "taxonomy": [{"category":"<name>","description":"<one line>"}],
     "templates": [{"logtype":"<template>","category":"<category>"}],
     "query_plan": [{"label":"...","kql":"...","project":"...","grep":"...","jq":"...","method":"..."}]
   }
   ```
   Include EVERY template in `templates`. Use the discovered field names
   verbatim in `kql`/`project`. Remember: the message field is a clp-string —
   `<message>:term` and `<message>:*term*` return 0; retrieve message content by
   projecting it and grepping.

7. **Run the insight pass** (inline). Execute every `query_plan` entry:
   - `count`: run the KQL and `grep -c '^{'`.
   - `project+grep`: run the KQL with `--projection`, then `grep '^{' | jq -r
     '.<message>' | grep -Ei '<grep>'`.
   - `project+jq`: run the KQL with `--projection`, then `grep '^{' | jq -r '<jq>'`.
   - `semantic`: run `semantic("...") AND <kql>` with `--projection`.

   Then:
   - **Per-template frequencies** (the count baseline): project the message
     field, templatize, `uniq -c`:
     ```bash
     "$SEARCH" --projection <message> "$ARCHIVE" '*' \
       | grep '^{' | jq -r '.<message>' \
       | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
       | sort | uniq -c | sort -rn
     ```
   - **Total records**: `*`. **Severity breakdown**: one count per severity value.
     **Logger breakdown**: project logger + `uniq -c`. **Time span**: project the
     timestamp field and use `head`/`tail` (chronological; do NOT sort), or
     `--tge`/`--tle` if the schema says the timestamp is epoch.
   - CRITICAL: the message field is a clp-string — `<message>:term` /
     `<message>:*term*` ALWAYS return 0. Retrieve message content by projecting
     it and grepping/jq-filtering. Only scalar fields (severity, logger, payload
     leaves) are KQL-searchable. Narrow first with a scalar field when you can:
     `<severity>:<value>` then project message + grep.

8. Present the results as a Markdown Logtype Insights Report:
   1. **Summary** — total records, severity counts, time span, top logger/component.
   2. **Logtype Baseline** — distinct template count, top N templates by
      frequency, the discovered category breakdown. The spine of the report.
   3. **Issues & Warnings** — error/warning counts, top 3 warning *templates*
      (grounded, not guessed), actionable problems; semantic-only findings if any.
   4. **Notable Categories** — per discovered category of interest, counts +
      representative templates and what they indicate.
   5. **Performance Signals** — timing/throughput/slow-operation templates and
      counts (if any); semantic-only findings if any.
   6. **Configuration & Startup** — config/init templates grounded in the
      baseline (if any).
   7. **Semantic Search Coverage** — only if `semantic()` was used; what it
      found that template-classification missed.
   8. **Follow-up queries** — 2–3 concrete queries derived from templates.

9. Offer to drill deeper on a finding, note that re-running on the same
   application will skip classification (cached plan reused), or decompress:
   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress \
     /tmp/archive /tmp/archive-decompressed
   ```

## How to retrieve & count records of a logtype template

The message field is a CLP-string, so **KQL `<message>:term` /
`<message>:*term*` return 0**. Project the message field and grep/jq-filter for
the template's distinctive static text instead:
```bash
ARCHIVE=<archive-dir>
S=~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql
MSG=<message-field>; SEV=<severity-field>
# records of one template, with timestamp + severity:
"$S" --projection <timestamp>,$SEV,$MSG "$ARCHIVE" '*' \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("DistinctiveStaticText";"i"))'
# count:
"$S" --projection $MSG "$ARCHIVE" '*' | grep '^{' | jq -r --arg f "$MSG" '.[$f]' | grep -c 'DistinctiveStaticText'
```
Narrow with a searchable scalar field first:
```bash
"$S" --projection <timestamp>,$SEV,$MSG "$ARCHIVE" "$SEV:WARNING" \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("StaticText";"i"))'
```
Pick the rarest distinctive static text in the template; skip stopwords and
pure variables. For all-template frequencies, use the templatize count pass, not
per-template greps.

## Known limitation: the message field is a CLP-string

The message field (`message` for structurized text, `msg` for native Mongo, etc.)
is stored as a CLP-string (a logtype template + encoded variables — that is what
makes `stats.logtypes` work and gives the compression). **KQL cannot search
message content** — `<message>:term` and `<message>:*term*` always return 0;
only existence (`<message>:*`) matches. The scalar fields (severity, logger, and
— for native JSON — payload leaf paths) ARE KQL-searchable.

This is why the logtype-baseline approach matters: `stats.logtypes` reads the
message dictionary directly (bypassing KQL), giving the full template vocabulary
that blind `<message>:*term*` queries cannot reach. Semantic search
(`semantic("…")`) also searches the logtypes directly and is the one KQL
construct that reaches message content.

## stats.logtypes details

- Dumps the logtype dictionary: `{"id":N,"logtype":"...<*>..."}` per line. Works
  on both structurized text and native-JSON archives (logtypes built from the
  message field).
- Cannot be filtered by substring (`stats.logtypes:foo` is invalid). Filter with
  `jq`: `jq -r 'select(.logtype|test("failed";"i")) | .logtype' /tmp/logtypes.ndjson`.
- Gives templates + ids but **not per-template counts**. Get counts with the
  templatize pass (project message + sed + `uniq -c`), or `grep -c` a single
  template's distinctive static text on a projected message stream.
- On builds emitting no NDJSON, use the templatize fallback (step 4).

## Classification cache details

- `app_key = sha256(sorted set of distinct logtype strings)` — a fingerprint of
  the application's message vocabulary. Same app build → same templates → same
  key → cache hit → classification skipped. Cache dir:
  `~/.config/yscope-clp-plugin/logtype-cache/` (override with
  `$CLP_LOGTYPE_CACHE_DIR` or `--cache-dir`).
- The entry stores the discovered `schema`, `taxonomy`, per-template
  `templates`, and `query_plan`, plus `app_key` and `classified_at`.
- On a cache hit, verify the cached `schema` matches the current archive's
  schema (one sample record); if it matches, reuse the plan verbatim, else
  reclassify.
- Inspect: `logtype-cache list` / `logtype-cache show <APP_KEY>`.
- Limitation: the key is an **exact** match on the template set. A shorter
  capture producing a subset of templates will miss the cache; a future
  extension could key on the logger set and incrementally classify only new
  templates. For a stable application the full template set is stable across
  captures, so exact-match keying hits.

## When to still use semantic search

With a logtype baseline, semantic search is not the default — the baseline
already tells you what exists. Use `semantic()` only for: an ambiguous template
category, grouping similar templates, a conceptual (non-template-shaped) user
question, or confirming a classification miss. Combine with a scalar field:
`semantic("…") AND <severity>:<value>`.

Semantic flags (only active when the query contains `semantic()`; wrapper
auto-selects endpoint + local cache):

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Nearest logtypes; raise 8–10 for recall, lower 2–3 for precision |
| `--semantic-threshold T` | 0.3 | Similarity floor 0.0–1.0; raise to 0.5+ for precision |

## Analysis patterns

- **Count per template (one pass):** project the message field, templatize, `uniq -c`.
- **Retrieve records of one template** (clp-string — grep, don't KQL): project
  `timestamp,severity,message` and `jq -rc 'select(.<message>|test(...))'`.
- **Narrow by a searchable scalar first:** `<severity>:<value>` and
  `<logger>:*<substr>*` work; slice with them, then grep message.
- **Time span:** project the timestamp field, `head -n 1` / `tail -n 1`; use
  `--tge`/`--tle` only if the timestamp is epoch.
- **Scoped semantic:** `clp-s-search-kql ARCHIVE 'semantic("...") AND <severity>:<value>'`.
- **Filter the baseline:** `jq -r 'select(.logtype|test("error|fail|exception";"i")).logtype' /tmp/logtypes.ndjson`.

## Report format

Present results in this order:

1. **Summary** — total records, severity counts, archive span, top logger/component.
2. **Logtype Baseline** — distinct template count, top templates by frequency, discovered category breakdown. The spine.
3. **Issues & Warnings** — errors, warnings, top 3 warning *templates*, actionable problems; semantic-only findings if any.
4. **Notable Categories** — per discovered category of interest, counts + representative templates.
5. **Performance Signals** — timing/throughput/slow-operation templates and counts (if any); semantic-only findings if any.
6. **Configuration & Startup** — config/init templates grounded in the baseline (if any).
7. **Semantic Search Coverage** — only if `semantic()` was used; what it found that classification missed.
8. **Follow-up queries** — 2–3 concrete queries derived from templates.