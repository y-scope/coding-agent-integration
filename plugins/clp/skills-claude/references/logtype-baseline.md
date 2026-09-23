# Logtype baseline reference (logtype-insights)

Read this only when needed: the bootstrap misbehaves (empty dump, fallback
questions), the user drills into individual templates, or you need the
retrieval/semantic patterns. The happy path never needs this file — the
`logtype-insights-bootstrap` script encapsulates the dump and the cache probe.

## stats.log_shapes details

- `stats.log_shapes` dumps the logtype dictionary: one raw JSON object per
  line, `{"archive_id":"...","count":...,"id":N,"shape":"..."}`. The shape
  string encodes variables in an archive-dependent form — regular archives use
  raw placeholder bytes (0x11 int, 0x12 str, 0x13 float; `count` is null),
  clpp/`--experimental` archives use `%rule.name%` TextShape placeholders
  (`count` is a real integer). `logtype-cache normalize` detects the encoding
  per line and renders both to the canonical `{"logtype":"...<*>..."}` NDJSON
  used by this skill and the cache. Works on structurized text archives and
  native-JSON archives alike (logtypes come from the message field).
- The search wrapper adds the required `--experimental` flag automatically and
  rejects the legacy `stats.logtypes` spelling (shapes-API binaries silently
  return nothing for it). It also prints archive-metadata header lines to
  stdout — always filter with `grep '^{'` before jq (the repo-wide idiom).
- You **cannot** filter a stats query by substring (`stats.log_shapes:foo` is
  not valid); it always dumps the whole dictionary. Filter downstream:
  ```bash
  jq -r 'select(.logtype|test("failed";"i")) | .logtype' /tmp/logtypes.ndjson
  ```
- `stats.log_shapes` gives templates and ids but **not per-template counts on
  regular archives** (`count` is null there; clpp archives carry real counts).
  Get counts with the templatize pass (below), which yields `count \t
  template` in one O(records) scan.

## Templatize fallback (binaries that predate the shapes API)

When `stats.log_shapes` emits no NDJSON, the bootstrap (re-run with
`--message <field>`) builds an approximate baseline by projecting the message
field and templatizing variable runs:

```
sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g'
```

It writes both `/tmp/logtype-freqs.txt` (`count  template`, most frequent
first) and the canonical `/tmp/logtypes.ndjson` (via `logtype-cache
normalize`) so the cache probe and the rest of the workflow work unchanged.
Caveat: templatized strings are approximations — they are stable across runs
of the *same* binary, but need not match byte-exactly the shapes-API logtypes
a newer binary would produce, so a cache entry built on the fallback path may
not GROWTH-match one built on the shapes path (it will re-classify as NEW).

## Searching the message field: exact vs. wildcard

The message field (`message` structurized, `msg` native Mongo, …) is stored as
a CLP-string (logtype template + encoded variables — what makes
`stats.log_shapes` and the compression work), but it follows the same KQL
rule as every other field: `<message>:term` is an **exact** match against the
whole field value, so it correctly returns 0 whenever no message equals just
`term`. To match a substring, wildcard it — `<message>:*term*` — same as
`shared-search.md`'s general wildcard rule. The scalar fields (severity,
logger, payload leaf paths) are also KQL-searchable and narrow faster, since
an exact match on them needs no wildcard. The logtype-baseline approach still
matters even with wildcard search available: the dictionary dump gives the
full template vocabulary up front, so queries can be built from real
templates instead of guessed keywords. Semantic search (`semantic("…")`) also
searches the logtypes directly and is a good complement to wildcard search
for concept-shaped questions.

## Retrieve & count records of a template

Prefer a direct KQL wildcard search on the message field for the template's
distinctive static text — it's fast, not a full-archive project+grep pass:

```bash
ARCHIVE=<archive-dir>
SEARCH="${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql"
MSG=<message-field>
# records of one template, with timestamp + severity:
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '<message>:*DistinctiveStaticText*'
# count of that template:
"$SEARCH" --projection $MSG "$ARCHIVE" '<message>:*DistinctiveStaticText*' | grep -c '^{'
```

Fall back to project + grep/jq only when the distinctive text has characters
KQL's wildcard syntax can't express cleanly (e.g. it needs a regex, not a
substring):

```bash
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '*' \
  | grep '^{' | jq -rc --arg f "$MSG" 'select(.[$f]|test("DistinctiveStaticText";"i"))'
```

Narrow with a working scalar field first when you can — `<severity>:` and
`<logger>:` are also searchable, and combine with the message wildcard in one
compound query:

```bash
"$SEARCH" --projection <timestamp>,<severity>,$MSG "$ARCHIVE" \
  '<severity>:WARNING AND <message>:*StaticText*'
```

Rules of thumb: pick the rarest distinctive static text (never a variable or a
stopword); for all-template frequencies use one templatize pass, not
per-template greps.

## Analysis patterns

- **Count per template (one pass, all templates):**
  ```bash
  clp-s-search-kql --projection <message> ARCHIVE '*' \
    | grep '^{' | jq -r '.<message>' \
    | sed -E 's/\{[^}]+\}/<*>/g; s/\b[0-9]+\b/<*>/g' | sort | uniq -c | sort -rn
  ```
- **Time span:** project the timestamp field, `head -n 1` / `tail -n 1`
  (records are chronological; do NOT sort); `--tge`/`--tle` only if the
  timestamp is a real epoch (native JSON).
- **Scoped semantic:** `clp-s-search-kql ARCHIVE 'semantic("...") AND <severity>:<value>'`
- **Filter the baseline with jq:**
  ```bash
  jq -r 'select(.logtype|test("error|fail|exception";"i")).logtype' /tmp/logtypes.ndjson
  ```

## When to still use semantic search

With a logtype baseline, semantic search is not the default exploratory tool —
the baseline already tells you what exists. The insight pass still runs one
mandatory scoped semantic cross-check (reported in the "Semantic Search
Coverage" section, with empty/no-hit or meaningless results dropped). Beyond
that mandatory pass, use `semantic()` only for:

| Situation | Why |
| --- | --- |
| A template's category is ambiguous | `semantic("…")` votes on intent |
| Grouping similar templates | cluster the small dictionary conceptually |
| The user's question is conceptual, not template-shaped | "anything about reliability?" |
| Confirming a template-classification miss | run semantic, diff vs the baseline |

Combine with a scalar KQL field for precision:
`semantic("…") AND <severity>:<value>`. Semantic flags (only active when the
query contains `semantic()`; the wrapper auto-selects endpoint + local cache):

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Nearest logtypes; raise 8–10 for recall, lower 2–3 for precision |
| `--semantic-threshold T` | 0.3 | Similarity floor 0.0–1.0; raise to 0.5+ for precision |
