# YScope CLP Plugin

Plugin for compressing, searching, and decompressing coding-agent session
log archives with [CLP](https://github.com/y-scope/clp) (Compressed Log
Processor).

CLP is the open-source platform for log archive storage, search, and
analytics. Pre-release builds may also include licensed YScope extensions.

## API Surface

The plugin exposes only:

- list recent Claude Code and Codex session JSONL files.
- compress one selected session with `clp-s c --timestamp-key timestamp`.
- compress log files from an arbitrary folder with `clp-s c --remove-path-prefix FOLDER -f FILE_LIST OUTPUT_DIR`.
- search local CLP archives with KQL (including `semantic("query")`) and stdout results.
- dump an archive's logtype dictionary with the `stats.log_shapes` query.
- decompress a local CLP archive directory.

It does not expose full-project compression, reducers, network/file output
handlers, results-cache writes, indexing, conversion, remote decompression,
metadata sinks, or arbitrary `clp-s` option passthrough.

## Skills

| Skill | Scope |
| --- | --- |
| `compress` | Compress a session JSONL file into a CLP archive directory. |
| `compress-folder` | Compress log files from an arbitrary folder into a CLP archive directory. |
| `search` | Search CLP archives with KQL, including `semantic("query")`. |
| `logtype-insights` | App-agnostic log analysis driven by the archive's logtype dictionary: dump the templates, classify them (cached), then run targeted queries derived from templates that are known to exist. |
| `decompress` | Decompress a CLP archive directory for raw inspection. |
| `claude-code-trajectory` | End-to-end Claude Code session analysis: list → compress → search → decompress, plus Claude-specific query starters. |
| `codex-trajectory` | Same workflow for Codex session logs, plus Codex-specific query starters. |

Future use-cases will add their own skill directories under `skills-claude/`
(or `skills-codex/` if the use-case is agent-specific).

## Install

Hosted installer:

```bash
curl -fsSL https://installer.yscope.ai/coding-agent-plugin.sh | bash
```

Local marketplace:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp

claude plugin marketplace add "$PWD" --scope user
claude plugin install clp@yscope --scope user

codex plugin marketplace add "$PWD"
codex plugin add clp@yscope
```

Local plugin session:

```bash
claude --plugin-dir ./plugins/clp
```

## Wrappers

Restricted-passthrough wrappers around `clp-s` — these are the security
boundary (flag allowlist, path validation, env hardening):

- `bin/clp-s-list-sessions`
- `bin/clp-s-compress-session`
- `bin/clp-s-compress-folder`
- `bin/clp-s-search-kql`
- `bin/clp-s-decompress`

The wrappers prefer `CLP_S_BIN`, then plugin-local `bin/clp-s`, then
plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`.

Local helpers (not `clp-s` passthroughs — they never invoke the binary):

- `bin/structurize.py` — converts unstructured text logs to structured JSONL.
  Used by `clp-s-compress-folder --structurize`; not called directly.
