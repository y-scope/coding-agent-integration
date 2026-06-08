---
name: decompress
description: Decompress a clp-s archive directory into a selected output directory.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"]
---

# Decompress

Decompress an existing regular `clp-s` archive directory into an output
directory using the plugin's restricted wrapper.

## Allowed User Operation

Use only this wrapper for decompression:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" $ARGUMENTS
```

The wrapper intentionally exposes only `clp-s x` for local archive directories.
Do not use or suggest network/S3 auth, MongoDB metadata output, arbitrary
`clp-s` option passthrough, reducers, indexing, conversion, or search from this
skill.

## Invocation Flow

When the user asks to decompress a CLP archive:

1. Ask for the archive directory if it is not provided.
2. Ask for an output directory if it is not provided.
3. Prefer a specific temporary or project output directory, not a broad
   location such as `/`, the user's home directory, or `/home/jack/.claude`.
4. Run the decompression wrapper with the selected paths.
5. Report the `Output dir` printed by the wrapper.

## Common Commands

Decompress an archive directory:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

Preview the command without writing files:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
  /tmp/session-archive \
  /tmp/session-archive-decompressed \
  --dry-run
```

Decompress records in log order:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
  --ordered \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```
