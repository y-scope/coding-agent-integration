---
name: decompress
description: Decompress a local CLP archive directory into a selected output directory.
---

# Decompress

Use only:

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress ARCHIVES_DIR OUTPUT_DIR
```

If installed elsewhere, resolve the same wrapper from that plugin root.

Rules:

- Accept the top-level archive directory printed by compression, or the inner
  `clp-s` archive directory. Prefer the top-level path.
- Ask for `ARCHIVES_DIR` and `OUTPUT_DIR` if missing.
- Avoid broad output locations such as `/`, home, `~/.claude`, or `~/.codex`.
- Do not expose network/S3 auth, MongoDB metadata output, reducers, indexing,
  conversion, search, or arbitrary `clp-s` passthrough.
- Report the printed `Output dir`.

Examples:

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress \
  --ordered \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```
