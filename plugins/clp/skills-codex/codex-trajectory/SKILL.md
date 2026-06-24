---
name: codex-trajectory
description: Analyze Codex session logs — list, compress, search, and decompress trajectories.
---

# Codex Trajectory

End-to-end workflow for analyzing a Codex session log with CLP. Use this
when the user asks to investigate what happened in a Codex session: which
tools fired, what failed, how long a turn took, what context was used.

For general-purpose KQL search (no session involved), use the `search`
skill instead.

## Workflow

1. List sessions, newest first:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-list-sessions
   ```

   Use `--agent codex` (default) or `--agent claude` if the user asks.

2. Present choices with these columns: `IDX`, `AGENT`, modified timestamp,
   raw bytes, human size, session name, project/cwd, session ID.

3. Compress the selected `IDX`:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-compress-session \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index <IDX> \
     --timestamp-key timestamp
   ```

4. Report compression stats: raw input bytes, archive bytes, compression
   ratio, file size reduction.

5. **Spawn a subagent to run all searches.** Use model `haiku` (fall back to
   `sonnet`). The subagent runs searches, processes raw JSON, and returns only
   a compact report — keeping the main context clean.

   Subagent prompt template (fill in `ARCHIVE`, `PLUGIN_BIN`, `GOAL`):

   ```
   Analyze this Codex CLP session archive: ARCHIVE

   Search wrapper: PLUGIN_BIN/clp-s-search-kql
   Goal: GOAL

   Efficiency rules (follow strictly):
   - Use compound KQL instead of multiple queries: field1:A AND field2:B
   - Count with: clp-s-search-kql ARCHIVE 'KQL' | grep -c '^{'
   - Fetch only needed fields: --project timestamp --project fieldName
   - Zoom into time windows: --tge EPOCH_MS --tle EPOCH_MS (NOT timestamp KQL)
   - Use semantic("query") when field names are uncertain

   KQL syntax rules (do not violate):
   - Time ranges: ONLY via --tge/--tle flags with epoch ms — NEVER as KQL predicates
   - Array fields: dot notation only — payload.type:X NOT payload[].type:X
   - Convert timestamps: python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"

   Semantic search: use semantic("query") — the wrapper auto-selects a working
   endpoint and shares a local cache across sessions, so no endpoint flags are
   needed.

   Suggested starting queries:
   - Tool call breakdown: run one query per tool name using payload.name:TOOL | grep -c '^{'
   - Failures: payload.type:function_call_output AND payload.success:false
   - Stderr output: payload.stderr:*
   - Token usage: payload.type:token_count OR payload.info.total_token_usage.total_tokens:*

   Return ONLY (no raw JSON, no header lines):
   1. Archive path
   2. Queries run (KQL string only, one per line)
   3. Key findings (≤10 bullets, concrete numbers and facts)
   4. 2–3 follow-up queries worth running
   ```

6. Present the subagent's compact report to the user. Offer to drill deeper
   with a follow-up subagent or decompress for raw inspection:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress \
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
| Codex metadata | `type:session_meta` |
| Codex events | `type:event_msg` |
| Codex tool calls | `payload.type:function_call` |
| Codex shell calls | `payload.type:function_call AND payload.name:exec_command` |
| Codex tool outputs | `payload.type:function_call_output` |
| Codex failed tools | `payload.type:function_call_output AND payload.success:false` |
| Codex stderr | `payload.stderr:*` |
| Codex token/context | `payload.type:token_count OR payload.info.total_token_usage.total_tokens:*` |
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
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'payload.type:function_call' | grep -c '^{'
```

**Compound KQL — one query instead of multiple + joins:**
```bash
# Failed tool calls only
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'payload.type:function_call_output AND payload.success:false'
# Shell calls matching a keyword
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'payload.name:exec_command AND payload.cmd:*cargo*'
# Failures with stderr
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'payload.success:false AND payload.stderr:*'
```

**Reduce payload — fetch only needed fields with `--project`:**
```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql \
  --project timestamp --project payload.name \
  ARCHIVE 'payload.type:function_call'
```

**Zoom into a time window with `--tge`/`--tle` (epoch milliseconds):**
```bash
# Convert: python3 -c "from datetime import datetime; print(int(datetime(2026,6,17,8,23,57).timestamp()*1000))"
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql --tge T1 --tle T2 ARCHIVE '*'
```

**Use semantic search when field names are uncertain or queries are exploratory:**
```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("task not found errors")'
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("build failures") AND payload.name:exec_command'
```