- `bin/logtype-cache` — persistent cache of the `logtype-insights`
  classification, with incremental update when an archive grows. See
  [Logtype Cache](#logtype-cache).

## Session Workflow

List recent sessions:

```bash
./plugins/clp/bin/clp-s-list-sessions
```

Defaults:

- sources: Claude `~/.claude/projects` and Codex `${CODEX_HOME:-~/.codex}/sessions`.
- limit: latest 5, sorted by session file mtime descending.
- Claude subagents: excluded unless `--include-subagents`.
- manifest: written to `/tmp`.

When presenting choices, always include `IDX`, `AGENT`, modified timestamp,
raw bytes, human size, session name, project/cwd, and session ID.

Check archive root:

```bash
./plugins/clp/bin/clp-s-compress-session --show-archives-root
```

Default archive root is `${TMPDIR:-/tmp}/yscope-clp-archives`. Use it without
asking. Ask only when the user wants persistent storage or a different root.

Compress the selected row:

```bash
./plugins/clp/bin/clp-s-compress-session \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index 1 \
  --timestamp-key timestamp
```

After compression, report these lines:

- `Raw input bytes`
- `Archive bytes`
- `Compression ratio`
- `File size reduction`
- `Archives dir`
- `Selected session`
- `Archive metadata`

Use the printed top-level `Archives dir` for search and decompression. The
wrappers resolve the inner `clp-s` archive directory automatically. Metadata in
`.yscope-clp-archive.json` maps archive to session file, agent, roots,
timestamp key, SHA-256, compression stats, command, and resolved inner archive.

## Folder Logs

Compress log files from an arbitrary folder:

```bash
./plugins/clp/bin/clp-s-compress-folder --folder /var/log/myapp
```

Defaults:

- extensions: `log,jsonl,json,txt,ndjson,out,err` (override with `--extensions`,
  or use `--extensions '*'` to include every regular file).
- recursive: yes (use `--no-recursive` for top-level only).
- structurize: off (pass `--structurize` for unstructured text logs — see below).
- timestamp key: none (pass `--timestamp-key KEY` if your logs have a known
  timestamp field; required for time-range search).
- archive root: `${TMPDIR:-/tmp}/yscope-clp-archives` (override per-run with
  `--archives-root DIR`). Ask only when the user wants persistent storage or a
  different root.

### Unstructured text logs

Plain-text logs (interleaved timestamp, logger, level, message) have no field
structure for `clp-s` to index. `--structurize` runs each file through
`bin/structurize.py` first, producing JSONL with `timestamp/logger/level/message`
and setting `--timestamp-key timestamp` automatically:

```bash
./plugins/clp/bin/clp-s-compress-folder --folder /var/log/vllm --structurize
```

Files that cannot be parsed are skipped with a warning. Do not use it on logs
that are already JSON/JSONL/NDJSON.

After compression, report:

- `Raw input bytes`
- `Archive bytes`
- `Compression ratio`
- `File size reduction`
- `Input files`
- `Archives dir`
- `Archive metadata`

The resulting archive is compatible with `clp-s-search-kql` and
`clp-s-decompress`. Use the printed top-level `Archives dir` for search and
decompression. Metadata in `.yscope-clp-archive.json` records the source
folder, extensions, file count, compression stats, command, and resolved inner
archive.

Useful commands:

```bash
./plugins/clp/bin/clp-s-compress-folder --show-archives-root
./plugins/clp/bin/clp-s-compress-folder --set-archives-root ~/clp-archives
./plugins/clp/bin/clp-s-compress-folder --folder /var/log/myapp --dry-run
./plugins/clp/bin/clp-s-compress-folder --folder ./logs --extensions log,txt
```

## Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'level:error'
```

Allowed controls: `--tge`, `--tle`, `--ignore-case`, `--archive-id`,
`--projection`, `--semantic-endpoint`, `--semantic-top-k`,
`--semantic-threshold`, `--embedding-batch-size`.

Use single quotes around KQL in shell commands. Numeric comparisons use infix
syntax, for example `durationMs >= 30000`.

## Semantic Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'semantic("slow database queries")'
```

Semantic search finds log events whose logtype is semantically similar to a
natural language query, even when exact keywords differ. Use `semantic("query")`
in KQL and combine with regular KQL using `AND`, e.g.
`'semantic("errors") AND level:error'`.

The wrapper health-checks the embedding service before running a semantic
search; if the service is unavailable, the search fails with a clear error.

Endpoint: auto-detected — a local embedding server on `http://localhost:8080`
is preferred (so token data stays on the machine), otherwise the remote
endpoints are tried in order — `https://ca-central-1-semantic-cache.yscope.ai`
then `https://ca-central-2-semantic-cache.yscope.ai` — and the first that
passes the health check is used. Override with
`--semantic-endpoint URL` or set `CLP_SEMANTIC_ENDPOINT`.
Other semantic flags: `--semantic-top-k K` (default 5), `--semantic-threshold T`
(default 0.3, range 0.0-1.0), `--embedding-batch-size N` (default auto),
`--semantic-cache-dir DIR`, and `--semantic-cache-cold-capacity N`.

A local embedded semantic cache is auto-enabled under the plugin config dir
(`~/.config/yscope-clp-plugin/semantic-cache`, cold tier of 10 000 000 entries
/ ~4 GB, matching the clp-s default) so repeated semantic queries score
in-process (~sub-ms) instead of round-tripping to the endpoint. The cache is
shared across all sessions and archives. Disable with `--semantic-cache-dir
none` or `CLP_SEMANTIC_CACHE_DIR=none`; resize with
`--semantic-cache-cold-capacity N` or `CLP_SEMANTIC_CACHE_COLD_CAPACITY`.

The local cache requires a `clp-s` that supports `--semantic-cache-dir`. Older
builds (e.g. clp-core 0.12.1) do not, and abort with `Unknown OUTPUT_HANDLER`
if the flag is passed. The wrapper probes `clp-s s --help` and, when the flag
is unsupported, prints a warning and falls back to remote-only scoring — the
search still works, without the local cache.

## Decompress

```bash
./plugins/clp/bin/clp-s-decompress \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

## Logtype Insights

`logtype-insights` analyzes an archive by first dumping its **logtype
dictionary** — the complete vocabulary of distinct message templates, with
variables replaced by `<*>`:

```bash
# stats.log_shapes (shapes API, clp-core >= 0.13) dumps the dictionary as raw
# {"archive_id","count","id","shape"} lines; the wrapper adds the required
# --experimental automatically. Shape strings encode variables in an
# archive-dependent form (raw placeholder bytes on regular archives,
# %rule.name% TextShape placeholders on clpp archives) — `logtype-cache
# normalize` detects the encoding per line and renders both to the canonical
# {"logtype":"...<*>..."} NDJSON.
# The wrapper prints archive-metadata header lines to stdout, so filter to
# JSON records with grep '^{' first. The legacy `stats.logtypes` spelling is
# rejected: shapes-API binaries silently return nothing for it.
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | ./plugins/clp/bin/logtype-cache normalize > /tmp/logtypes.ndjson
jq -s 'length' /tmp/logtypes.ndjson
```

This reads the dictionary rather than every record, so it is cheap regardless
of archive size: a run with millions of records typically has tens to a few
hundred templates. Every subsequent query is derived from a template that is
known to exist, instead of guessing keywords that may not appear at all.

The skill is app-agnostic — it discovers the schema (timestamp/severity/logger/
message field names) from a sample record, so it works on structurized text
archives and native-JSON archives alike.

Note that the message field is stored as a CLP-string, so KQL **cannot** match
message content: `message:term` and `message:*term*` always return 0. Retrieve
message content by projecting the field and grepping it; the scalar fields
(severity, logger, JSON payload leaves) are KQL-searchable normally.
`semantic("…")` searches the logtypes directly and is the one KQL construct
that reaches message content.

### Logtype Cache

Classifying templates into categories and deriving a query plan is the
expensive step, and it is a property of the *application*, not the individual
capture — the same build emits the same templates every run. `bin/logtype-cache`
persists that classification, keyed by `sha256` of the sorted distinct logtype
strings (the placeholder-rendered form, so fingerprints are stable across
binary generations):

```bash
LC=./plugins/clp/bin/logtype-cache
# Dump the dictionary and render it to canonical logtype NDJSON in one pipe:
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | "$LC" normalize > /tmp/logtypes.ndjson
"$LC" count --logtypes-file /tmp/logtypes.ndjson   # distinct templates
"$LC" diff  --logtypes-file /tmp/logtypes.ndjson   # UPTODATE | GROWTH | NEW
"$LC" list                                          # cached entries + lineage
"$LC" show <APP_KEY>
```

(`key`, `count`, and `diff` also accept a raw `stats.log_shapes` dump directly
— lines carrying a `shape` field are rendered on the fly — but store and pass
around the normalized form so every tool sees identical strings.)

`diff` prints one tab-separated header line, followed by NDJSON
`{"logtype":"…"}` lines for the templates that still need classifying:

| Header | Meaning |
| --- | --- |
| `UPTODATE\t<app_key>\t<count>` | Template set unchanged — reuse the cached classification, nothing to classify. |
| `GROWTH\t<app_key>\t<base_key>\t<count>\t<new_count>` | Archive grew from `<base_key>` — only the `<new_count>` new templates follow and need classifying. |
| `NEW\t<app_key>\t<count>` | No compatible base — all `<count>` templates follow. |

On GROWTH the new classification is merged into the base entry with
`put-merged --base-key BK --key NK` (templates, taxonomy, and query plan are
unioned; `grown_from` records the lineage), so a growing archive only ever
costs the classification of its newly-added templates.

Cache location: `~/.config/yscope-clp-plugin/logtype-cache/`, overridable with
`$CLP_LOGTYPE_CACHE_DIR`, or per-command with `--cache-dir` on the subcommands
that read or write the cache (`diff`, `get`, `put`, `put-merged`, `list`,
`show`). `normalize`, `count`, and `key` only transform/hash the input and do
not accept it.

## Query Starters

For session-log analysis (which tools fired, what failed, how long a turn
took, what context was used), see the per-use-case trajectory skills:

- Claude Code: `claude-code-trajectory` skill (in the installed plugin)
- Codex: `codex-trajectory` skill (in the installed plugin)

For harness/test/patch failures and Docker/resource issues, see the
`Trajectory` sections in those skills — both have a "Query Starters" table
covering SWE-bench runs, test failures, patch failures, and Docker issues.

For semantic search suggestions, see the `Semantic Search` section in the
`search` skill.

For broad trajectory debugging, suggest a subagent when available. Ask it to
run the query sequence and return only the archive path, queries, top
findings, and next useful queries.
