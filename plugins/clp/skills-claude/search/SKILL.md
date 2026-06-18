---
name: search
description: Search local CLP archives with KQL (including semantic search).
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"]
---

# Search

Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP
commands/options.

## Rules

- Search wrapper:
  `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVES_DIR 'KQL_QUERY'`
- Semantic search uses `semantic("query")` in KQL for natural-language search.
  The wrapper health-checks the embedding service before search.
- Semantic flags: `--semantic-endpoint`, `--semantic-top-k`, `--semantic-threshold`, `--embedding-batch-size`.
- Default semantic endpoint: `https://embedding.yscope.ai/v1/similarity`. Override with `--semantic-endpoint` or `CLP_SEMANTIC_ENDPOINT`.
- Use single quotes around KQL in shell commands.
- Numeric comparisons use infix syntax: `durationMs >= 30000`.
- The wrapper accepts a top-level `Archives dir` printed by compression OR the
  inner `clp-s` archive directory; the inner one is resolved automatically.
- Allowed search controls: `--tge`, `--tle`, `--ignore-case`, `--archive-id`,
  `--project`, `--projection`, `--semantic-endpoint`, `--semantic-top-k`,
  `--semantic-threshold`, `--embedding-batch-size`.

For session-log workflows (list → compress → search), use the
`claude-code-trajectory` skill instead of this one.

## KQL Syntax

| Concept | Correct | Wrong |
| --- | --- | --- |
| String match | `field:value` | |
| Wildcard | `field:*value*` | |
| Numeric compare | `durationMs >= 30000` | |
| Boolean | `A AND B`, `A OR B`, `NOT A` | |
| Phrase | `"multi word phrase"` | |
| Time range | `--tge EPOCH_MS --tle EPOCH_MS` **flags** | ~~`timestamp >= "2026-06-17"`~~ in KQL |
| Array field path | `message.content.type:tool_use` | ~~`message.content[].type:tool_use`~~ |

**Time filtering is always flags, never KQL.** CLP-S does not support timestamp
comparisons inside KQL. Use `--tge` / `--tle` with Unix epoch milliseconds:

```bash
# Convert ISO timestamp to epoch ms:
python3 -c "from datetime import datetime, timezone; print(int(datetime(2026,6,17,8,23,57,tzinfo=timezone.utc).timestamp()*1000))"
# Then pass as flags:
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --tge 1750150637000 --tle 1750150639000 ARCHIVE '*'
```

**Array fields use dot notation without index brackets.** Records are stored
with each array item flattened; use `field.subfield:value`, never `field[].subfield:value`.

## Semantic Search

Use `semantic("natural language query")` in KQL to find log events whose
logtype is semantically similar to the query, even when exact keywords differ.
The wrapper health-checks the embedding service before running a semantic
search; if the service is unavailable, the search fails with a clear error.

### Endpoints

Prefer the local embedding server when it is running; fall back to the remote
endpoint:

| Endpoint | When to use |
| --- | --- |
| `http://localhost:8080` | Local server — lower latency, preferred |
| `http://tor-1.similarity.yscope.ai` | Remote fallback |

Check localhost first, then fall back:

```bash
if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
  SEMANTIC_ENDPOINT=http://localhost:8080
else
  SEMANTIC_ENDPOINT=http://tor-1.similarity.yscope.ai
fi
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --semantic-endpoint "$SEMANTIC_ENDPOINT" \
  ARCHIVE 'semantic("query")'
```

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  /tmp/session-archive \
  'semantic("slow database queries")'

"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --semantic-top-k 10 \
  /tmp/session-archive \
  'semantic("authentication failures")'

"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --semantic-threshold 0.5 \
  /tmp/session-archive \
  'semantic("timeout errors")'

"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --semantic-endpoint https://custom.example.com/v1/similarity \
  /tmp/session-archive \
  'semantic("errors")'
```

Combine `semantic(...)` with regular KQL using `AND`, e.g.
`'semantic("errors") AND level:error'`.

## Efficiency Patterns

CLP searches the compressed archive without decompressing unmatched records.
Prefer KQL predicates over fetching all records and post-filtering.

**Count matches without fetching full records:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'KQL' | grep -c '^{'
```

**Compound KQL to avoid multiple round-trips:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'field1:value AND field2 >= 1000'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'name:Edit OR name:Write OR name:MultiEdit'
```

**Fetch only needed fields with `--project`:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --project timestamp --project durationMs \
  ARCHIVE 'subtype:turn_duration'
```

**Zoom into a time window with `--tge`/`--tle` (epoch milliseconds):**
```bash
# Convert: python3 -c "from datetime import datetime; print(int(datetime(2026,6,17,8,23,57).timestamp()*1000))"
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --tge T1 --tle T2 ARCHIVE 'KQL'
```
