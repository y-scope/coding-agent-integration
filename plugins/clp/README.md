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
- search local CLP archives with KQL (including `semantic("query")`) and stdout results.
- decompress a local CLP archive directory.

It does not expose full-project compression, reducers, network/file output
handlers, results-cache writes, indexing, conversion, remote decompression,
metadata sinks, or arbitrary `clp-s` option passthrough.

## Skills

| Skill | Scope |
| --- | --- |
| `compress` | Compress a session JSONL file into a CLP archive directory. |
| `search` | Search CLP archives with KQL, including `semantic("query")`. |
| `decompress` | Decompress a CLP archive directory for raw inspection. |
| `claude-code-trajectory` | End-to-end Claude Code session analysis: list → compress → search → decompress, plus Claude-specific query starters. |
| `codex-trajectory` | Same workflow for Codex session logs, plus Codex-specific query starters. |

Future use-cases (e.g. vLLM debugging) will add their own skill directories
under `skills-claude/` (or `skills-codex/` if the use-case is agent-specific).

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

- `bin/clp-s-list-sessions`
- `bin/clp-s-compress-session`
- `bin/clp-s-search-kql`
- `bin/clp-s-decompress`

The wrappers prefer `CLP_S_BIN`, then plugin-local `bin/clp-s`, then
plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`.

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

## Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'level:error'
```

Allowed controls: `--tge`, `--tle`, `--ignore-case`, `--archive-id`,
`--project`, `--projection`, `--semantic-endpoint`, `--semantic-top-k`,
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
`https://ca-central-2-semantic-cache.yscope.ai` is used. Override with
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

## Decompress

```bash
./plugins/clp/bin/clp-s-decompress \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

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
