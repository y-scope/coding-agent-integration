---
name: compress
description: Compress one selected Claude Code session JSONL file under ~/.claude/projects using a clp-s regular archive directory.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"]
---

# Compress

Compress one selected Claude Code session transcript log with `clp-s` while
preserving the required archive format.

## Required Behavior

Use regular `clp-s` archive directory mode for this workflow. Do not pass
`--single-file-archive` when compressing Claude Code session logs for search.

Compression requires a single selected session. Do not compress the entire
`~/.claude/projects` tree in this plugin.

Claude Code session logs use the top-level JSON key `timestamp` for event time.
Always pass `--timestamp-key timestamp` when compressing selected session logs.
Do not use a nested timestamp key such as `message.timestamp`.

Prefer the bundled wrapper because it validates that the selected session is
under the expected Claude project-log directory, uses the regular archive
directory format, and prints a concise summary:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" $ARGUMENTS
```

The wrapper defaults to:

- source root: `~/.claude/projects` derived from `$HOME`
- selected session input: required
- output directory: `./clp-s-archives/session-<session-id>-<UTC timestamp>`
- timestamp key: `timestamp`
- compression command: `clp-s c --timestamp-key timestamp -f <file-list> <output-dir>`

Do not call `clp-s x`, `clp-s s`, `clp`, `clo`, `clg`, `indexer`,
`log-converter`, or `reducer-server` from this compression skill.

## Invocation Flow

When the user asks to compress or search Claude Code session logs:

1. List the latest sessions first:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
   ```

2. Show the listed `IDX`, modified time, size, project/cwd, and session ID to
   the user, and ask which `IDX` to compress.
3. After the user selects an index, pass the printed `Selection manifest` and
   chosen `IDX` to the compression wrapper:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index 1 \
     --timestamp-key timestamp
   ```
4. If no output directory is provided, accept the wrapper default.
5. If the user asks where the archive was written, report the `Archives dir`
   and `Archive entries` lines from the wrapper output. Use that same
   `Archives dir` path for `/clp:search` and `/clp:decompress` if needed.
6. If compression fails, report the exact error and the command summary printed
   by the wrapper.

The session listing is sorted by session file last modified time, newest first,
and shows the latest 5 sessions by default. Use `--limit N` only if the user
needs more choices.

## Common Commands

List the latest 5 selectable Claude session logs:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
```

Compress a selected session from a manifest:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --selection-file /tmp/clp-s-session-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp
```

Compress a selected session to an explicit archive directory:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --selection-file /tmp/clp-s-session-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --output-dir /tmp/session-archive
```

Compress a specific session file directly:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --session-file ~/.claude/projects/.../session.jsonl \
  --timestamp-key timestamp
```

Preview the planned operation without creating an archive:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --selection-file /tmp/clp-s-session-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --dry-run
```

Print archive stats after compression:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --selection-file /tmp/clp-s-session-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --print-archive-stats
```

## Notes

`clp-s` is for JSON/JSONL logs. This skill only compresses one selected Claude
session JSONL file per invocation. Some session metadata/control records may not
have a `timestamp` field, but transcript event records use top-level
`timestamp`, and local verification confirmed `clp-s` accepts
`--timestamp-key timestamp` for these files.
