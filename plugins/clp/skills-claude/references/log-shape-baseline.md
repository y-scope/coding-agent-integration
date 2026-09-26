# Log shape baseline reference (`analyze-logs`, general route)

Read this only when needed: the bootstrap misbehaves (empty dump, missing frequencies), the user drills into individual templates, or you need the retrieval/semantic patterns. The happy path never needs this file — the `clp bootstrap` script encapsulates the dump and the cache probe.

## stats.log_shapes details

- `stats.log_shapes` dumps the log shape dictionary: one raw JSON object per line, `{"archive_id":"...","count":N,"id":N,"shape":"..."}`. `count` is how many values in that archive carried the template, stored by clp-s at compression time; it is `null` only for archives compressed before clp-s stored these counts. `id` numbers the templates within one archive, so aggregate across archives by template, never by `id`. Shapes mark variables as `%int%`/`%str%`/`%float%` on regular archives and `%rule.name%` on clpp/`--experimental` archives (older clp-s builds emitted raw placeholder bytes instead). `clp shape-cache normalize` detects the encoding per line and renders all of them to the canonical `{"log_shape":"...<*>..."}` NDJSON used by this skill and the cache, and `clp shape-cache freqs` sums the counts per template. Works on structurized text archives and native-JSON archives alike (log shapes come from the message field).
- The search wrapper adds the required `--experimental` flag automatically and rejects the legacy `stats.logtypes` spelling (shapes-API binaries silently return nothing for it).
- You **cannot** filter a stats query by substring (`stats.log_shapes:foo` is not valid); it always dumps the whole dictionary. Filter downstream:
  ```bash
  python3 - failed /tmp/log-shape-freqs.ndjson <<'PY'
  import json, re, sys
  pattern = re.compile(sys.argv[1], re.I)
  for line in open(sys.argv[2]):
      log_shape = json.loads(line)["log_shape"]
      if pattern.search(log_shape):
          print(log_shape)
  PY
  ```
- Per-template frequencies come from those stored counts, with no scan of the records. The bootstrap writes them to `/tmp/log-shape-freqs.ndjson` (`{"count":N,"hash":"...","length":N,"log_shape":"..."}`, most frequent first). Its `log_shape` is the template's first `MAX_CHARS` characters, all of it when `length` is no longer: the bootstrap stores that much per template in the cache database, so a later analysis of the same archive reads the counts back instead of dumping the dictionary. The full text is in `/tmp/log-shapes.ndjson` when the bootstrap dumped the dictionary (`SHAPES_SOURCE=dump`; `--dump` forces it). By hand, from a dump:
  ```bash
  CLP="${CLAUDE_PLUGIN_ROOT}/bin/clp"
  "$CLP" search ARCHIVE 'stats.log_shapes' | "$CLP" shape-cache freqs
  ```
  `freqs` exits 1 when any archive has `null` counts. Report frequencies as unavailable for such an archive and suggest recompressing it; do not approximate them by projecting and counting messages.
- An empty `stats.log_shapes` dump means the clp-s binary predates the shapes API. The bootstrap then exits 1; reinstalling the plugin fixes it.

## The message field needs the same wildcard rule as any field

The message field (`message` structurized, `msg` native Mongo, …) is stored as a CLP-string (log shape + encoded variables — what makes `stats.log_shapes` and the compression work). That storage is irrelevant to searching it: `<message>:term` is an exact match, same as `<field>:term` on any field per `shared-search.md`, so it correctly returns 0 unless a message equals exactly `term`. Exact match is faster, so prefer it whenever you know the full field value (e.g. a scalar like severity or logger); wildcard only when you need a substring match — `<message>:"*term*"` — which is what message content almost always needs, since it's free text, not an enumerable value. The log-shape-baseline approach still matters even with wildcard search available: the dictionary dump gives the full template vocabulary up front, so queries can be built from real templates instead of guessed keywords. Semantic search (`semantic("…")`) also searches the log shapes directly and is a good complement to wildcard search for concept-shaped questions.

## Retrieve & count records of a template

Prefer a direct KQL wildcard search on the message field for the template's distinctive static text — it's fast, not a full-archive project+grep pass:

```bash
ARCHIVE=<archive-dir>
CLP="${CLAUDE_PLUGIN_ROOT}/bin/clp"
MSG=<message-field>
# example records of one template, with timestamp + severity (--limit stops the scan early):
"$CLP" search --limit 20 --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '<message>:"*Distinctive Static Text*"'
# count of that template — native --count, not --projection | grep -c:
"$CLP" search --count "$ARCHIVE" '<message>:"*Distinctive Static Text*"'
```

