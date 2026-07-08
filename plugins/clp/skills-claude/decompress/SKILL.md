---
name: decompress
description: Decompress a local CLP archive directory into a selected output directory.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"]
---

# Decompress

Use only:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" ARCHIVES_DIR OUTPUT_DIR
```

Rules:

- Accept the top-level `Archives dir` printed by compression, or the inner
  `clp-s` archive directory. Prefer the top-level path.
- Ask for `ARCHIVES_DIR` and `OUTPUT_DIR` if missing.
- Avoid broad output locations such as `/`, home, `~/.claude`, or `~/.codex`.
- Do not expose network/S3 auth, MongoDB metadata output, reducers, indexing,
  conversion, search, or arbitrary `clp-s` passthrough.
- Report the printed `Output dir`.

clpp (clp+ / `--experimental`) archives are decompressed the same way: the
wrapper auto-detects them (by their `parsing_specification` / `parent_rule_shapes`
marker files) and passes `--experimental` for you — no extra flags needed. clpp
archives require `--experimental` to open, so this auto-detection is required;
pass `--no-experimental` only to force stable mode. See `clpp-shared.md` for
clpp background.

Examples:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
  --ordered \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```
