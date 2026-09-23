# Logtype baseline reference (logtype-insights)

Read this only when needed: the bootstrap misbehaves (empty dump, missing frequencies), the user drills into individual templates, or you need the retrieval/semantic patterns. The happy path never needs this file — the `logtype-insights-bootstrap` script encapsulates the dump and the cache probe.

## stats.log_shapes details

- `stats.log_shapes` dumps the logtype dictionary: one raw JSON object per line, `{"archive_id":"...","count":N,"id":N,"shape":"..."}`. `count` is how many values in that archive carried the template, stored by clp-s at compression time; it is `null` only for archives compressed before clp-s stored these counts. `id` numbers the templates within one archive, so aggregate across archives by template, never by `id`. Shapes mark variables as `%int%`/`%str%`/`%float%` on regular archives and `%rule.name%` on clpp/`--experimental` archives (older clp-s builds emitted raw placeholder bytes instead). `logtype-cache normalize` detects the encoding per line and renders all of them to the canonical `{"logtype":"...<*>..."}` NDJSON used by this skill and the cache, and `logtype-cache freqs` sums the counts per template. Works on structurized text archives and native-JSON archives alike (logtypes come from the message field).
- The search wrapper adds the required `--experimental` flag automatically and rejects the legacy `stats.logtypes` spelling (shapes-API binaries silently return nothing for it). It also prints archive-metadata header lines to stdout — always filter with `grep '^{'` before jq (the repo-wide idiom).
- You **cannot** filter a stats query by substring (`stats.log_shapes:foo` is not valid); it always dumps the whole dictionary. Filter downstream:
  ```bash
  jq -r 'select(.logtype|test("failed";"i")) | .logtype' /tmp/logtypes.ndjson
  ```
- Per-template frequencies come from those stored counts, with no scan of the records. The bootstrap writes them to `/tmp/logtype-freqs.ndjson` (`{"count":N,"logtype":"..."}`, most frequent first); by hand:
  ```bash
  clp-s-search-kql ARCHIVE 'stats.log_shapes' | grep '^{' | logtype-cache freqs
  ```
  `freqs` exits 1 when any archive has `null` counts. Report frequencies as unavailable for such an archive and suggest recompressing it; do not approximate them by projecting and counting messages.
- An empty `stats.log_shapes` dump means the clp-s binary predates the shapes API. The bootstrap then exits 1; reinstalling the plugin fixes it.

## The message field needs the same wildcard rule as any field

The message field (`message` structurized, `msg` native Mongo, …) is stored as a CLP-string (logtype template + encoded variables — what makes `stats.log_shapes` and the compression work). That storage is irrelevant to searching it: `<message>:term` is an exact match, same as `<field>:term` on any field per `shared-search.md`, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so prefer it whenever you know the full field value (e.g. a scalar like severity or logger); wildcard only when you need a substring match — `<message>:*term*` — which is what message content almost always needs, since it's free text, not an enumerable value. The logtype-baseline approach still matters even with wildcard search available: the dictionary dump gives the full template vocabulary up front, so queries can be built from real templates instead of guessed keywords. Semantic search (`semantic("…")`) also searches the logtypes directly and is a good complement to wildcard search for concept-shaped questions.

## Retrieve & count records of a template

Prefer a direct KQL wildcard search on the message field for the template's distinctive static text — it's fast, not a full-archive project+grep pass:

```bash
ARCHIVE=<archive-dir>
SEARCH="${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql"
MSG=<message-field>
# example records of one template, with timestamp + severity (--limit stops the scan early):
"$SEARCH" --limit 20 --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '<message>:"*Distinctive Static Text*"'
# count of that template — native --count, not --projection | grep -c:
"$SEARCH" --count "$ARCHIVE" '<message>:"*Distinctive Static Text*"'
```

Always quote the wildcard value — `field:"*value*"` not `field:*value*`. The `*` wildcard works inside quotes; without them, any space in the value causes clp-s to treat the term as natural language and trigger the semantic fallback (which errors when no endpoint is active). Single-word values work either way, but quoting unconditionally is the safe habit.

`--count` counts inside the engine, so use it for every "how many records match X" question, including filters that match most of the archive (e.g. all INFO records); there is no need to count a rare complement and subtract. `--unique FIELD` lists a field's distinct values but still scans the matching records. See `shared-search.md` for both.

Avoid `grep`/`jq` over a full record scan: it is O(records), and messages can be large, while KQL search runs inside the engine. A keyword alternation is not a reason to grep; OR the wildcards in one query:

```bash
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" \
  '<message>:"*foo*" OR <message>:"*bar*" OR <message>:"*baz*"'
```

Fall back to project + grep/jq only when the distinctive text needs real regex features KQL wildcards can't express (anchors, character classes, backreferences):

```bash
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '*' \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("DistinctiveStaticText";"i"))'
```

Narrow with a working scalar field first when you can — `<severity>:` and `<logger>:` are also searchable, and combine with the message wildcard in one compound query:

```bash
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" \
  '<severity>:WARNING AND <message>:"*Static Text*"'
```

Rules of thumb: pick the rarest distinctive static text (never a variable or a stopword); for all-template frequencies read `/tmp/logtype-freqs.ndjson`, not per-template greps.

## Analysis patterns

- **Count per template (all templates):** `head -20 /tmp/logtype-freqs.ndjson`, or `jq -r 'select(.logtype|test("error";"i")) | "\(.count)\t\(.logtype)"' /tmp/logtype-freqs.ndjson` for a subset.
- **Count for a group of templates:** sum `count` over the templates matching the group's static text. This is O(distinct templates), not a record scan, so prefer it to `--count` whenever the group is defined by message text alone:
  ```bash
  jq -s '[.[] | select(.logtype | test("compact|flush|memtable|ingest";"i")) | .count] | add' /tmp/logtype-freqs.ndjson
  ```
- **Time span:** project the timestamp field, `head -n 1` / `tail -n 1` (records are chronological; do NOT sort); `--tge`/`--tle` only if the timestamp is a real epoch (native JSON).
- **Scoped semantic:** `clp-s-search-kql ARCHIVE 'semantic("...") AND <severity>:<value>'`
- **Filter the baseline with jq:**
  ```bash
  jq -r 'select(.logtype|test("error|fail|exception";"i")).logtype' /tmp/logtypes.ndjson
  ```

## When to still use semantic search

With a logtype baseline, semantic search is not the default exploratory tool — the baseline already tells you what exists. The insight pass still runs one mandatory scoped semantic cross-check (reported in the "Semantic Search Coverage" section, with empty/no-hit or meaningless results dropped). Beyond that mandatory pass, use `semantic()` only for:

| Situation | Why |
| --- | --- |
| A template's category is ambiguous | `semantic("…")` votes on intent |
| Grouping similar templates | cluster the small dictionary conceptually |
| The user's question is conceptual, not template-shaped | "anything about reliability?" |
| Confirming a template-classification miss | run semantic, diff vs the baseline |

Combine with a scalar KQL field for precision: `semantic("…") AND <severity>:<value>`. Semantic flags (only active when the query contains `semantic()`; the wrapper auto-selects the endpoint):

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Nearest logtypes; raise 8–10 for recall, lower 2–3 for precision |
| `--semantic-threshold T` | 0.3 | Similarity floor 0.0–1.0; raise to 0.5+ for precision |
