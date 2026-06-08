# YScope CLP Plugin

Claude Code plugin for compressing selected session transcript logs from
`~/.claude/projects/**/*.jsonl`, searching CLP archives, and
decompressing archive directories.

CLP is the open-source platform for log archive storage, search, and analytics.
Built for humans and AI, at the edge, and in the cloud. Downloaded builds may
also include licensed YScope extensions such as semantic search and schema
generation, depending on the user's access key and artifact entitlement.

This plugin/installer flow is for private pre-release builds. Continue only if
YScope has expressly authorized your access. If you are not authorized, do not
install or use these artifacts.

The wrapper compresses one selected session into a regular `clp-s` archive
directory:

```bash
clp-s c --timestamp-key timestamp ...
```

## User-Facing API

This plugin intentionally exposes only three operations:

- Compress one selected Claude session JSONL log into a regular `clp-s` archive
  directory.
- Search `clp-s` archives with KQL and return stdout results.
- Decompress a local `clp-s` archive directory into a selected output
  directory.

It does not expose reducers, network/file output handlers, results-cache writes,
indexing, conversion, remote decompression, metadata sinks, or arbitrary
`clp-s` option passthrough.

## Components

- `bin/clp-s-compress-claude-projects` - wrapper that compresses one selected
  Claude session log with `clp-s c --timestamp-key timestamp`.
- `bin/clp-s-list-sessions` - lists recent Claude session JSONL files under
  `~/.claude/projects`, newest first, and writes a stable selection
  manifest.
- `bin/clp-s-search-kql` - restricted `clp-s s` wrapper that returns results on
  stdout and blocks unsupported output handlers/options.
- `bin/clp-s-decompress` - restricted `clp-s x` wrapper for local archive
  directory decompression.
- `skills/compress/SKILL.md` - user-invoked compression skill.
- `skills/search/SKILL.md` - user-invoked KQL search skill.
- `skills/decompress/SKILL.md` - user-invoked decompression skill.

## Install

### Bash Installer

For a hosted production script with its installer manifest URL embedded:

```bash
curl -fsSL https://installer.yscope.ai/claude-code-plugin.sh | bash
```

While URLs are not embedded, pass both manifests explicitly:

```bash
curl -fsSL https://installer.yscope.ai/claude-code-plugin.sh \
  | bash -s -- \
      --installer-manifest-url https://example.yscope.io/clp/installer-manifest.json \
      --manifest-url https://example.yscope.io/clp/manifest.json
```

The Bash script downloads a platform-specific compiled installer binary,
verifies its SHA-256 when provided, and execs it. The compiled installer shows
pre-release terms, prompts for an access key, downloads the plugin marketplace
archive and matching `clp-s` artifact, installs the marketplace under
`~/.claude/marketplaces/yscope`, and installs `clp@yscope` with Claude Code.

From a local checkout, build and launch the installer binary:

```bash
cd installer
bun install
bun run build:binary
cd ..
./claude-code-plugin.sh --installer-path ./installer/dist/yscope-clp-installer
```

### Local Marketplace

From the repository root, validate and register the local `yscope` marketplace:

```bash
claude plugin validate ./yscope
claude plugin marketplace add "$PWD/yscope" --scope user
claude plugin install clp@yscope --scope user
```

For project-only installation, use `--scope project` for both `marketplace add`
and `plugin install`.

After installation, invoke the skills with:

```text
/clp:compress
/clp:search /tmp/session-archive 'level:error'
/clp:decompress /tmp/session-archive /tmp/session-archive-decompressed
```

The wrapper scripts prefer `CLP_S_BIN` when set, then plugin-local `bin/clp-s`,
then plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`.

## Search

The search wrapper currently supports non-semantic KQL only. Semantic search is
temporarily disabled until the CLP semantic logtype sanitizer issue is fixed.
Do not use `semantic(...)`, `--semantic-endpoint`, `--semantic-top-k`,
`--semantic-threshold`, or `--embedding-batch-size` with this plugin.

Useful Claude session KQL examples:

| Goal | KQL query |
| --- | --- |
| Tool calls | `message.content.type:tool_use` |
| Bash commands | `message.content.name:Bash` |
| Tool results | `message.content.type:tool_result` or `toolUseResult:*` |
| Errors | `level:error` |
| Connection errors | `cause:*ConnectionRefused*` or `cause:*ECONNRESET*` |
| API errors | `isApiErrorMessage:true` |
| Model family | `message.model:*glm*` |
| Long turns | `subtype:turn_duration AND durationMs >= 30000` |
| User messages | `type:user` |
| Assistant responses | `type:assistant` |
| Compact boundaries | `subtype:compact_boundary` |
| Plugin attribution | `attributionPlugin:clp` |

## Session Selection

When a user asks to search Claude Code session logs, list recent session files
from `~/.claude/projects` first:

```bash
./yscope/plugins/clp/bin/clp-s-list-sessions
```

The default list is the latest 5 main session JSONL files, sorted by file last
modified time descending. The command writes a `Selection manifest` in `/tmp`.
After the user chooses an `IDX`, compress that exact session. Full-project
compression is intentionally not supported:

```bash
./yscope/plugins/clp/bin/clp-s-compress-claude-projects \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index 1 \
  --timestamp-key timestamp
```

Use the printed `Archives dir` path for search:

```bash
./yscope/plugins/clp/bin/clp-s-search-kql \
  /path/printed/as/Archives-dir \
  'type: attachment'
```

Use `/clp:decompress` when you need to extract files from that archive
directory.

## Local Testing

Validate the plugin:

```bash
claude plugin validate ./yscope/plugins/clp
```

Load it for a Claude Code session:

```bash
claude --plugin-dir ./yscope/plugins/clp
```

Invoke the skill in Claude Code:

```text
/clp:compress
```

Invoke KQL search in Claude Code:

```text
/clp:search /tmp/session-archive 'level:error'
```

Invoke decompression in Claude Code:

```text
/clp:decompress /tmp/session-archive /tmp/session-archive-decompressed
```

Or run the wrapper directly:

```bash
./yscope/plugins/clp/bin/clp-s-list-sessions
./yscope/plugins/clp/bin/clp-s-compress-claude-projects \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --dry-run
```
