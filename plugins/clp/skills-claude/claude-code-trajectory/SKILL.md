---
name: claude-code-trajectory
description: Analyze Claude Code session logs — list, compress, search, and decompress trajectories.
allowed-tools:
  - "Agent"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-session-turns:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle:*)"
---

# Claude Code Trajectory

End-to-end workflow for analyzing a Claude Code session log with CLP. Use this when the user asks to investigate what happened in a Claude session: which tools fired, what failed, how long a turn took, what context was used.

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — the single subagent at step 5 — and fan the queries out across more agents only if the user asks for it.

For general-purpose KQL search (no session involved), use the `search` skill instead.

## Workflow

1. List sessions, newest first:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
   ```

Use `--agent claude` (default) or `--agent codex` if the user asks.

2. Present choices with these columns: `IDX`, `AGENT`, modified timestamp, raw bytes, human size, session name, project/cwd, session ID.

3. Compress the selected `IDX`:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index <IDX> \
     --timestamp-key timestamp
   ```

4. Report compression stats: raw input bytes, archive bytes, compression ratio, file size reduction.

   Then check whether the session launched agents or workflows:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --count ARCHIVE 'message.content.name:Agent OR message.content.name:Workflow'
   ```

   If it did (any count), the main log is not the whole session: it records only each launch, and
   `Agent` returns `async_launched` at once. What each agent did, how long it ran, what failed and
   what was retried is in other files. Build a **bundle** of the session (see Multi-agent sessions
   below) and pass it to the subagent as BUNDLE alongside ARCHIVE:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> build --session-id <SESSION_ID>
   ```

   Write every path out in full, as here: a command with a shell variable (`$B`, `${TMPDIR}`) no longer
   matches this skill's allowed tools, so it waits for approval.

   If that directory already holds a bundle (it has `manifest.json`), reuse it; add `--force` only
   when the session has changed since. Relay any `REPAIRED` line to the user: that log had NUL bytes
   from a lost write, which the build removed (the source file is untouched).

5. **Spawn a subagent to run all searches.** Use the Agent tool and leave the model unset, so the subagent uses the session's model: the subagent's job is interpretation as much as querying (parallel agent time is not wall-clock time, a status is not an outcome, the last tool before a silence is not the tool that hung), and a small model got those wrong in testing where the session's model did not. Pick a smaller model only for a subagent that just counts or fetches. The subagent runs searches, processes raw JSON, and returns only a compact report — keeping the main context clean. Catalog SQL on a bundle returns compact rows, so a few such queries can also run in the main thread without a subagent.

