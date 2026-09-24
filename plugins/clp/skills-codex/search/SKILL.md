---
name: search
description: Search local CLP archives with KQL (including semantic search).
---

# Search

Use only the plugin wrapper from `~/.codex/marketplaces/yscope/plugins/clp` (or resolve the same `bin/` wrappers from the install root). Do not call bare `clp-s`.

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVES_DIR 'KQL_QUERY'
```

The wrapper accepts the top-level `Archives dir` printed by compression or the inner clp-s archive directory (resolved automatically). Use single quotes around KQL. A sensible default embedding endpoint is built in; pass extra clp-s flags only if the user asks for something specific.

For session-log workflows (list → compress → search), use the `codex-trajectory` skill instead of this one.

## KQL

| Concept | Syntax |
| --- | --- |
| String match | `field:value` |
| Wildcard (substring) | `field:"*value*"` — quote it, even for one word |
| Numeric compare | `durationMs >= 30000` |
| Boolean | `A AND B`, `A OR B`, `NOT A` |
| Phrase | `"multi word phrase"` |

Two gotchas:
- **Time ranges are flags, never KQL.** Use `--tge EPOCH_MS` / `--tle EPOCH_MS`: `python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"`
- **Array fields use dot notation.** Use `payload.type:function_call`, not `payload[].type:function_call`.

## Semantic search

Use `semantic("natural language query")` in KQL to find log events whose log shape is semantically similar to the query, even when exact keywords differ. No flags needed — the wrapper auto-selects a working endpoint. Combine with regular KQL using `AND`.

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("slow database queries")'
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("errors") AND payload.type:function_call'
```

## Tips

- Project only the columns you need to limit data returned: when you know which fields matter, pass `--projection COLUMNS` (comma-separated; repeatable), e.g. `--projection timestamp,payload.name`. Omit it only when you need the full record.
- When a few example records are enough, pass `--limit N`. It always caps the output; it saves time only when the limit is reached before later schema tables or archives are read, because clp-s decompresses a whole table before returning its first record. Which N come back is unspecified (not the earliest or latest), so never use it for counts.
- Count matches with `--count`, never `--projection ... | grep -c '^{'`. It counts inside the engine without serializing any record, so its cost barely depends on how many records match (measured ~6-7s whether a filter matched 92 or 16.5M records of a 16.5M-record archive). It prints one `{"archive_id":...,"count":N}` line per archive, and nothing at all when zero records match; treat empty output as a real zero. Use `grep -c '^{'` only for an incidental count from a `--projection` output you are already reading for another reason.
  ```bash
  ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql --count ARCHIVE 'KQL'
  ```
- List a field's distinct values among matches with `--unique FIELD` instead of projecting and running `sort | uniq`. It still scans the matching records (measured ~84s for a low-cardinality field over 16.5M records), so get per-value totals with one `--count` query per value.
- `--count`, `--unique`, and `--limit` are mutually exclusive, and `--count`/`--unique` cannot be combined with `--projection`.
- Prefer one compound KQL query over several: `'field1:value AND field2 >= 1000'`. A keyword alternation is not a reason to grep: OR the wildcards in the query itself, `message:"*a*" OR message:"*b*" OR message:"*c*"`, which still runs inside the search engine. Pipe to `grep`/`jq` only when the match needs real regex features (anchors, character classes, backreferences).
