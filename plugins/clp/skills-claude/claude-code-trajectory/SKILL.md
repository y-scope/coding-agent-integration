---
name: claude-code-trajectory
description: Analyze Claude Code session logs — list, compress, search, and decompress trajectories.
allowed-tools:
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
---

# Claude Code Trajectory

End-to-end workflow for analyzing a Claude Code session log with CLP. Use
this when the user asks to investigate what happened in a Claude session:
which tools fired, what failed, how long a turn took, what context was used.

For general-purpose KQL search (no session involved), use the `search`
skill instead.

## Workflow

1. List sessions, newest first:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
   ```

   Use `--agent claude` (default) or `--agent codex` if the user asks.

2. Present choices with these columns: `IDX`, `AGENT`, modified timestamp,
   raw bytes, human size, session name, project/cwd, session ID.

3. Compress the selected `IDX`:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index <IDX> \
     --timestamp-key timestamp
   ```

4. Report compression stats: raw input bytes, archive bytes, compression
   ratio, file size reduction.

5. Search the printed archive directory:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
     /tmp/session-archive \
     'message.content.name:Bash'
   ```

6. (Optional) Decompress for raw inspection:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
     /tmp/session-archive \
     /tmp/session-archive-decompressed
   ```

If the user provides an archive path directly, skip listing/compression
and search that archive.

## Query Starters

For broad trajectory debugging, suggest using a subagent and ask it to
return only archive path, queries, top findings, and next queries.

| Goal | KQL |
| --- | --- |
| Claude tool calls | `message.content.type:tool_use` |
| Claude Bash calls | `message.content.name:Bash` |
| Claude command text | `message.content.input.command:*` |
| Claude edits | `message.content.name:Edit OR message.content.name:MultiEdit OR message.content.name:Write` |
| Claude tool results | `message.content.type:tool_result OR toolUseResult:*` |
| Claude failures | `toolUseResult.success:false OR toolUseResult.stderr:* OR level:error` |
| Claude API/transport errors | `isApiErrorMessage:true OR subtype:api_error OR cause:*ECONNRESET*` |
| Claude long turns | `subtype:turn_duration AND durationMs >= 30000` |
| Claude compaction | `subtype:compact_boundary` |
| Harness runs | `"swebench.harness.run_evaluation" OR "run_evaluation"` |
| Harness reports | `"report.json" OR "instance_results.jsonl" OR "results.json"` |
| Test failures | `"FAILED" OR "AssertionError" OR "Traceback"` |
| Patch failures | `"git apply" AND ("failed" OR "reject" OR "patch does not apply")` |
| Docker/resource issues | `"docker" AND ("failed" OR "No space left" OR "permission denied")` |
| Semantic: slow operations | `semantic("slow operations")` |
| Semantic: authentication issues | `semantic("authentication failures")` |
| Semantic: network errors | `semantic("network timeout or connection errors")` |
| Semantic: combined with KQL | `semantic("errors") AND level:error` |

Combine a user-provided repo, file, command, test, or instance ID with a
starter query using `AND`.