Subagent prompt template (fill in `ARCHIVE`, `PLUGIN_BIN`, `GOAL`, and `BUNDLE` for a multi-agent session; otherwise drop the multi-agent block):

   ```
   Analyze this Claude Code CLP session archive: ARCHIVE

   Search wrapper: PLUGIN_BIN/clp-s-search-kql
   Goal: GOAL

   Efficiency rules (follow strictly):
   - Use compound KQL instead of multiple queries: field1:A AND field2:B
   - Count with: clp-s-search-kql --count ARCHIVE 'KQL' (in-engine; prints nothing when zero records match)
   - Project aggressively — fetch only the fields you need, not full records: pass
     `--projection COLUMNS` (comma-separated) for the required columns (e.g.
     `--projection timestamp,durationMs`).
     Only omit --projection when you genuinely need the whole record.
   - Zoom into time windows: --tge EPOCH_MS --tle EPOCH_MS (NOT timestamp KQL)
   - Use semantic("query") when field names are uncertain

   KQL syntax rules (do not violate):
   - Time ranges: ONLY via --tge/--tle flags with epoch ms — NEVER as KQL predicates
   - Array fields: dot notation only — message.content.type:X NOT message.content[].type:X
   - Avoid NOT on a field that some records lack: those records drop out entirely.
     `type:user AND NOT message.content.type:tool_result` returns 5 records on a
     session with 116 typed prompts. Filter positively instead.
   - Convert timestamps: python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"

   Semantic search: use semantic("query") — the wrapper auto-selects a working
   endpoint, so no endpoint flags are needed.

   Suggested starting queries:
   - Fields: PLUGIN_BIN/clp-s-schema-tree ARCHIVE lists every field with its type and record count
   - Tool call breakdown: --count per tool with message.content.name:TOOL (Bash, Edit, Read, Write, Agent, ...); --unique returns nothing for a field inside an array
   - Failures: toolUseResult.success:false OR toolUseResult.stderr:* OR level:error
   - Turn time: PLUGIN_BIN/clp-s-session-turns ARCHIVE splits each turn (one human prompt to the next) into human wait, tool, model, idle and other time, and lists the longest tool waits. Do not add up subtype:turn_duration records: they nest inside each other and can be negative.
   - Compaction: subtype:compact_boundary

   Multi-agent session: BUNDLE is a bundle of the whole session (main log, every agent's
   transcript, workflow runs and journals). Answer structure in SQL first, then fetch evidence:
   - PLUGIN_BIN/clp-bundle BUNDLE sql "SELECT ..." (read-only) over the catalog:
     nodes(id, kind, label, agent_id, run_id, task_id, instance, phase, start, end, status, cause,
     attempts, tool_calls, errors, tokens, attrs JSON); kind is main, agent (direct or nested),
     run (a workflow's definition), workflow (one launch of it; a resume is another, id ending ~2),
     phase, unit (a logical workflow agent), attempt (one try of a unit), launch_error. Attempt
     status: ok, failed (cause api-503, api-400, timeout), stalled-retried (no outcome, later
     retried), unresolved. Agent status: completed, failed, no-notification.
     edges(src, dst, kind: launch, result, contains, executes, ran, resume, phase_order; tool_use_id).
     events(uuid, kind, pos, agent_id, ts, type, turn, human, interrupt, is_error, ref_agent_id,
     ref_task_id) with event_tools(event -> events.id, tool_use_id, role use|result, name, is_error):
     one row per user or assistant record, no text.
   - Starter SQL:
     failures by cause: select kind, status, cause, count(*) n from nodes where kind in ('agent','attempt') group by 1,2,3 order by n desc
     workflow runs by wasted attempts: select r.label, r.status, count(*) attempts, sum(a.status != 'ok') not_ok from nodes a join nodes r on r.id = 'run:' || a.run_id where a.kind = 'attempt' group by a.run_id order by not_ok desc
     longest agents: select id, label, status, round((julianday(end) - julianday(start)) * 1440, 1) minutes from nodes where kind = 'agent' order by minutes desc limit 5
     last tool before each stall: select coalesce((select t.name from events e join event_tools t on t.event = e.id and t.role = 'use' where e.agent_id = n.agent_id order by e.ts desc, e.id desc limit 1), '(none)') last_tool, count(*) n from nodes n where n.kind = 'attempt' and n.status = 'stalled-retried' group by 1 order by n desc
   - Evidence: PLUGIN_BIN/clp-bundle BUNDLE show ID (catalog row, parents, children, archive and
     query) and evidence ID (its records, time-ordered; a run's evidence is its runtime log, with
     stall and API-error lines). ID is a node id, an agent id or prefix, a run id or a task id.
   - Back from a record to the graph: who --uuid UUID (the agent it belongs to, what it launched or
     reports on), who --agent-id, who --tool-use-id, who --at YYYY-MM-DDTHH:MM (UTC).
   - Records by agent, turn, tool or flag: events --agent ID --tool Bash --errors --interrupts;
     one full record: record UUID.
   - KQL over one kind of log: PLUGIN_BIN/clp-s-search-kql --archive-id ID BUNDLE/archives 'KQL'
     (IDs: sql "select kind, archive_id from archives"; kinds main, agent, workflow-agent,
     workflow-journal, workflow-run). Turn time and fields read the main log only: pass
     BUNDLE/archives/<main archive id> to clp-s-session-turns and clp-s-schema-tree.
   To say why something failed, read one example of each cause before explaining it: evidence on a
   stalled attempt shows its last tool result, the silence and the runtime's interrupt; evidence on
   run:ID shows the runtime's stall and API-error lines. The cause column is a label, not an explanation.
   Write paths out in full; a command with a shell variable needs approval.
   Do not conclude:
   - that a workflow succeeded from status "completed": the runtime reports it with agents stalled
     or failed; count attempts by status;
   - an order of work from phase_order edges: phases overlap (workflows are pipelined);
   - which agent's output fed which: no log records data flow between agents;
   - that one run is one launch: a resume reuses the run id, so compare its workflow instances.

   Return ONLY (no raw JSON, no header lines):
   1. Archive path
   2. Queries run (KQL string only, one per line)
   3. Key findings (≤10 bullets, concrete numbers and facts)
   4. 2–3 follow-up queries worth running
   ```

