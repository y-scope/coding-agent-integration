---
name: search
description: Search clp-s archives with non-semantic KQL queries and return matching results on stdout.
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)", "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects:*)"]
---

# Search

Run restricted `clp-s` KQL searches that return results to stdout.

## Allowed User Operation

Use only this wrapper for searching:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" $ARGUMENTS
```

The wrapper intentionally exposes only `clp-s s` with stdout results. Do not use
or suggest other `clp-s` output handlers such as `file`, `network`,
`results-cache`, or `reducer`. Do not call `clp-s x`, `clp`, `clo`, `clg`,
`indexer`, `log-converter`, or `reducer-server` from this plugin.

Semantic search is temporarily disabled. Do not use or suggest
`semantic(...)`, `--semantic-endpoint`, `--semantic-top-k`,
`--semantic-threshold`, or `--embedding-batch-size` until the CLP semantic
logtype sanitizer issue is fixed and this plugin is explicitly re-enabled.

## Claude Session Logs

When the user asks to search "my Claude session logs", "Claude Code session
logs", or similar, interpret that as the JSONL session files under:

```text
~/.claude/projects
```

First list the latest selectable sessions, sorted by session file last modified
time, newest first:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
```

Show the latest 5 choices by default. Ask the user which `IDX` to compress.
After the user selects one, compress that selected session into a regular
`clp-s` archive directory:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-claude-projects" \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index <IDX> \
  --timestamp-key timestamp
```

Then run KQL search against the `Archives dir` printed by the compression
wrapper, not an individual archive subdirectory or file. Continue to use only
`clp-s-search-kql` for the search step.

Claude Code session logs use top-level `timestamp` values such as
`2026-06-04T13:52:48.798Z`. Do not use `message.timestamp`.

## Claude Session Schema

When the user asks what they can search for, suggest practical KQL using the
Claude session schema below.

Common top-level fields:

- `uuid`, `parentUuid`, `sessionId`, `messageId` for record linkage.
- `timestamp` for ISO-8601 event time. This is the compression timestamp key.
- `type`, usually one of `user`, `assistant`, `system`, `attachment`,
  `custom-title`, `agent-name`, `last-prompt`, `permission-mode`,
  `queue-operation`, or `file-history-snapshot`.
- `cwd`, `gitBranch`, `slug`, `version`, `entrypoint`, `userType`, and
  `isSidechain` for session context.

Common nested or type-specific fields:

- `message.role`, `message.content`, `message.content.type`, and
  `message.content.name` for user and assistant message blocks.
- `message.model`, `message.usage`, and `message.stop_reason` for assistant
  responses.
- `subtype`, `durationMs`, `messageCount`, `level`, and `cause` for system
  records such as `turn_duration`, `compact_boundary`, `local_command`, and
  `api_error`.
- `operation` for queue operations.
- `permissionMode` for user records.
- `isApiErrorMessage` for assistant API-error records.
- `toolUseResult` for user-side tool result payloads.
- `customTitle`, `agentName`, `attributionPlugin`, and `attributionSkill` for
  titles, subagents, and plugin/skill attribution.

## Suggested Session Queries

Suggest these examples when the user asks what to search for. Keep the KQL query
single-quoted in shell commands.

| Goal | KQL query |
| --- | --- |
| Find all tool calls | `message.content.type:tool_use` |
| Find any named tool call | `message.content.name:*` |
| Find Bash tool calls | `message.content.name:Bash` |
| Find file edits | `message.content.name:Edit` or `message.content.name:MultiEdit` |
| Find tool results | `message.content.type:tool_result` or `toolUseResult:*` |
| Find errors | `level:error` |
| Find connection errors | `cause:*ConnectionRefused*` or `cause:*ECONNRESET*` |
| Find API errors | `isApiErrorMessage:true` |
| Find responses from a model family | `message.model:*glm*` |
| Find an exact model with punctuation | `message.model:"glm-5.1:cloud"` |
| Find long turns over 30 seconds | `subtype:turn_duration AND durationMs >= 30000` |
| Find sessions on a branch | `gitBranch:main` |
| Find user messages | `type:user` |
| Find assistant responses | `type:assistant` |
| Find compact boundaries | `subtype:compact_boundary` |
| Find queue operations | `operation:enqueue`, `operation:dequeue`, or `operation:remove` |
| Find a permission mode | `permissionMode:plan` |
| Find plugin-attributed responses | `attributionPlugin:clp` |
| Find a specific skill attribution | `attributionSkill:*search*` |
| Find session-start attachments | `attachment.hookName:SessionStart` |
| Find prompt-linked user turns | `promptId:*` |

For numeric comparisons, use infix comparison syntax such as
`durationMs >= 30000`; do not use `durationMs:>30000`.

## Supported Controls

The wrapper allows:

- `ARCHIVES_DIR`
- `KQL_QUERY`
- `--tge TS`
- `--tle TS`
- `-i` / `--ignore-case`
- `--archive-id ID`
- `--project COLUMN` repeated
- `--projection col1,col2`

## Examples

KQL:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  /tmp/session-archive \
  'level:error'
```

Projected KQL:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --project timestamp \
  --project type \
  --project sessionId \
  /tmp/session-archive \
  'message.content.name:Bash'
```

## Known Local Verification

The current plugin search wrapper supports plain KQL search against regular
archive directories created by the compression wrapper. Semantic KQL is
temporarily disabled at the plugin layer.