Always quote the wildcard value — `field:"*value*"` not `field:*value*`. The `*` wildcard works inside quotes; without them, any space in the value causes clp-s to treat the term as natural language and trigger the semantic fallback (which errors when no endpoint is active). Single-word values work either way, but quoting unconditionally is the safe habit.

`--count` counts inside the engine, so use it for every "how many records match X" question, including filters that match most of the archive (e.g. all INFO records); there is no need to count a rare complement and subtract. `--unique FIELD` lists a field's distinct values but still scans the matching records. See `shared-search.md` for both.

Avoid `grep` over a full record scan: it is O(records), and messages can be large, while KQL search runs inside the engine. A keyword alternation is not a reason to grep; OR the wildcards in one query:

```bash
"$CLP" search --projection <timestamp>,<severity>,$MSG "$ARCHIVE" \
  '<message>:"*foo*" OR <message>:"*bar*" OR <message>:"*baz*"'
```

Fall back to project + a filter only when the distinctive text needs real regex features KQL wildcards can't express (anchors, character classes, backreferences):

```bash
"$CLP" search --projection <timestamp>,<severity>,$MSG "$ARCHIVE" '*' \
  | python3 - "$MSG" <<'PY'
  import json, re, sys
  pattern = re.compile("DistinctiveStaticText", re.I)
  for line in sys.stdin:
      if line.startswith("{"):
          value = str(json.loads(line).get(sys.argv[1], ""))
          if pattern.search(value):
              print(line, end="")
  PY
```

Narrow with a working scalar field first when you can — `<severity>:` and `<logger>:` are also searchable, and combine with the message wildcard in one compound query:

```bash
"$CLP" search --projection <timestamp>,<severity>,$MSG "$ARCHIVE" \
  '<severity>:WARNING AND <message>:"*Static Text*"'
```

Rules of thumb: pick the rarest distinctive static text (never a variable or a stopword); for all-template frequencies read `/tmp/log-shape-freqs.ndjson`, not per-template greps.

## Analysis patterns

- **Count per template (all templates):** `head -20 /tmp/log-shape-freqs.ndjson`, or filter a subset in python:
  ```bash
  python3 - error /tmp/log-shape-freqs.ndjson <<'PY'
  import json, re, sys
  pattern = re.compile(sys.argv[1], re.I)
  for line in open(sys.argv[2]):
      record = json.loads(line)
      if pattern.search(record["log_shape"]):
          print(f'{record["count"]}\t{record["log_shape"]}')
  PY
  ```
- **Count for a group of templates:** sum `count` over the templates matching the group's static text. This is O(distinct templates), not a record scan, so prefer it to `--count` whenever the group is defined by message text alone:
  ```bash
  python3 - 'compact|flush|memtable|ingest' /tmp/log-shape-freqs.ndjson <<'PY'
  import json, re, sys
  pattern = re.compile(sys.argv[1], re.I)
  total = 0
  for line in open(sys.argv[2]):
      record = json.loads(line)
      if pattern.search(record["log_shape"]):
          total += record["count"]
  print(total)
  PY
  ```
- **Time span:** `timeRange` in the archive's `.yscope-clp-archive.json` (`begin`/`end`, the earliest and latest timestamp across every record, recorded at compression). When it is absent or null, the span is unavailable; never estimate it from fetched records.
- **Scoped semantic:** `"${CLAUDE_PLUGIN_ROOT}/bin/clp" search ARCHIVE 'semantic("...") AND <severity>:<value>'`
- **Filter the baseline by template text:**
  ```bash
  python3 - 'error|fail|exception' /tmp/log-shape-freqs.ndjson <<'PY'
  import json, re, sys
  pattern = re.compile(sys.argv[1], re.I)
  for line in open(sys.argv[2]):
      log_shape = json.loads(line)["log_shape"]
      if pattern.search(log_shape):
          print(log_shape)
  PY
  ```

## When to still use semantic search

With a log shape baseline, semantic search is not the default exploratory tool — the baseline already tells you what exists. The insight pass still runs one mandatory scoped semantic cross-check (reported in the "Semantic Search Coverage" section, with empty/no-hit or meaningless results dropped). Beyond that mandatory pass, use `semantic()` only for:

| Situation | Why |
| --- | --- |
| A template's category is ambiguous | `semantic("…")` votes on intent |
| Grouping similar templates | cluster the small dictionary conceptually |
| The user's question is conceptual, not template-shaped | "anything about reliability?" |
| Confirming a template-classification miss | run semantic, diff vs the baseline |

Combine with a scalar KQL field for precision: `semantic("…") AND <severity>:<value>`. Semantic flags (only active when the query contains `semantic()`; the wrapper auto-selects the endpoint):

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Nearest log shapes; raise 8–10 for recall, lower 2–3 for precision |
| `--semantic-threshold T` | 0.3 | Similarity floor 0.0–1.0; raise to 0.5+ for precision |
