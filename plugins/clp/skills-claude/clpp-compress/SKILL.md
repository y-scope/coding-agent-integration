---
name: clpp-compress
description: Compress a session JSONL into a CLP archive with clpp (clp+ / clp-s --experimental) — decomposed queries and log shapes via a log-surgeon parsing specification. Use this when the user mentions clp+, clpp, or clp-s experimental, or asks for log shapes / decomposed queries. For plain (non-experimental) compression, use the compress skill instead.
allowed-tools:
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"
---

# clpp compress

Compress with CLP's **experimental** (clpp / clp+) layer — the `--experimental`
flag, which decomposes each `message` into structured tokens using a log-surgeon
**parsing specification**, enabling log-shape queries and `shape()`/`decompose()`
search.

Use only the plugin wrappers. Do not call bare `clp-s`.

**When to use this skill:** the user says "clp+", "clpp", "clp-s experimental",
"experimental compression", "log shapes", or "decomposed queries", or asks to
compress with a parsing spec. Otherwise use the stable `compress` skill.

## Read the shared references

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/clpp-shared.md` — the clpp
  additions (the `--experimental` / `--parsing-specification` pairing, spec-matching
  rules, `--clp-s-bin` note, decompress caveat).
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/shared-compress.md` — the common
  compress workflow: list sessions, choose an `IDX`, compress, report stats.

## Compress

`--experimental` and `--parsing-specification` **must both be passed** (the
clp-s binary rejects either alone). The wrapper enforces the same contract.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
  --session-file ~/.claude/projects/<…>/session.jsonl \
  --experimental --parsing-specification /path/to/parsing-spec.txt \
  --timestamp-key timestamp
```

The parsing spec **must match the structure of the input**. The benchmark specs
under `./benchmark/parsing-specs/` in a CLP source checkout are tuned for the
hive dataset, not for Claude/Codex session JSON — supply a session-appropriate
spec when compressing sessions.

After compression, report the same stats as the `compress` skill: raw input
bytes, archive bytes, compression ratio, file size reduction, archives dir, and
archive metadata. The wrapper records the full experimental command in the
archive metadata JSON.

For the session-list/choose workflow and all stable compress options, follow
`shared-compress.md`. To point at a local clpp build, see `clpp-shared.md` and
the `dev` skill.