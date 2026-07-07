---
name: clpp-search
description: Search a CLP archive with clpp (clp+ / clp-s --experimental) KQL — the shape() and decompose() functions and decomposed-query projections. Use this when the user mentions clp+, clpp, or clp-s experimental, or asks for log shapes / decomposed queries. For plain (non-experimental) KQL search, use the search skill instead.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"]
---

# clpp search

Search a CLP archive with the **experimental** (clpp / clp+) KQL layer active —
`--experimental` — which unlocks the `shape()` and `decompose()` functions and
decomposed-query projections. Stable KQL runs unchanged under the flag.

Use only the plugin wrapper. Do not call bare `clp-s`.

**When to use this skill:** the user says "clp+", "clpp", "clp-s experimental",
"experimental search", "log shapes", "shape()", or "decompose()", or asks to
search a clpp archive. Otherwise use the stable `search` skill.

## Read the shared references

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/clpp-shared.md` — the clpp
  additions: `shape()`/`decompose()` semantics, projection output-key nesting,
  the `--experimental` flag, `--clp-s-bin` note.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/shared-search.md` — the common
  KQL syntax and tips (wildcards, time-range flags, array dot-notation, semantic
  search, `--projection`, counting with `grep -c '^{`).

## Search

Pass `--experimental` to activate the clpp KQL layer. Without it, `shape()` and
`decompose()` are treated as plain text rather than functions.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  /tmp/archive 'shape(message): "*error*"'
```

Project the shape string or the decomposed view of a column:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --projection 'shape(message)' /tmp/archive '*'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --projection 'decompose(message)' /tmp/archive '*'
```

Combine experimental filters with stable KQL and time-range flags:
`shape(message): "*error*" AND level:error`, plus `--tge`/`--tle` for time
windows. See `clpp-shared.md` for output-key nesting and `shared-search.md` for
the full KQL reference.