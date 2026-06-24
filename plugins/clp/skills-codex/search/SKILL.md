---
name: search
description: Search local CLP archives with KQL (including semantic search).
---

# Search

Use only the plugin wrapper from `~/.codex/marketplaces/yscope/plugins/clp` (or
resolve the same `bin/` wrappers from the install root). Do not call bare `clp-s`.

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVES_DIR 'KQL_QUERY'
```

The wrapper accepts the top-level `Archives dir` printed by compression or the
inner clp-s archive directory (resolved automatically). Use single quotes around
KQL. Sensible defaults (embedding endpoint, local cache) are built in; pass extra
clp-s flags only if the user asks for something specific.

For session-log workflows (list → compress → search), use the `codex-trajectory`
skill instead of this one.

## KQL

| Concept | Syntax |
| --- | --- |
| String match | `field:value` |
| Wildcard | `field:*value*` |
| Numeric compare | `durationMs >= 30000` |
| Boolean | `A AND B`, `A OR B`, `NOT A` |
| Phrase | `"multi word phrase"` |

Two gotchas:
- **Time ranges are flags, never KQL.** Use `--tge EPOCH_MS` / `--tle EPOCH_MS`:
  `python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"`
- **Array fields use dot notation.** Use `payload.type:function_call`, not
  `payload[].type:function_call`.

## Semantic search

Use `semantic("natural language query")` in KQL to find log events whose logtype
is semantically similar to the query, even when exact keywords differ. No flags
needed — the wrapper auto-selects a working endpoint and shares a local cache
across sessions. Combine with regular KQL using `AND`.

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("slow database queries")'
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("errors") AND payload.type:function_call'
```

## Tips

- Count matches without fetching full records:
  `~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'KQL' | grep -c '^{'
- Prefer one compound KQL query over several: `'field1:value AND field2 >= 1000'`