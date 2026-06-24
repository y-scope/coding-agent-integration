---
name: claude-code-trajectory
description: Analyze Claude Code session logs — list, compress, search, and decompress trajectories.
allowed-tools:
  - "Agent"
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

5. **Spawn a subagent to run all searches.** Use the Agent tool with model
   `haiku` (fall back to `sonnet`). The subagent runs searches, processes raw
   JSON, and returns only a compact report — keeping the main context clean.

   Subagent prompt template (fill in `ARCHIVE`, `PLUGIN_BIN`, `GOAL`):

   ```
   Analyze this Claude Code CLP session archive: ARCHIVE

   Search wrapper: PLUGIN_BIN/clp-s-search-kql
   Goal: GOAL

   Efficiency rules (follow strictly):
   - Use compound KQL instead of multiple queries: field1:A AND field2:B
   - Count with: clp-s-search-kql ARCHIVE 'KQL' | grep -c '^{'
   - Project aggressively — fetch only the fields you need, not full records: pass
     `--project` for each required column (e.g. `--project timestamp --project durationMs`).
     Only omit --project when you genuinely need the whole record.
   - Zoom into time windows: --tge EPOCH_MS --tle EPOCH_MS (NOT timestamp KQL)
   - Use semantic("query") when field names are uncertain

   KQL syntax rules (do not violate):
   - Time ranges: ONLY via --tge/--tle flags with epoch ms — NEVER as KQL predicates
   - Array fields: dot notation only — message.content.type:X NOT message.content[].type:X
   - Convert timestamps: python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"

   Semantic search: use semantic("query") — the wrapper auto-selects a working
   endpoint and shares a local cache across sessions, so no endpoint flags are
   needed.

   Suggested starting queries:
   - Tool call breakdown: run one query per tool name using message.content.name:TOOL | grep -c '^{'
   - Failures: toolUseResult.success:false OR toolUseResult.stderr:* OR level:error
   - Long turns: subtype:turn_duration AND durationMs >= 30000
   - Compaction: subtype:compact_boundary

   Return ONLY (no raw JSON, no header lines):
   1. Archive path
   2. Queries run (KQL string only, one per line)
   3. Key findings (≤10 bullets, concrete numbers and facts)
   4. 2–3 follow-up queries worth running
   ```

6. Present the subagent's compact report to the user. Offer to drill deeper
   with a follow-up subagent or decompress for raw inspection:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
     /tmp/session-archive \
     /tmp/session-archive-decompressed
   ```

If the user provides an archive path directly, skip listing/compression
and go straight to step 5.

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

## Analysis Patterns

CLP searches the compressed archive — unmatched records are never decompressed.
Push logic into KQL rather than fetching all records and post-filtering in shell
or Python.

**For analyses that run 3+ queries, spawn a subagent:**
- Prefer Haiku model (`haiku`); fall back to Sonnet (`sonnet`) if unavailable.
- Brief the subagent with the archive path and the analysis goal.
- Ask it to return only: archive path, queries run, key findings, and next useful queries.
- This keeps the parent context lean and parallelizes independent query batches.

**Count matches — `grep -c '^{'` skips header lines; each result line starts with `{`:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'message.content.name:Bash' | grep -c '^{'
```

**Compound KQL — one query instead of multiple + joins:**
```bash
# Long turns only
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'subtype:turn_duration AND durationMs >= 30000'
# Bash calls matching a keyword
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'message.content.name:Bash AND message.content.input.command:*cargo*'
# Failures with stderr output
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'toolUseResult.success:false AND toolUseResult.stderr:*'
```

**Reduce payload — project only the fields you need, by default:** full records are
large; fetch only the columns your analysis uses.
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --project timestamp --project durationMs \
  ARCHIVE 'subtype:turn_duration'
```

**Zoom into a time window with `--tge`/`--tle` (epoch milliseconds):**
```bash
# Convert: python3 -c "from datetime import datetime; print(int(datetime(2026,6,17,8,23,57).timestamp()*1000))"
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --tge T1 --tle T2 ARCHIVE '*'
```

**Use semantic search when field names are uncertain or queries are exploratory:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("task not found errors")'
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("build failures") AND message.content.name:Bash'
```
