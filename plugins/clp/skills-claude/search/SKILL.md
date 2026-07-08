---
name: search
description: Search local CLP archives with KQL including semantic search (stable, non-experimental clp-s). Use clpp-search for clp+/clpp/clp-s --experimental search with shape()/decompose().
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"]
---

# Search

Search a local CLP archive with KQL using the **stable** (non-experimental)
clp-s path.

Use only the plugin wrapper. Do not call bare `clp-s`.

**When to use this skill:** plain (non-experimental) KQL / semantic search. If
the user mentions clp+, clpp, or clp-s experimental — or wants `shape()` /
`decompose()` / log shapes / decomposed-query projections — use the
`clpp-search` skill instead.

**See also:** `clpp-search` for the experimental path, and `dev` for pointing
the wrapper at a locally-built `clp-s`.

For session-log workflows (list → compress → search), use the
`claude-code-trajectory` skill instead of this one.

## Read the shared reference

The full KQL syntax and tips live in:

`${CLAUDE_PLUGIN_ROOT}/skills-claude/references/shared-search.md`

That covers: the KQL table, the wildcard-substring rule (literals match whole
values; substrings need explicit `*`), the time-range-flag gotcha, array
dot-notation, semantic search, `--projection`, and counting with `grep -c '^{`.

## Quick start

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVES_DIR 'KQL_QUERY'
```

The wrapper accepts the top-level `Archives dir` printed by compression or the
inner clp-s archive directory (resolved automatically). Use single quotes around
KQL. Sensible defaults (embedding endpoint, local cache) are built in; pass extra
clp-s flags only if the user asks for something specific.