# Shared search reference

Shared by the `search` (stable clp-s) and `clpp-search` (clpp / `--experimental`)
skills. Read this for the KQL syntax and search tips; read `clpp-shared.md` for
the clpp-only `shape()`/`decompose()` functions and decomposed-query projections.

Use only the plugin wrapper. Do not call bare `clp-s`.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVES_DIR 'KQL_QUERY'
```

The wrapper accepts the top-level `Archives dir` printed by compression or the
inner clp-s archive directory (resolved automatically). Use single quotes around
KQL. Sensible defaults (embedding endpoint, local cache) are built in; pass extra
clp-s flags only if the user asks for something specific.

For session-log workflows (list → compress → search), use the
`claude-code-trajectory` skill instead of this one.

## KQL

| Concept | Syntax |
| --- | --- |
| String match | `field:value` |
| Wildcard | `field:*value*` |
| Numeric compare | `durationMs >= 30000` |
| Boolean | `A AND B`, `A OR B`, `NOT A` |
| Phrase | `"multi word phrase"` |

### Wildcards are required for substring matches

A literal term matches only the **entire** field value. To match a substring,
add explicit wildcards:

- `hello*` — match `hello` at the **start** of a value (e.g. `hello world`).
- `*hello*` — match `hello` **anywhere** (e.g. `abc hello 123`).
- `message: "*job*"` — quoted wildcard form, as in the clp-s docs.

A bare `INFO` (no wildcards) returns 0 even when `INFO` appears in the data — that
is correct, not a bug. Always add `*` around the substring.

### Two gotchas

- **Time ranges are flags, never KQL.** Use `--tge EPOCH_MS` / `--tle EPOCH_MS`:
  `python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"`
- **Array fields use dot notation.** Use `message.content.type:tool_use`, not
  `message.content[].type:tool_use`.

## Semantic search

Use `semantic("natural language query")` in KQL to find log events whose logtype
is semantically similar to the query, even when exact keywords differ. No flags
needed — the wrapper auto-selects a working endpoint and shares a local cache
across sessions. Combine with regular KQL using `AND`.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("slow database queries")'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("errors") AND level:error'
```

## Tips

- Project only the columns you need to limit data returned: when you know which
  fields matter, pass `--project COLUMN` (repeatable), e.g.
  `--project timestamp --project level`. Omit it only when you need the full
  record.
- Count matches without fetching full records:
  `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'KQL' | grep -c '^{`
- Prefer one compound KQL query over several: `'field1:value AND field2 >= 1000'`
- Point at a local build with `--clp-s-bin PATH` or `CLP_S_BIN` (see the `dev`
  skill).