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

The logtype method is **not application-specific**: the dictionary dump, the
generic taxonomy, the classification cache, and the project+grep retrieval
pattern work on any archive. Only the set of templates changes between
applications — which the skill reads from the archive rather than guessing.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs
first, use `compress-folder`.

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
skill goes straight to the insight pass with the pre-made plan. When the archive
**grows** (new logs add new templates), the cache is updated **dynamically**:
only the newly-appearing templates are classified and merged into the existing
entry, not the whole dictionary (see "Classification cache details"). The skill
also reports the number of logtypes in the current archive.

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

Each shell invocation is independent, so shell variables do not persist between
steps. Re-declare `ARCHIVE`, `SEARCH`, and `CACHE` (and, on the GROWTH path,
`MODE`/`APP_KEY`/`BASE_KEY`) in any command that uses them, or run the dependent
commands together in one call.

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
   # The wrapper prints archive-metadata header lines to stdout, so filter to
   # JSON records with grep '^{' before jq — jq errors on the header otherwise.
   "$SEARCH" "$ARCHIVE" 'stats.logtypes' 2>/tmp/logtypes.err \
     | grep '^{' > /tmp/logtypes.ndjson
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

5. **Report the logtype count and probe the cache for dynamic update:**

   ```bash
   CACHE=~/.codex/marketplaces/yscope/plugins/clp/bin/logtype-cache

   # Number of distinct logtypes in the current archive (report this to the user):
   LOGTYPE_COUNT=$("$CACHE" count --logtypes-file /tmp/logtypes.ndjson)
   echo "Logtypes in this archive: $LOGTYPE_COUNT"

   # Dynamic-update probe. Header line then NDJSON {"logtype":"..."} to classify
   # (empty on UPTODATE):
   #   UPTODATE\t<app_key>\t<count>                       -> reuse cache, nothing to classify
   #   GROWTH  \t<app_key>\t<base_key>\t<count>\t<new_n>   -> classify only the <new_n> new
   #   NEW     \t<app_key>\t<count>                       -> classify all <count>
   "$CACHE" diff --logtypes-file /tmp/logtypes.ndjson > /tmp/lt-diff.out
   HEADER="$(head -1 /tmp/lt-diff.out)"
   MODE="$(printf '%s' "$HEADER" | cut -f1)"
   APP_KEY="$(printf '%s' "$HEADER" | cut -f2)"
   BASE_KEY="$(printf '%s' "$HEADER" | cut -f3)"   # only set when MODE=GROWTH
   # `|| true` because grep exits 1 when there is nothing to classify, which is
   # the normal UPTODATE (cache-hit) case — not an error.
   grep '^{' /tmp/lt-diff.out > /tmp/logtypes-to-classify.ndjson || true
   ```

   - **UPTODATE:** reuse the cached classification (verify its `schema` matches
     step 3; if not, reclassify as NEW): `"$CACHE" get "$APP_KEY" > /tmp/logtype-classification.json` → skip to step 7.
   - **GROWTH:** archive grew from `base_key` (=`cut -f3`). Save the base to reuse
     its categories: `BASE_KEY="$(printf '%s' "$HEADER" | cut -f3)"; "$CACHE" get "$BASE_KEY" > /tmp/logtype-base-classification.json`. Classify only the new templates (step 6) and merge.
   - **NEW:** no base; classify all templates (step 6) and store fresh.

6. **(GROWTH / NEW only) Classify the templates `diff` emitted** (inline). For
   GROWTH these are ONLY the new templates (preserve the base's categories for the
   unchanged templates — do not reclassify them); for NEW they are all of them.
   Classify each into the best-fitting category, using the GENERIC default
   taxonomy AND, for GROWTH, the existing base categories (reuse where a template
   fits; add a new category only if none fits), AND for NEW any APP-SPECIFIC
   categories you discover (e.g. for MongoDB: workload/operations (slow query,
   write-concern waits), replication/election, sharding, indexing,
   WiredTiger/storage; for vLLM: worker-health, kv-cache, model-loading). Generic
   defaults:

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
   of the templates you are classifying, expressed in the discovered field names.
   For each entry give: `label`, the KQL `kql` (using searchable scalar fields —
   severity/logger/payload leaves; NOT `<message>:term`, which is a clp-string
   and returns 0), the `project` columns, and the `method` (`count` /
   `project+grep` (with `grep`) / `project+jq` (with `jq`) / `semantic`). For
   GROWTH, reuse existing plan labels where a new template fits an existing
   category; add new entries only for genuinely new signals. Example (Mongo):
   `{"label":"Slow queries","kql":"attr.durationMillis:*","project":"t.$date,attr.durationMillis,msg","jq":"select((.attr.durationMillis//0)>100)","method":"project+jq"}`.
   Example (vLLM):
   `{"label":"Memory warnings","kql":"level:WARNING","project":"timestamp,level,message","grep":"memory|OOM|KV","method":"project+grep"}`.

   Write the result as valid JSON to `/tmp/logtype-new-class.json` with this shape,
   then validate and store (merge into the base for GROWTH, fresh for NEW):
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
   "$CACHE" get "$APP_KEY" > /tmp/logtype-classification.json   # merged/full plan for step 7
   ```
   Shape (templates = ONLY the templates you classified — the diff-emitted set):
   ```
   {
     "schema": {"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>","payload":["<leaf>",...]},
     "taxonomy": [{"category":"<name>","description":"<one line>"}],
     "templates": [{"logtype":"<template>","category":"<category>"}],
     "query_plan": [{"label":"...","kql":"...","project":"...","grep":"...","jq":"...","method":"..."}]
   }
   ```
   Include EVERY template you were given (the diff-emitted set) in `templates` —
   for GROWTH that is only the new templates (merged with the base later); for NEW
   it is all of them. Paste logtypes **verbatim** from the `stats.logtypes`
   NDJSON (same whitespace and `{var}`/`<*>` placeholders) or GROWTH detection
   will miss on a future run. Use the discovered field names verbatim in
   `kql`/`project`. Remember: the message field is a clp-string —
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
  `templates`, and `query_plan`, plus `app_key`, `classified_at`, and
  `grown_from` (set when the entry was produced by an incremental grow).
- **Dynamic update when the archive grows.** `logtype-cache diff` returns one of:
  - **UPTODATE** — template set unchanged since the cached classification; reuse
    it (no classifying). Verify the cached `schema` matches the current archive
    (one sample record); if not, reclassify as NEW.
  - **GROWTH** — a cached entry's template set is a subset of the current set
    (the archive grew from it). Classify ONLY the new templates and `put-merged`
    into that base entry (templates/taxonomy/query_plan unioned; `grown_from`
    recorded). Existing templates keep their cached categories.
  - **NEW** — no compatible base (first capture of this app, or a different
    app). Classify all templates and store fresh.
  Re-analyzing a growing archive thus costs only the classification of the newly
  added templates, not the whole dictionary.
- Inspect: `logtype-cache count --logtypes-file F`, `logtype-cache diff --logtypes-file F`,
  `logtype-cache list` (shows `grown_from` lineage), `logtype-cache show <APP_KEY>`.
- Caveat: GROWTH detection requires the cached entry's `templates[].logtype` to
  use the **exact** `stats.logtypes` strings (same whitespace and
  `{var}`/`<*>` placeholders). Paste logtypes verbatim from the `stats.logtypes`
  NDJSON, not a re-templatized or stripped form, or the subset match can miss.

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