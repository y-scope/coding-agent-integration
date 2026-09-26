# Session forensics — queries, catalog SQL, evidence, caveats

Read this when drilling into a finding from the `claude-code-trajectory` pass, or when the user asks one specific question about a session instead of running the full pass.

Write every path out in full. A command with a shell variable (`$B`, `${TMPDIR}`) no longer matches the skill's allowed tools and stops for approval.

## How to drill

The same at every level: **overview** (counts, catalog) → **locate** (a turn, window, category, agent, tool) → **evidence** (the records, projected, in order) → **context** (the records around them, the tool-result file, the launching call) → **check** (confirm with an independent query).

IDs connect the levels: a timestamp opens a window, a `uuid` names a record, a `tool_use_id` pairs a call with its result or a launch with its agent, an `agentId` or `runId` opens a transcript or a run.

## Query efficiency

CLP searches the compressed archive — unmatched records are never decompressed. Push logic into KQL rather than fetching records and post-filtering.

- **Compound KQL over multiple queries:** `field1:A AND field2:B`
- **Count in-engine:** `clp-s-search-kql --count ARCHIVE 'KQL'` prints `{"archive_id":...,"count":N}`, or nothing when zero records match.
- **Project aggressively:** `--projection timestamp,durationMs`. Omit only when you genuinely need whole records.
- **Zoom by time:** `--tge EPOCH_MS --tle EPOCH_MS`. Convert with
  `python3 -c "from datetime import datetime,timezone; print(int(datetime(Y,M,D,h,m,s,tzinfo=timezone.utc).timestamp()*1000))"`
- **Semantic search when field names are uncertain:** `semantic("query")` — the wrapper picks a working endpoint, so no endpoint flags.

## KQL syntax rules

- Time ranges go in `--tge`/`--tle` only — **never** as KQL predicates.
- Array fields use dot notation: `message.content.type:X`, not `message.content[].type:X`.
- **Quote wildcard values:** `key:"*term*"`, not `key:*term*`. An unquoted wildcard with spaces falls through to semantic search and errors.
- **Avoid `NOT` on a field some records lack** — those records drop out entirely. `type:user AND NOT message.content.type:tool_result` returns 5 records on a session with 116 typed prompts. Filter positively.
- `--unique FIELD` returns nothing for a field inside an array.

## Query starters

| Goal | KQL |
| --- | --- |
| Tool calls | `message.content.type:tool_use` |
| Bash calls | `message.content.name:Bash` |
| Command text | `message.content.input.command:*` |
| Edits | `message.content.name:Edit OR message.content.name:MultiEdit OR message.content.name:Write` |
| Tool results | `message.content.type:tool_result OR toolUseResult:*` |
| Failures | `toolUseResult.success:false OR toolUseResult.stderr:* OR level:error` |
| Failed tool calls | `message.content.is_error:true` |
| API/transport errors | `isApiErrorMessage:true OR subtype:api_error OR cause:"*ECONNRESET*"` |
| Interrupts | `message.content.text:"[Request interrupted*"` |
| Permission denials | `message.content.content:"*doesn*t want to proceed*"` |
| Compaction | `subtype:compact_boundary` |
| Truncated reads | `attachment.type:"read_truncation_notice"` |
| Long turns | `subtype:turn_duration AND durationMs >= 30000` |
| Models used | `--unique message.model ARCHIVE '*'` |
| Record kinds | `--unique attachment.type ARCHIVE '*'` and `--unique subtype` |
| Test failures | `"FAILED" OR "AssertionError" OR "Traceback"` |
| Patch failures | `"git apply" AND ("failed" OR "reject" OR "patch does not apply")` |
| Semantic | `semantic("slow operations")`, `semantic("authentication failures")`, `semantic("errors") AND level:error` |

Turn timing comes from `clp-s-session-turns ARCHIVE`, not KQL. Do not add up `subtype:turn_duration` records: they nest inside each other and can be negative.

**If `clp-s-session-turns` or `clp-bundle repo` fails with `Failed to open archive … Error code: 18`**, the search wrapper resolved a `clp-s` too old to read the archive (a stale `/usr/bin/clp-s`, say). Set `CLP_S_BIN` to the build that made the bundle — the catalog records it in its `bundle.clp_s` row — and run again. `clp-session-facts` already does this for its own subprocesses, so the failure only shows up when you call these two by hand.

Field discovery: `clp-s-schema-tree ARCHIVE` lists every field with its type and record count.

## Bundle catalog SQL

`clp-bundle BUNDLE sql "SELECT ..."` is read-only and returns compact rows, so it can run in the main thread without a subagent.

