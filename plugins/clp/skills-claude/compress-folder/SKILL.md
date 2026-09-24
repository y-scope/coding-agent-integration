---
name: compress-folder
description: Compress log files or folders of log files into a searchable CLP archive directory. Detects each file's format first.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-detect-logs:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"]
---

# Compress Folder

Use only the plugin wrappers. Do not call bare `clp-s` or expose arbitrary CLP commands/options.

Compression is two steps with a decision between them: `clp-detect-logs` reads the first 128 KiB of each file and shows you what is there, you decide from that report, and `clp-s-compress-folder` does the work with exactly the flags you chose. Don't read the log files yourself; the report has what you need, already cut to size.

## Rules

- Do not use this skill for session JSONL files; use `compress` for sessions.
- Do not pass `--single-file-archive`; search uses regular archive directories.
- Take `--timestamp-key` from the detection report (or from the user). Without one, `clp-s` still compresses and searches, but time-range flags (`--tge`/`--tle`) will not work.
- `clp-s` only ingests JSON. Text logs go in through `--structurize`, which converts them to JSONL. A file that is already JSON is never structurized.
- Default file extensions: `log`, `jsonl`, `json`, `txt`, `ndjson`, `out`, `err`. Override with `--extensions`.
- Default archive root: `${TMPDIR:-/tmp}/yscope-clp-archives`.
- Ask about archive location only if the user wants persistent storage or a change.

## Workflow

1. If the user does not name a file or folder, ask for one.

2. **Detect.** Run the detector on the same paths (read-only; it reads the first 128 KiB of each file, so it takes well under a second even on many-GB logs):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-detect-logs" /path/to/logs /path/to/other.log
   ```

   It first says whether each path is a file or a folder (and how many files a folder matched). Per file it then prints one of:

   - `format: json` — JSON objects parsed one after another (two or more make it JSON). `fields:` lists every field path with its type, and how many records have it when not all do; `timestamp:` names the field that holds a timestamp in every record and the kind of value (ISO 8601 string, epoch number, epoch as a string); `records:` shows the first records as valid JSON with every string cut to 128 characters.
   - `format: text` — `lines:` shows the first 20 lines, each cut to 256 characters. If the lines match a bundled format that `--structurize` converts on its own (`vllm-sflow`, `vllm-raw`), the format line says so.
   - `compressed`, `binary`, `empty`.

   It ends with a `SUGGEST` command per group of files that need the same settings, and `SKIP` lines for files that can't be compressed as they are.

3. **Decide**, and tell the user in one or two lines what you chose and why (e.g. "vllm_worker_3.log is sflow-wrapped vLLM text, so I'm structurizing it; cockroach.node1.log is JSON with epoch timestamps in `timestamp`, so it goes in as it is with that key"):

   - `json`: compress it as it is. Pass the reported timestamp field as `--timestamp-key` (check the example value really is the event time). Never add `--structurize`.
   - `text` that matches a bundled format: pass `--structurize`; it sets `--timestamp-key timestamp` itself.
   - `text` with no bundled format: work out its structure from the `lines:` shown — where each record starts, the timestamp and its format, the level, the logger, the message — and, if the user wants the file, write a parser (below) and test it before compressing.
   - `compressed`, `binary`, `empty`: leave them out and say so. Decompress a compressed log first if the user wants it.
   - One archive takes one `--timestamp-key`, and `--structurize` converts every text file it is given. Files that need different settings are compressed in separate runs, each with its own `--path` list. The `SUGGEST` lines are grouped that way already; use them unless the user wants something else.

4. **Compress** with the flags you chose:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --path /path/to/logs --timestamp-key ts
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --path /path/to/vllm_worker_3.log --structurize
   ```

   `--path` takes a file or a folder and can be repeated; the files found in all of them go into one archive. With `--structurize`, each converted file prints `[structurize] <file>: N records`, a JSON file prints `already JSON, compressed as it is`, and a file that can't be converted is skipped with a warning that gives the reason.

   A compression of a multi-GB log takes a minute or more. Run the wrapper as a background Bash call (`run_in_background: true`) in the FOREGROUND of that call: no trailing `&` (that detaches it, and the harness reports the call finished at once), and no `pgrep`/`ps` wait loop (`pgrep -f` can match the checking command itself and never end). The harness notifies you when the wrapper exits. Meanwhile the wrapper prints a `[compress] ...` heartbeat every 30 seconds (`--heartbeat SECONDS`, 0 to silence) with elapsed time, input read, an estimated time left and the archive size so far; read the call's output file and relay one line to the user each time. For a state check at any moment:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-compress-status" <archives-dir>
   ```

   It prints `STATE=` (`running`, `done`, `failed`, or `died` when the process vanished without finishing), `PROGRESS_PCT=` and the byte counts, and exits 0 for done, 3 for running, 1 for failed or died.

5. After compression, always report:

   - `Raw input bytes`
   - `Archive bytes`
   - `Compression ratio`
   - `File size reduction`
   - `Input files`
   - `Archives dir`
   - `Archive metadata`

6. Use the printed top-level `Archives dir` for search and decompression. The wrappers resolve the inner `clp-s` archive directory automatically.

## Writing a parser for an unknown text format

When the report shows text with no bundled format and the user wants the file, write a small Python file that defines one function, from the lines the report shows:

```python
import re

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+) (\w+): (.*)$")


def parse_line(line):
    m = LINE.match(line)
    if not m:
        return None  # a continuation line: it is appended to the previous record's message
    return {"timestamp": m[1], "level": m[2], "logger": m[3], "message": m[4]}
```

- `parse_line(line)` gets each non-empty line without its newline. It returns a dict for a line that starts a record, or `None` for a line that continues the previous one.
- The dict needs `timestamp` and `message`; any other keys become fields. Give `timestamp` as an ISO 8601 string (`2026-06-09 10:02:41,887`, `2026-06-09T10:02:41Z`) or epoch seconds or milliseconds, so `clp-s` can index it. Convert other forms in `parse_line` (for syslog's `Jun  9 10:02:41`, add the year).
- Save it outside the log folder (e.g. under `/tmp`), then test it. The detector runs it on every whole line of the 128 KiB it reads from each text file and shows the records it returns, still without writing anything:

  ```bash
  "${CLAUDE_PLUGIN_ROOT}/bin/clp-detect-logs" --parser /tmp/myapp_parser.py /path/to/app.log
  ```

  Fix the parser until `parser:` shows the lines you expect starting a record (continuation lines, such as stack traces, are the rest) and `timestamp:` shows an ISO 8601 or epoch value, then compress with the `SUGGEST` line, which adds `--structurize --parser FILE`.

## Useful Commands

Show archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --show-archives-root
```

Set persistent archive root:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" --set-archives-root ~/clp-archives
```

Dry run (read-only: prints the plan, converts and compresses nothing):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --path /path/to/logs \
  --dry-run
```

Compress only top-level `.log` files with a timestamp field:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --path /var/log/myapp \
  --extensions log \
  --no-recursive \
  --timestamp-key timestamp
```

Compress vLLM text logs:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
  --path /var/log/vllm \
  --extensions log,txt \
  --structurize
```
