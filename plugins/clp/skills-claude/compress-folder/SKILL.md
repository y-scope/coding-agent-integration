---
name: compress-folder
description: Compress log files from an arbitrary folder into a searchable CLP archive directory.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"]
---

# Compress Folder

Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP commands/options.

## Rules

- Compress log files from one folder. Do not use this skill for session JSONL files; use `compress` for sessions.
- Do not pass `--single-file-archive`; search uses regular archive directories.
- `--timestamp-key` has no default. Only pass it when the user says their logs have a known timestamp field. Omit it otherwise — `clp-s` will still compress and search, but time-range flags (`--tge`/`--tle`) will not work.
- Default file extensions: `log`, `jsonl`, `json`, `txt`, `ndjson`, `out`, `err`. Override with `--extensions`.
- Default archive root: `${TMPDIR:-/tmp}/yscope-clp-archives`.
- Ask about archive location only if the user wants persistent storage or a change.

## Structurize (Unstructured Text Logs)

Use `--structurize` when compressing **unstructured text logs** — plain-text log files that lack a regular structured format (e.g. vLLM wrapper logs, application logs with interleaved timestamps and messages). The flag runs each input file through `bin/structurize.py`, which parses out timestamp, logger, level, worker, and message fields and writes structured JSONL. This gives `clp-s` proper timestamp extraction and better compression.

When `--structurize` is active:

- Each input file is converted to a `.json` sidecar in a temp directory.
- `--timestamp-key timestamp` is set automatically (do not override it).
- Files that structurize cannot parse are skipped with a warning.
- The archive's `source.path` metadata still records the original folder path.

Do **not** use `--structurize` for files that are already structured (JSON, JSONL, NDJSON) — structurize is designed for unstructured text formats.

## Workflow

1. If the user does not specify a folder, ask for one.

2. Run compression:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --folder /path/to/logs
   ```

Override extensions or add a timestamp key as needed:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
     --folder /path/to/logs \
     --extensions log,txt \
     --timestamp-key ts
   ```

For unstructured text logs (vLLM logs, plain-text app logs):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
     --folder /path/to/logs \
     --structurize
   ```

   A compression of a multi-GB log takes a minute or more. Run the wrapper as a background Bash call (`run_in_background: true`) in the FOREGROUND of that call: no trailing `&` (that detaches it, and the harness reports the call finished at once), and no `pgrep`/`ps` wait loop (`pgrep -f` can match the checking command itself and never end). The harness notifies you when the wrapper exits. Meanwhile the wrapper prints a `[compress] ...` heartbeat every 30 seconds (`--heartbeat SECONDS`, 0 to silence) with elapsed time, input read, an estimated time left and the archive size so far; read the call's output file and relay one line to the user each time. For a state check at any moment:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-compress-status" <archives-dir>
   ```

   It prints `STATE=` (`running`, `done`, `failed`, or `died` when the process vanished without finishing), `PROGRESS_PCT=` and the byte counts, and exits 0 for done, 3 for running, 1 for failed or died.

3. After compression, always report:

   - `Raw input bytes`
   - `Archive bytes`
   - `Compression ratio`
   - `File size reduction`
   - `Input files`
   - `Archives dir`
   - `Archive metadata`

4. Use the printed top-level `Archives dir` for search and decompression. The wrappers resolve the inner `clp-s` archive directory automatically.

## Useful Commands

Show archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --show-archives-root
```

Set persistent archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --set-archives-root ~/clp-archives
```

Dry run:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --folder /path/to/logs \
  --dry-run
```

Compress only top-level `.log` files with a timestamp field:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --folder /var/log/myapp \
  --extensions log \
  --no-recursive \
  --timestamp-key timestamp
```

Compress unstructured text logs (e.g. vLLM wrapper logs):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --folder /var/log/vllm \
  --extensions log,txt \
  --structurize
```