---
name: compress
description: Compress one selected session JSONL file into a searchable CLP archive directory.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"]
---

# Compress

Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP
commands/options.

## Rules

- Compress exactly one selected session JSONL file.
- Do not compress full Claude/Codex trees.
- Do not pass `--single-file-archive`; search uses regular archive directories.
- Always use `--timestamp-key timestamp`.
- Session roots: Claude `~/.claude/projects`, Codex `${CODEX_HOME:-~/.codex}/sessions`.
- Default archive root: `${TMPDIR:-/tmp}/yscope-clp-archives`.
- Ask about archive location only if the user wants persistent storage or a change.

## Workflow

1. List sessions, newest first:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
   ```

   Use `--agent claude` or `--agent codex` if the user asks for one agent.

2. Present choices with these columns: `IDX`, `AGENT`, modified timestamp,
   raw bytes, human size, session name, project/cwd, session ID.

3. After the user chooses an `IDX`, compress using the printed manifest:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index <IDX> \
     --timestamp-key timestamp
   ```

4. After compression, always report:

   - `Raw input bytes`
   - `Archive bytes`
   - `Compression ratio`
   - `File size reduction`
   - `Archives dir`
   - `Selected session`
   - `Archive metadata`

Use the printed top-level `Archives dir` for search/decompress. Wrappers resolve
the inner `clp-s` archive directory automatically.

## Useful Commands

Show archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" --show-archives-root
```

Set persistent archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" --set-archives-root ~/clp-s-archives
```

Dry run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
  --selection-file /tmp/clp-s-session-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --dry-run
```
