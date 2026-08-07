---
name: compress
description: Compress one selected session JSONL file into a searchable CLP archive directory (stable, non-experimental clp-s). Use clpp-compress for clp+/clpp/clp-s --experimental compression with a parsing spec.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"]
---

# Compress

Compress one selected session JSONL file into a searchable CLP archive using the
**stable** (non-experimental) clp-s path.

Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP
commands/options.

**When to use this skill:** plain (non-experimental) compression. If the user
mentions clp+, clpp, or clp-s experimental — or wants a parsing spec, log shapes,
or decomposed queries — use the `clpp-compress` skill instead.

**See also:** `clpp-compress` for the experimental path, and `dev` for pointing
the wrapper at a locally-built `clp-s`.

## Read the shared reference

The full compress workflow and rules live in:

`${CLAUDE_PLUGIN_ROOT}/skills-claude/references/shared-compress.md`

That covers: list sessions → choose an `IDX` → compress → report stats, the
session-root and archive-root defaults, stable compress options, and the
useful commands (`--show-archives-root`, `--set-archives-root`, `--dry-run`).

## Quick start

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index <IDX> \
  --timestamp-key timestamp
```

After compression, report: raw input bytes, archive bytes, compression ratio,
file size reduction, archives dir, selected session, archive metadata. Use the
printed top-level `Archives dir` for search/decompress — wrappers resolve the
inner `clp-s` archive directory automatically.