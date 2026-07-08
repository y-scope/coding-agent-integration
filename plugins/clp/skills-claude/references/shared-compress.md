# Shared compress reference

Shared by the `compress` (stable clp-s) and `clpp-compress` (clpp / `--experimental`)
skills. Read this for the compress workflow and rules; read `clpp-shared.md` for
the clpp-specific additions when compressing with a parsing specification.

## Rules

- Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP
  commands/options.
- Compress exactly **one** selected session JSONL file. Do not compress full
  Claude/Codex trees.
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

   Or, when the user gives a path directly, use `--session-file` instead of the
   selection manifest.

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

## Stable compress options

`--compression-level`, `--target-encoded-size`, `--print-archive-stats`,
`--output-dir`, `--dry-run`, and the archive-root config all apply to both the
stable and the clpp (`--experimental`) paths.

## Useful commands

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

## Pointing at a local build

Pass `--clp-s-bin PATH` per invocation, or set `CLP_S_BIN` so every wrapper uses a
locally-built `clp-s` (e.g. an in-flight clpp branch). See the `dev` skill for
build/test/lint of a local CLP source tree.