6. Before presenting the subagent's report, check any finding that is surprising, or that a caveat in the prompt warns about, with one query of your own (a bundle's `sql` or `evidence`, or a `--count`), and correct it rather than relaying it. Then present the compact report to the user. Offer to drill deeper with a follow-up subagent or decompress for raw inspection:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
     /tmp/session-archive \
     /tmp/session-archive-decompressed
   ```

If the user provides an archive path directly, skip listing/compression and go straight to step 5.

## When the user does not know what to look for

Most people ask "what happened?" or "what went wrong?" without a question in mind. Do not guess one:
run the health check below, report what stands out with numbers, then offer the question menu.

**Health check** (one pass, counts only; ARCHIVE is the main log's archive, BUNDLE the bundle if any):

| Signal | Command | Read it as |
| --- | --- | --- |
| Where time went | `clp-s-session-turns ARCHIVE` | Model, human wait, tool, idle per turn; the longest waits |
| Tool errors | `clp-s-search-kql --count ARCHIVE 'message.content.is_error:true'` | Failed tool calls on the main thread |
| API errors | `clp-s-search-kql --count ARCHIVE 'isApiErrorMessage:true'` | Provider or gateway failures |
| Interrupts | `clp-s-search-kql --count ARCHIVE 'message.content.text:"[Request interrupted*"'` | The user (or the runtime) stopped a response |
| Permission denials | `clp-s-search-kql --count ARCHIVE 'message.content.content:"*doesn*t want to proceed*"'` | Tool calls the user refused |
| Compactions | `clp-s-search-kql --count ARCHIVE 'subtype:"compact_boundary"'` | Context was summarized; work before it may be lost to the model |
| Truncated reads | `clp-s-search-kql --count ARCHIVE 'attachment.type:"read_truncation_notice"'` | The model saw only part of a file |
| Harness record kinds | `clp-s-search-kql --unique attachment.type ARCHIVE '*'` and `--unique subtype` | Look for error-like kinds you did not expect |
| Agents and workflows | `clp-bundle BUNDLE sql "select kind, status, cause, count(*) from nodes where kind in ('agent','attempt') group by 1,2,3"` | Failed, stalled and unresolved work |
| Error rate per tool | `clp-bundle BUNDLE sql "select u.name, count(*) calls, sum(r.is_error) errors from event_tools u join event_tools r on r.tool_use_id = u.tool_use_id and r.role = 'result' where u.role = 'use' group by 1 order by errors desc"` | Which tools fail, across every agent |

**Question menu** (offer it; each row names where to start and where to drill):

| The user wants to know | Start with | Drill down to |
| --- | --- | --- |
| What happened | Turns and their prompts (`clp-s-session-turns`); the log shape dictionary (`log-insights` skill) | One turn, its time window (`--tge/--tle`), its records |
| Where the time went | `clp-s-session-turns` | The longest turn, its longest wait, that tool call and its output |
| What failed, and whether it recovered | Error counts; error messages grouped by CLP's template: `clp-s-search-kql --experimental --projection 'uuid,shape(toolUseResult)' ARCHIVE 'message.content.is_error:true'` (every failed tool call carries its error as the top-level string `toolUseResult`; `shape()` cannot reach the text inside `message.content`) | One template's records: keep each record's `uuid` from that projection (exact), or filter with the template's literal text and `*` for each variable, as in `shape(toolUseResult): "Error: Exit code*"` (a superset: a typed `%int%` matches nothing); then the records just before and after |
| Whether effort was wasted | Repeated templates (the same command shape many times) | Each occurrence and what followed it |
| What it cost | `message.usage.*_tokens` on assistant records; compactions | The heaviest turns or agents |
| What it produced | `type:"pr-link"` records; edits (`toolUseResult.structuredPatch`, `toolUseResult.filePath`) | The turn or agent that made a PR, the edits and test runs before it |
| How often the human stepped in | Prompts, interrupts, denials, `AskUserQuestion` waits | The prompt and what preceded it |
| Which agents did what, and why they failed | Bundle catalog SQL | `clp-bundle show`, `evidence`, then back with `who --uuid` |

Drilling down is the same at every level: overview (counts, templates, catalog) → locate (a turn,
window, template, agent, tool) → evidence (the records, projected, in order) → context (the records
around them, the tool-result file, the launching call) → check (confirm with an independent query).
IDs connect the levels: a timestamp opens a window, a `uuid` names a record, a `tool_use_id` pairs a
call with its result or a launch with its agent, an `agentId` or `runId` opens a transcript or a run.

## Reviewing sessions for harness issues

When the goal is the harness itself (Claude Code, a gateway, a workflow runtime) rather than the task,
look for problems that recur across sessions and projects. A message that repeats across unrelated
projects points at the harness or provider; one tied to a single repository points at the task.

- **Log integrity** (any occurrence is a finding): a build's `REPAIRED` line (NUL bytes from a lost
  write), a record cut off mid-write, a tool call with no result
  (`sql "select count(*) from event_tools u where u.role = 'use' and not exists (select 1 from event_tools r where r.tool_use_id = u.tool_use_id and r.role = 'result')"`),
  an agent launch with no completion notification (agent status `no-notification`).
- **Runtime honesty:** a workflow reported `completed` whose attempts did not all succeed; a resume
  that re-ran work already done; the harness's own `turn_duration` records, which nest and can be
  negative (use `clp-s-session-turns` instead).
- **Retries and stalls:** attempts `stalled-retried` (with evidence: what came before the silence),
  runtime `[stall]` log lines (`evidence run:ID`), retry budgets reached.
- **Configuration and provider:** API errors by status (a 400 such as an unknown model name is a
  configuration error; 503 and 529 are capacity), timeouts.
- **Harness tool contracts:** errors from the harness's own tools, such as `StructuredOutput` schema
  mismatches (the error rate per tool query above), truncated reads, rejected workflow launches
  (`launch_error` nodes).
- **Overhead:** harness-injected records (hook results, reminders) as a share of all records, found by
  counting templates; compactions per hour.
- **Waiting on the human:** long `AskUserQuestion` waits, idle share, denials.

Report each finding with its count, its rate against the session's own totals, and one example
(`uuid` or node id) someone can open, and say whether it is a harness, provider, model, task or
environment problem, or that it cannot be told apart from the logs.

## Query Starters

For broad trajectory debugging, suggest using a subagent and ask it to return only archive path, queries, top findings, and next queries.

| Goal | KQL |
| --- | --- |
| Claude tool calls | `message.content.type:tool_use` |
| Claude Bash calls | `message.content.name:Bash` |
| Claude command text | `message.content.input.command:*` |
| Claude edits | `message.content.name:Edit OR message.content.name:MultiEdit OR message.content.name:Write` |
| Claude tool results | `message.content.type:tool_result OR toolUseResult:*` |
| Claude failures | `toolUseResult.success:false OR toolUseResult.stderr:* OR level:error` |
| Claude API/transport errors | `isApiErrorMessage:true OR subtype:api_error OR cause:"*ECONNRESET*"` |
| Claude time per turn | `clp-s-session-turns ARCHIVE` (not a KQL query) |
| Claude turn_duration records | `subtype:turn_duration AND durationMs >= 30000` (these nest inside each other, so use them only to find a moment, not to add up time) |
| Claude compaction | `subtype:compact_boundary` |
| Multi-agent: failures and retries per agent | `clp-bundle BUNDLE sql "select kind, status, cause, count(*) from nodes where kind in ('agent','attempt') group by 1,2,3"` (not KQL; see Multi-agent sessions) |
| Multi-agent: one agent's story | `clp-bundle BUNDLE show AGENT_ID`, then `clp-bundle BUNDLE evidence AGENT_ID` |
| Harness runs | `"swebench.harness.run_evaluation" OR "run_evaluation"` |
| Harness reports | `"report.json" OR "instance_results.jsonl" OR "results.json"` |
| Test failures | `"FAILED" OR "AssertionError" OR "Traceback"` |
| Patch failures | `"git apply" AND ("failed" OR "reject" OR "patch does not apply")` |
| Docker/resource issues | `"docker" AND ("failed" OR "No space left" OR "permission denied")` |
| Semantic: slow operations | `semantic("slow operations")` |
| Semantic: authentication issues | `semantic("authentication failures")` |
| Semantic: network errors | `semantic("network timeout or connection errors")` |
| Semantic: combined with KQL | `semantic("errors") AND level:error` |

Combine a user-provided repo, file, command, test, or instance ID with a starter query using `AND`.

## Analysis Patterns

CLP searches the compressed archive — unmatched records are never decompressed. Push logic into KQL rather than fetching all records and post-filtering in shell or Python.

**For analyses that run 3+ queries, spawn a subagent:**
- Leave the model unset so the subagent uses the session's model; use a smaller one only for pure counting or fetching.
- Brief the subagent with the archive path and the analysis goal.
- Ask it to return only: archive path, queries run, key findings, and next useful queries.
- Check surprising findings with a query of your own before relaying them.
- This keeps the parent context lean and parallelizes independent query batches.

**Count matches with `--count` (in-engine, no records serialized; prints `{"archive_id":...,"count":N}`, or nothing when zero records match):**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --count ARCHIVE 'message.content.name:Bash'
```

