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

## Semantic Search

Use `semantic("natural language query")` in KQL to find log events whose
logtype is semantically similar to the query, even when exact keywords differ.
The wrapper health-checks the embedding service before running a semantic
search; if the service is unavailable, the search fails with a clear error.

Examples:

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