**nodes**(id, kind, label, agent_id, run_id, task_id, instance, phase, start, end, status, cause, attempts, tool_calls, errors, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write, attrs JSON)

- `kind`: `main`, `agent` (direct or nested), `run` (a workflow's definition), `workflow` (one launch; a resume is another, id ending `~2`), `phase`, `unit` (a logical workflow agent), `attempt` (one try of a unit), `launch_error`.
- attempt `status`: `ok`, `failed` (cause `api-503`, `api-400`, `timeout`), `stalled-retried` (no outcome, later retried), `unresolved`.
- agent `status`: `completed`, `failed`, `no-notification`.
- `tokens_*` are what the API reported, each response counted once; input is counted on every call. A run's `attrs.runtime_tokens` is the workflow runtime's own, smaller figure.

**edges**(src, dst, kind, tool_use_id) — `launch`, `result`, `contains`, `executes`, `ran`, `resume`, `phase_order`.

**events**(uuid, kind, pos, agent_id, ts, type, turn, human, interrupt, is_error, ref_agent_id, ref_task_id, message_id, tokens_*) — one row per user or assistant record, no text. `tokens_*` are set on one record per model response only, so a sum counts each call once; `tokens_input` is the context sent with that call. An agent's events carry the main thread's turn at their time.

**event_tools**(event → events.id, tool_use_id, role `use`|`result`, name, is_error).

**Outcomes:** `file_versions`(turn, ts, path, file_hash, version, backup) from the harness's file history (main thread only; `backup` is the earlier content under `files/`); `actions`(uuid, kind, agent_id, turn, ts, action `commit`|`pr`|`test`, failed, confirmed, branch, sha, pr_url, tests_passed, tests_failed); and the view `file_changes`(uuid, kind, agent_id, turn, ts, name, file_hash) of successful edits and writes.

**archives**(kind, archive_id) — kinds `main`, `agent`, `workflow-agent`, `workflow-journal`, `workflow-run`.

### Starter SQL

```sql
-- failures by cause
select kind, status, cause, count(*) n from nodes where kind in ('agent','attempt') group by 1,2,3 order by n desc

-- workflow runs by wasted attempts
select r.label, r.status, count(*) attempts, sum(a.status != 'ok') not_ok
from nodes a join nodes r on r.id = 'run:' || a.run_id
where a.kind = 'attempt' group by a.run_id order by not_ok desc

-- longest agents
select id, label, status, round((julianday(end) - julianday(start)) * 1440, 1) minutes
from nodes where kind = 'agent' order by minutes desc limit 5

-- tokens by agent
select id, label, tokens_input, tokens_output from nodes where kind in ('agent','run')
order by tokens_input desc limit 10

-- context over time for one agent (a sharp drop is a compaction)
select ts, tokens_input context, tokens_output from events where agent_id = 'ID' and tokens_input is not null order by ts

-- error rate per tool, across every agent
select u.name, count(*) calls, sum(r.is_error) errors from event_tools u
join event_tools r on r.tool_use_id = u.tool_use_id and r.role = 'result'
where u.role = 'use' group by 1 order by errors desc

-- tool calls that never got a result (log-integrity finding)
select count(*) from event_tools u where u.role = 'use'
and not exists (select 1 from event_tools r where r.tool_use_id = u.tool_use_id and r.role = 'result')

-- last tool before each stall
select coalesce((select t.name from events e join event_tools t on t.event = e.id and t.role = 'use'
  where e.agent_id = n.agent_id order by e.ts desc, e.id desc limit 1), '(none)') last_tool, count(*) n
from nodes n where n.kind = 'attempt' and n.status = 'stalled-retried' group by 1 order by n desc

-- runtime honesty: instances reporting completed that hold failing attempts
select w.status, count(distinct w.id) launches, sum(case when x.not_ok > 0 then 1 else 0 end) with_failures
from nodes w left join (select run_id, sum(status != 'ok') not_ok from nodes where kind = 'attempt' group by 1) x
on x.run_id = w.run_id where w.kind = 'workflow' group by 1
```

## Evidence commands

```bash
clp-bundle BUNDLE show ID          # catalog row, parents, children, archive and query
clp-bundle BUNDLE evidence ID      # its records, time-ordered; a run's evidence is its runtime log
clp-bundle BUNDLE record UUID      # one full record
clp-bundle BUNDLE context UUID --before N --after N   # the records around it, in its own log and order
clp-bundle BUNDLE events --agent ID --tool Bash --errors --interrupts
clp-bundle BUNDLE who --uuid UUID  # also --agent-id, --tool-use-id, --at YYYY-MM-DDTHH:MM (UTC)
clp-bundle BUNDLE outcomes         # per turn; --by agent for per-agent
clp-bundle BUNDLE repo             # asks git and gh which commits and PRs each command actually made
```

`ID` is a node id, an agent id or prefix, a run id or a task id.

KQL over one kind of log: `clp-s-search-kql --archive-id ID BUNDLE/archives 'KQL'`. Turn time and field discovery read the main log only — pass `BUNDLE/archives/<main archive id>`.

**To say why something failed, read one example of each cause before explaining it.** Evidence on a stalled attempt shows its last tool result, the silence and the runtime's interrupt; evidence on `run:ID` shows the runtime's stall and API-error lines. The `cause` column is a label, not an explanation.

## Grouping errors by template

Every failed tool call carries its error as the top-level string `toolUseResult`; `shape()` cannot reach the text inside `message.content`.

```bash
clp-s-search-kql --experimental --projection 'uuid,shape(toolUseResult)' ARCHIVE 'message.content.is_error:true'
```

Then open one template's records: keep each record's `uuid` from that projection (exact), or filter with the template's literal text and `*` for each variable — `shape(toolUseResult): "Error: Exit code*"` (a superset; a typed `%int%` matches nothing). Then `clp-bundle BUNDLE context UUID`.

## Conclusions the logs do not support

- **A workflow reporting `completed` did not necessarily succeed.** The runtime reports it that way with agents stalled or failed. Count attempts by status.
- **`phase_order` edges are not an order of work.** Phases overlap; workflows are pipelined.
- **No log records data flow between agents.** You cannot say which agent's output fed which.
- **One run is not one launch.** A resume reuses the run id, so compare its workflow instances.
- **The last tool before a silence is not the tool that hung.** Check the timestamps: the tool result often returns normally and the silence falls in the model turn after it.
- **Parallel agent time is not wall-clock time.** Summed attempt minutes routinely exceed the session's span.
- **Command output understates outcomes.** Report a commit or PR as existing only from `clp-bundle repo`, and say how it matched (exact, time+subject, time) — never from a command having run.
- **The logs record activity, not value.** They cannot say whether the work was any good.

## Reviewing many sessions for harness issues

When the goal is the harness itself rather than one task, look for problems recurring across sessions and projects. `clp-bundle-review` does the whole pass: it bundles every Claude Code session under a Claude home, computes the signals below for each, ranks them, and groups failed tool calls across sessions.

```bash
clp-bundle-review /tmp/yscope-clp-bundles --build --skip <THIS_SESSION_ID>
```

Pass `--skip` with the id of the session you are running in — it is still being written. Add `--trend week` (or `day`) for signals per period with volume, session count and a 95% interval; a change is flagged only between periods with enough volume. **A period resting on one or two sessions is about those sessions, not a trend — say so.** `--json` makes it a metrics feed.

Error grouping sends the template text of non-command error messages (variables already replaced by `<*>`) to the plugin's built-in semantic endpoint. **Tell the user before running it on logs they have not agreed to send**, and relay an endpoint error as it is.

What it checks, and how to check one session by hand:

- **Log integrity** (any occurrence is a finding): a build's `REPAIRED` line (NUL bytes from a lost write), a record cut off mid-write, a tool call with no result, an agent launch with no completion notification (status `no-notification`).
- **Runtime honesty:** a workflow reported `completed` whose attempts did not all succeed; a resume that re-ran work already done; the harness's own `turn_duration` records, which nest and can be negative.
- **Retries and stalls:** attempts `stalled-retried` (with evidence: what came before the silence), runtime `[stall]` lines (`evidence run:ID`), retry budgets reached.
- **Configuration and provider:** API errors by status. A 400 such as an unknown model name is a configuration error; 503 and 529 are capacity. Timeouts are their own class.
- **Harness tool contracts:** errors from the harness's own tools, such as `StructuredOutput` schema mismatches; truncated reads; rejected workflow launches (`launch_error` nodes — in practice usually prose pasted into a script literal: an em dash, an unescaped apostrophe, an unterminated string).
- **Overhead:** harness-injected records (hook results, reminders) as a share of all records; compactions per hour.
- **Waiting on the human:** long `AskUserQuestion` waits, idle share, denials.
- **Wasted calls** (rare; check strictly so they are not noise): a failed call retried at once with identical input, a file re-read when nothing could have changed it. Literal loops of identical calls were almost absent across 71,203 calls — do not report repetition as a loop without evidence.

Report each finding with its count, its rate against the session's own totals, and one example someone can open, and say whether it is a harness, provider, model, task or environment problem — or that the logs cannot tell them apart. A message recurring across unrelated projects points at the harness or provider; one tied to a single repository points at the task.