**Compound KQL — one query instead of multiple + joins:**
```bash
# Long turns only
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'subtype:turn_duration AND durationMs >= 30000'
# Bash calls matching a keyword
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'message.content.name:Bash AND message.content.input.command:"*cargo*"'
# Failures with stderr output
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'toolUseResult.success:false AND toolUseResult.stderr:*'
```

**Reduce payload — project only the fields you need, by default:** full records are large; fetch only the columns your analysis uses.
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --projection timestamp,durationMs \
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

## Multi-agent sessions

A session that launches agents or workflows is spread over many files: the main log, one transcript
per agent (thousands for workflow-heavy sessions), workflow summaries and journals, and the agents'
meta files. `clp-bundle` keeps them as one bundle: one CLP archive per kind of log, plus a SQLite
catalog of how they connect (who launched whom, workflow runs and their resumed instances, retried
attempts with a failure cause, timing, and one row per record). SQL answers structure; CLP holds the
records; the two point at each other by the IDs the records carry.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> build --session-id <SESSION_ID>   # or --session-file PATH.jsonl
"${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> sql "select kind, status, cause, count(*) from nodes where kind in ('agent','attempt') group by 1,2,3"
"${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> show <AGENT_ID>
"${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> evidence <AGENT_ID>
"${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> who --uuid <UUID>
```

- Building takes seconds (a 290 MB session with 1,426 agent transcripts: about 12 s). A session with
  no agents gets a catalog with only its main thread, so build a bundle only when step 4 finds launches.
- The build stops on a file it does not recognize, a line that is not JSON, or a last record cut off
  mid-write, naming file and line. A cut-off last record usually means the session is still running:
  tell the user and build once it has stopped. The session you are running in is still being written,
  so a bundle of it would be incomplete even when the build succeeds.
- A catalog from an older plugin version is refused with the command that rebuilds it
  (`clp-bundle BUNDLE rebuild`); run it.
- Claude Code sessions only.

