---
name: analyze-logs
description: One entry point for analyzing logs with CLP — any logs, whether a Claude Code session or an application's own (vLLM, MongoDB, nginx, CockroachDB, …). One command identifies which application wrote the logs and routes the run from there: a Claude Code session gets the seven fixed session categories (reliability, cost, time, outcomes, harness faults, human loop, rework), the bundle of every agent and workflow it launched, and optional 0–10 scoring; anything else gets the log-shape-baseline pass — dump the archive's template dictionary, classify the real templates into categories (cached per application and updated incrementally), and drive targeted KQL from templates that are known to exist, never blind keywords. Both routes end in a checked report you can save. Use it for "analyse these logs", "what's in this vLLM log", "what happened in my Claude Code session", "why did that agent run take so long", or any end-to-end log or session investigation. Also lists, searches and decompresses sessions and archives for ad-hoc questions.
allowed-tools:
  - "Agent"
  - "AskUserQuestion"
  - "Artifact"
  - "Monitor"
  - "TaskStop"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp:*)"
  - "Bash(python3:*)"
  - "Bash(grep:*)"
  - "Bash(sort:*)"
  - "Bash(uniq:*)"
  - "Bash(head:*)"
  - "Bash(tail:*)"
  - "Bash(cat:*)"
  - "Bash(wc:*)"
  - "Bash(sed:*)"
  - "Bash(cut:*)"
  - "Bash(printf:*)"
  - "Bash(echo:*)"
---

# Analyze Logs

End-to-end analysis of any logs with CLP: prepare the target, find out which application wrote it, then run the pass that application's records deserve and write a report.

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not. (This governs environment/setup problems only. The one retry the workflow itself specifies — re-running a subagent once when it returns unusable output at the general route's steps 6 and 9 — is part of the task and still applies.)

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — on the specialised route, the extras subagent at step 3 and the report writer in the report phase; on the general route, the baseline query pool running in the background from step 4, the single classification subagent at step 6 with the cache store running in the background after it, the plan pool running in the background from step 7 while the user picks a focus, and the report writer in the report phase — each spawned only where it is called for, and fan the queries or templates out across more agents only if the user asks for it.

There is one pipeline — acquire, categorise, measure, report — and it runs on logs. A Claude Code session is not a different kind of thing from a vLLM worker log; it is logs from a particular application. So this skill asks one question first, with one command, and the answer picks the route:

- **`ROUTE=specialised`** — a Claude Code session. Its file layout, its record graph and its seven categories are known in advance, so there is nothing to classify and nothing to cache: the run is the seven fixed checks, a subagent looking for what they miss, a focus question, and optional scoring.
- **`ROUTE=general`** — anything else, on its own terms. Discover the structure and the categories from the logs with the **log shape baseline** method: dump the archive's log shape dictionary (the complete vocabulary of distinct message templates, `<*>` marking variables — tens to a few hundred templates no matter how many millions of records), classify those *real* templates into categories, cache the classification per application, and derive every later query from a template that is guaranteed to exist. No blind keyword batteries.

Both routes share this skill's opening (the one command, and reading what it reports) and its close (the report, the check, saving it, the closing message). Everything between them is the route's own, and each route has its own five phases — do not blend them.

For a single ad-hoc KQL query, use the `search` skill. To compress raw logs before analysing them, use `compress-folder`.

## References — read on demand, not up front

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/evidence-tiers.md` — **both routes.** Read before the first figure you quote: the four evidence tiers, and the three things that look measured and may not be: a category, a score, and a value that may be a placeholder.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/report-style.md` — **both routes**, and the report writer's prompt has it read: the shape of a report. Short and skimmable by default, the thorough form only when the user asks, every argument marked as one, and the collapsed reference section its claims link into.
- `${CLAUDE_PLUGIN_ROOT}/writing-guide/rules.md` — **both routes**, read by the report writer alongside the file above, and worth reading before you write anything to the user: how a sentence reads, whether a claim is one the evidence supports, and (Part 8) how a finished report lands on someone skimming it. It is plain Markdown at the plugin root so that a person can edit it; its evidence is in `writing-guide/examples.md` beside it.
- `${CLAUDE_PLUGIN_ROOT}/writing-guide/humanizer.md` and `${CLAUDE_PLUGIN_ROOT}/writing-guide/sound.md` — **both routes**, read by the report writer as a revision pass before it saves. The first is the 25 AI-writing patterns, vendored verbatim under MIT; the second says which ones a report trips over and which of a report's habits they would wrongly cut.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-insight.md` — **specialised route**, read at its step 2: the seven categories, the questions' wording, the extras and report-writer prompts, the focus-shift rule, the scorecard, the report format.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-forensics.md` — **specialised route**, read when drilling into a finding, or when the user asks an ad-hoc question instead of running the full pass: KQL starters, bundle SQL, evidence commands, the harness-review checklist, and the conclusions the logs do not support.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` — **general route**, read at its step 6: the classification subagent prompt, the cluster/expand contract, the merge and cache store commands.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-insights.md` — **general route**, read at its step 7: the questions, summary, extract, query pool and focus commands, the report-writer prompt, the save question and commands, the report format.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-baseline.md` — **general route**, read only if the dump/fallback misbehaves or when drilling into individual templates (stats.log_shapes encodings, CLP-string limitation, retrieve/count/analysis patterns, semantic-search flags).

## Supported inputs

Anything the one command in phase 1 can read. It decides the route from the records, so you do not have to know which kind of log you were handed.

- A CLP archive directory (any kind).
- Raw log files or folders. Compression is the one app-specific step and phase 1 does it, picking the settings from the detector's own evidence. To compress by hand instead, the `compress-folder` skill does it: `clp detect` shows what the first 128 KiB of each file holds (JSON structure and timestamp field, or text lines), you pick the flags from that report (`--timestamp-key <field>` for JSON, `--structurize` for vLLM text, a parser you write for other text), and `clp compress folder --path ...` compresses.
- A Claude Code session picked from the list (the default when nothing was named), or a session id or `.jsonl` path the user names — which skips listing.
- A session bundle directory, which skips straight to the specialised route's step 2.
- Sessions not under `~/.claude`. The two flags are one level apart, on purpose: `--claude-root DIR` when listing wants `projects/` itself, `--claude-home DIR` when bundling wants the directory above it (it also reads `tasks/` and `file-history/`). Each refuses the other's level and names the correction.
- If nothing was provided, ask for an archive, a log file, a folder, or a session — or run the command with no target and present the session list it prints.

## Talking to the user

The user sees your messages, not the tools' output. Keep every message professional and short, and make each one tell the user something they did not know.

- **Five phases, numbered** — but **which five depends on the route**, so state them once the route is known, not before. The two lists are under each route below, and phase 5 is the report on both. Open each phase with one line: what it does and, when it can take over 30 s, how long. Close it with one line: what it found. A phase this run does not need gets one line saying why, before it is skipped (`[3/5] Classify: skipped, this app was classified on an earlier run`).
- **Lead with what was learned, not what ran.** "593 of 1,380 agent attempts stalled and were retried", not "ran the reliability SQL". "100 of 16.5M records are warnings or errors; fetching them all", not "Baseline #2: `NOT severity:"INFO"` → 100".
- **Progress only when it is news.** No line per stage, per query or per heartbeat. On a step that runs over a minute, post one status line each time a minute passes without news (`12 of 20 checks done`), so a slow step never looks stuck.
- **Keep the plumbing out.** Never mention monitors, task IDs, output files, background shells, archive UUIDs or `KEY=VALUE` names. Report a failure when it changes the result, and say what it costs ("the OPS-channel check failed; its count still comes from the quick checks").
- **Each figure once.** The totals and the category table appear once, in the phase 4 summary; later messages refer back to them instead of repeating them.
- **Write for the reader the user said they are.** The context question comes with a second one, how well the user knows the system that wrote the logs, and the answer sets `READER`: `expert`, `newcomer`, or the user's own description. Its first option lists a few terms from these logs, so the user can judge themselves against the report they will get. For a `newcomer`, the first message that uses a term of the system explains it in a clause, and a finding says why it matters. For an `expert`, name the term and move on. For a description, explain what falls outside it. The report follows the same answer; `references/report-style.md`, "Who the reader is", is the definition.
- **Ask only what changes the run,** and make every option's description literally true: say what choosing it queues, and when it queues nothing extra, say so.
- **Never state a number that is not in the facts file.** Every figure in your messages and in the report comes from the route's facts pass — `clp session measure` on the specialised route, `clp facts` on the general one. If you want a number it does not have, compute it with a query and say you did.
- **Mark the arguments, not the facts**, in chat as well as in the report. An inference or a piece of domain knowledge says so where it appears, in words; a measured or derived figure does not carry a badge announcing that it is a figure. `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/evidence-tiers.md` is the single definition of which tier a claim gets — read it before the first figure you quote, and do not restate it in your own words. The two rules that bite most often: a derived figure is never quoted bare, and a derivation carries its trap. The one most often missed is a value that may be a placeholder, such as a field that is 0 on every record: treat it as a caveat, ask the user when it would change the result, and never decide it yourself.
- **On the general route, use the user's words.** "Quick checks" (the baseline), "standard checks" (the core plan), "deeper checks" (drill entries), "matches almost everything" (non-selective), "reused from an earlier run" (UPTODATE). Query numbers such as "plan #4" belong in the report's Query log only.
- **Write plainly, in your messages as well as the report.** `${CLAUDE_PLUGIN_ROOT}/writing-guide/rules.md` governs both: short sentences, the number first, one idea per line, and a table wherever you compare three or more things.
- **No scripted pleasantries or apologies.** An estimate ("~2 min") already tells the user the wait is expected.

## Workflow

Each Bash call runs in its own shell, so shell variables do not persist between steps — re-declare them or run dependent commands together in one call. **Write every path out in full** — a command with a shell variable (`$B`, `${TMPDIR}`) no longer matches this skill's allowed tools and stops for approval.

Run anything that can take over a minute (the bootstrap on a large archive, the query pools, a subagent) in the background, so you can post the status line while it runs.

### Step 1 — prepare and route (both routes)

**Prepare, with one command.** Open phase 1. `clp` classifies the target, picks the compression settings from the detector's own evidence, compresses what is not compressed, decides whether a session bundle is needed and builds one, then names the next step:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp" <PATH|SESSION_ID>
```

`clp <TARGET>` is the analysis route, and the TARGET is the one place the first argument is not a subcommand: a path, a `.jsonl` file or a session id goes there bare, while every other step of this skill is `clp <subcommand> ...`. Add `--claude-home DIR` (the directory that holds `projects/`) for a session in a non-default location. No target → it lists the sessions it can see and stops; present that table, ask which, then run it again with the id. `--dry-run` shows the plan without writing. `--general` forces the general route even for an application that has a specialised one.

It asks one question — **which application produced these logs?** — and answers it from the records, not the path, reporting the answer with its evidence. Read these keys:

- `APP=` and `ROUTE=` → **the route is the branch below.** `claude-code` with `ROUTE=specialised` takes the specialised route; anything else takes the general route. `APP=unrecognised` is an ordinary success, not a problem: logs from an application with no registered specialisation are exactly what the general route discovers and caches. `NEXT_SKILL=` names this skill on both routes, since one skill owns both; `ROUTE=` is the branch, and nothing else decides it.
- `ARCHIVE=` and `BUNDLE=` → the artefacts to pass on. A session that launched nothing gets no bundle, and the specialised categories needing one report as not applicable.
- `PREPARED=` versus `ALREADY_PREPARED=` → whether work ran, or an existing archive and bundle were reused.
- `WHY=` and `EVIDENCE=` → why it called the application what it did. Quote from these rather than restating them; they are what makes the classification checkable.
- `LAUNCHES=` → how many records launched an agent or a workflow, which is why a bundle was or was not built.

**Name what these logs are, even when no application is registered.** `APP=unrecognised` means no *optimised route* exists, not that the application is unknown to you. The `EVIDENCE=` lines carry the fingerprint — the root fields most records hold, the timestamp shape, the file name — and that is usually enough to recognise a system on sight: `redactable`, `channel_numeric` and `goroutine` are CockroachDB; `logger` and `worker` with vLLM's text shape are an inference server. Say what you think it is, in one line, with the evidence you read and the word that marks it an inference.

Two reasons this matters more than a label. It tells you **what to look for**: a distributed database means gossip, replication, leases and clock skew; a web server means status codes and latency; an inference server means batch sizes, queueing and memory. Without it you are doing generic log statistics and will find only what any log has. And it lets you answer **why a finding matters** later, in the report, if the user wants that.

**Be honest about the confidence.** Say the identification is uncertain when the fields are generic, and name what would settle it. Never let a guess about the application harden into a fact in the report: a wrong identification quietly aims every later query at the wrong thing, which is worse than no identification at all.

**Exits and repairs.** Already an archive → it says so and does no work. Relay any `REPAIRED` line: that log had NUL bytes from a lost write, which the build removed (the source file is untouched). A build that stops on a record cut off mid-write usually means the session is still running — say so and prepare it once it has stopped. A target that is not logs, or cannot be read, fails with `error:` — report it verbatim and stop. A folder holding logs that need different compression settings is refused with the commands for each group, because one archive takes one timestamp key: point it at one group, or compress them separately and analyse each archive.

**Close phase 1** in one or two lines: raw size → archive size, the ratio, the elapsed time, and the archives directory (`9.8 GiB → 357 MiB (28×) in 43 s; archive: <dir>`). Input already an archive → one line saying so. On the specialised route add the session's time span and what the session is made of (`46 agents, 38 workflow launches, 1,380 attempts across 3,484 files`), or that it is single-threaded.

**Then branch on `ROUTE=`.** Say which route the run is taking and why in one line, name that route's five phases, and follow only that route's steps. Do not mix the two: they have different phases, different vocabularies and different report formats.

---

## Specialised route — a Claude Code session (`ROUTE=specialised`)

Compress the log, bundle every agent and workflow that ran under it, check the same seven categories every time, ask what matters, and write a report.

**Its five phases:** `[1/5] Compress`, `[2/5] Map the session`, `[3/5] Run the checks`, `[4/5] Focus`, `[5/5] Report`. Phases 1 and 2 are both done by step 1's one command — compressing the log is phase 1, and the bundle it builds *is* the map — so close them together there rather than announcing a phase that nothing further runs in. A session with no launches gets no bundle: say so in the phase 2 line.

The seven categories are fixed, because a Claude Code session has a fixed record structure: there is nothing to classify and nothing to cache, so there is no classification step and the run is fast. What the records are made of is still read, in a few seconds, because Claude Code adds record types and fields between releases. A subagent starts from that inventory, looks for anything the fixed set misses, and proposes it as an extra category.

2. **Read the reference and start the checks.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-insight.md` NOW. Open phase 3, then run the facts pass — it computes every number the report can quote, in code, so nothing is left to arithmetic:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" session measure --bundle /tmp/yscope-clp-bundles/<SESSION_ID>
   ```

   In the same Bash call, read what the records are made of: every field path with its type and record count, the paths stored under more than one type, and each record `type` with its count. It takes a few seconds, and it neither reads nor writes the classification cache:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" bootstrap --fields-only --heartbeat 0 --out-dir /tmp/clp-session-bootstrap /tmp/yscope-clp-bundles/<SESSION_ID>/archives
   ```

   That inventory is for the extras subagent at step 3, not a source of figures: the report quotes the facts file. Pass `--archive ARCHIVE` to `clp session measure` instead when there is no bundle, and point the bootstrap at that archive, and add `--axes` when the user wants scores (see step 5). It takes a few seconds; on a large session run it in the background. Its stdout gives the headline `KEY=VALUE`s and one `ALERT=<category>:<slug> value=… threshold=…` line per category whose headline metric crosses a bad threshold. Those alerts order the focus options at step 4 and nothing else: they are not scores, and one does not enter the report without you saying what fired it.

3. **Spawn the extras subagent, and ask the context question.** The seven categories are fixed, but a session can hold something none of them covers. Spawn ONE subagent (Agent tool, model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the facts file, the inventory directory `/tmp/clp-session-bootstrap`, and the prompt in the reference: it looks for record kinds, error shapes and behaviours the fixed categories miss, and returns at most three proposed extra categories, each with a count and one example id. It returns nothing when the fixed set covers everything, which is the common case.

   **Right after spawning it, ask the context question** (AskUserQuestion; wording in the reference): is the user chasing a known problem, checking something specific, evaluating the session for scoring, or just exploring. In the same call, ask how well they know Claude Code sessions, which sets `READER`. The subagent keeps working while they answer. Keep the answers for the focus question, the scoring decision and the report writer. A headless run, or no AskUserQuestion → skip the questions, give the whole picture, and write for an expert.

   Close phase 3 in one line: how many checks ran, plus any extra category the subagent proposed.

4. **Summarize, then ask for the focus.** Open phase 4 with the summary — the one place the totals and the category table appear. The reference has its shape: the session's span and what it was working on, the category table with each category's headline figure, and which categories raised alerts.

   **Check the context against the facts first.** If the user said they were chasing something and the facts point elsewhere, say so in one line before asking, with both numbers, and make the shift the recommended option — this is the most useful thing this route does. If nothing contradicts them, do not manufacture a shift.

   Then ask the focus question (AskUserQuestion; wording in the reference): the alerting categories marked "(Recommended)", the rest, "Everything", and "Score it". Each option's description says what choosing it runs. Run the deeper checks for what they chose, using the drill patterns in `session-forensics.md`. Say in one line what you queued.

5. **Score, if asked.** When the user picked "Score it", or asked for scores at any point:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" session score --bundle /tmp/yscope-clp-bundles/<SESSION_ID> --format table
   ```

   It runs the measurement pass, applies the scale's ladders, writes `/tmp/clp-session-scores.json` for a dashboard, and prints a table for you. It validates the scale first and **refuses on a bad one** — relay the `SCALE_PROBLEM` lines to the user rather than falling back to the default.

   **The division of labour is the point.** Measuring is `clp session measure`; mapping a value to a score through a declared ladder is `clp session score`, because it is a table lookup and arithmetic; choosing the thresholds is the customer's, in the scale file. What is left for you is what none of them can do: what the scores mean, which of platform, provider, model, task or environment each low axis belongs to, and anything the ladder has no rung for. Do not recompute a score or a group mean by hand — quote the tool's.

   **Look at the value behind each low score before explaining it.** The tool scores whatever the records hold, including a value the source may never have measured. If an axis rests on one, say so beside the score and do not attribute it to anyone. Ask the user whether the field is real when the answer would change what the scorecard says (evidence-tiers.md, "A value can be a placeholder").

   Add `--cohort task_type=… --cohort repo=…` when the user has said what kind of work this was; a trend needs sessions grouped by similar shape, and nothing in the log infers that reliably. The JSON records which cohort keys were left unset so a dashboard can tell "not grouped" from "grouped as null".

   **Also pass `--cohort supervision=autonomous|supervised|mixed`.** Some axes are only meaningful under one intent, and the scale gates them on it: `C4 autonomy` is scored only for a session meant to run unattended, because in a supervised session the same number is a design property and not a fault. Take the mode from what the user has already said if it is clear; otherwise ask, since scoring is opt-in and guessing the intent from the log is unreliable. Undeclared is a valid answer — C4 then goes unscored with that reason, which is better than inventing a mode. The value is still measured and reported in the Time category either way.

   Report the **group means separately and never a composite** — the groups have different owners, and averaging them hides which one is at fault. Show each raw value beside its score. Mark `C1` cohort-relative. Say how many axes each mean rests on, and name the scale file and its `scale_version`.

Then go to **[The report](#the-report--phase-5-on-both-routes)**, which is this route's steps 6 and 7.

---

## General route — any other application's logs (`ROUTE=general`)

Dump the archive's log shape dictionary, classify the real templates, and drive every query from a template that exists.

**Its five phases:** `[1/5] Compress`, `[2/5] Read the log vocabulary` (the bootstrap), `[3/5] Classify`, `[4/5] Run the checks`, `[5/5] Write the report`. Step 1 above is phase 1, including its closing line; the steps below are numbered from 3, because phase 1's close was step 2 and step 1 already did it.

The run stays interactive while the slow work happens in the background. While the classifier runs, ask the user what they already know about these logs, and how well they know the system that wrote them. When it returns, summarize what it found, start the core queries in priority order, and ask what to focus on. The answer queues deeper queries ahead of the rest and tells the report writer what to lead with. While the writer works, ask where to save the report and in which formats. Nobody to ask (a headless run) → the whole picture, with no questions.

The classification is a property of the **application**, not the capture, so it is cached (keyed by a fingerprint of the template set, capped at a character limit — 500 by default — and de-duplicated, matching what is embedded) and updated incrementally when the archive grows — re-analyzing the same app skips classification entirely. The cache is one SQLite database that stores templates by hash, never by text, so it stays small and fast even for apps whose templates are hundreds of KB each.

When `ROUTE=specialised` was reported and the user still wants the general treatment, `--general` on step 1's command forces it; the acquire stage stays the application's, so a session log is still compressed as a session.

3. **Bootstrap.** Open phase 2 with one line: what the bootstrap does, in plain words, and its estimate. It reads the field names and value distributions from a sample of up to 20,000 records, gets the per-template counts stored in the archive, then checks the classification cache. It classifies nothing (that is step 6), and it doesn't sample the dictionary: every template is counted. The first time it sees an archive, it dumps the full log shape dictionary and stores each template's counts in the cache database, which takes about 1 minute per 300 MiB of archive (`Archive bytes` from phase 1, or `du -sh <archive-dir>`): 1 s for a 1.6 MB vLLM archive, about 1 minute for a 357 MiB CockroachDB archive (9.8 GiB of raw logs). A later run on the same archive reads the stored counts instead, and takes about as long as the sample (15 s for that CockroachDB archive). Then run it:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" bootstrap <archive-dir>
   ```

   Its first line is its own estimate (`[bootstrap] archive 357.1 MB; expect about 2 min`, or `archive 357.1 MB, analyzed before; expect under a minute`). After that it prints a `[bootstrap]` line as each of its three stages starts and ends, and a heartbeat every 30 s while one runs. On an archive over 300 MiB, run it as a background Bash call (`run_in_background`, no trailing `&`). Follow its output with the Monitor tool (`tail -n +1 -F <output-file> | grep --line-buffered -E '^\[bootstrap\]|BOOTSTRAP_TIMINGS|rror'`, `timeout_ms` at its maximum; stop it with TaskStop when the harness reports the call exited), never with `sleep` between reads, which the harness blocks. Post only when a minute passes with no news, as one plain line (`still reading the vocabulary: 40 s of about 1 min`); never relay the raw `[bootstrap]` lines. It ends with `BOOTSTRAP_TIMINGS`; when the total is far from the estimate, say so in a line.

From its `KEY=VALUE` output record:
   - `FIELD path=... type=... records=N [values=V templates=K]` + `TEXT_FIELDS=` + `SCHEMA_TREE_FILE=` → every field of the whole archive, from its merged schema tree (no sampling): the KQL path, the type and the records that carry it; `shown=` marks array elements with `[]` (`message.content[].name` is queried as `message.content.name`). `ClpString` fields are the templated text; with `values`/`templates` the archive counts log shapes per field. The FIELD lines are the most common fields, then every text field; the full list is in `SCHEMA_TREE_FILE`.
   - `SAMPLE=` + `DIST field=... distinct=N values=...` → pick the **schema**: timestamp, severity, logger, **message** (the text field with the most templates that is not ruled by field in step 5), payload leaves if any. The DIST fields are the most common scalar fields from the tree; their value vocabularies come from the sample, so a low-distinct field there is severity/logger-like.
   - `TEMPLATE_FIELDS_FILE=` → which fields each template's values came from (`{"hash","fields":{path:N}}`); step 5 uses it. `TEMPLATE_FIELDS=UNAVAILABLE` → the archive was compressed by a clp-s build that does not count log shapes per field; step 5 then clusters every template.
   - `LOG_SHAPE_COUNT=` → report to the user.
   - `FREQS=OK` + `FREQS_FILE=` → per-template frequencies for the whole archive, summed from the counts clp-s stored at compression time: `{"count":N,"hash":"...","length":N,"log_shape":"..."}` NDJSON, most frequent first, where `log_shape` is the template's first `MAX_CHARS` characters (all of it when `length` is no longer). Step 7 uses this file; never recompute frequencies by projecting and counting messages.
   - `SHAPES_SOURCE=stored` → this archive was analyzed before, so its counts came from the cache database and the dictionary was not dumped; `LOG_SHAPES_FILE` (the full template text) is then not written. `SHAPES_SOURCE=dump` → the dictionary was dumped and the archive stored for next time.
   - `FREQS=UNAVAILABLE` → the archive was compressed before clp-s stored per-log-shape counts (`FREQS_HINT=` says so). Tell the user that template frequencies are unavailable for this archive and that recompressing the source logs with the current plugin adds them. Do not compute them another way.
   - `ARCHIVE_LOG_SHAPES=` + `ARCHIVE_VARS=` → the archive's own exact counts, read in a fraction of a second before anything is dumped, so phase 2 can open with the real template count instead of an estimate from bytes. `UNAVAILABLE` on a `clp-s` too old to answer; then say nothing about it.
   - `RECORD_FAMILY_COUNT=` / `RECORD_FAMILY_RESIDUAL=` / `RECORD_FAMILIES_FILE=` / `RECORD_FAMILY_METHOD=` → the records split into kinds, each with an exact count and a KQL predicate. **This is a partition**: the families plus the residual account for every record, so these shares may be read as shares of the whole. `METHOD=value field=<f> coverage=<pct>` split on that field's values and is exact; `METHOD=existence` found no field universal enough and split on field presence, which is approximate — say which you got whenever the residual is big enough to matter. The residual is records the partition does not name; report it rather than rounding it away.
   - `FIELD_COUNT_GROUPS=` / `FIELD_COUNTS_FILE=` → per-field record counts, where fields sharing a count are carried by the same records. **These overlap and must never be summed**: on one real archive the 43 lines total 725%. They tell you which fields travel together, nothing about shares of the whole. Use `RECORD_FAMILY_*` for that.
   - `TYPE_DRIFT_COUNT=` / `TYPE_DRIFT_FILE=` → field paths holding more than one type across records, most records first, each type named and counted. Treat drift on a field you are about to query as a hazard: a filter or projection written for one type silently misses the records carrying the other. On one archive `toolUseResult` is a `ClpString` on a failed tool call and an `Object` on a success, 116 against 4,525, so a query for either shape quietly answers about a subset. Mention drift to the user only where it changes a query or a finding.
   - `CACHE_MODE=` / `CACHE_REASON=` / `APP_KEY=` / `BASE_KEY=` / `TO_CLASSIFY=` / `MAX_CHARS=` → step 4. Pass `MAX_CHARS` through to `clp shape-cluster` and `clp shape-cache` so their fingerprints match. `CACHE_REASON` says why the mode is what it is — `first-run`, `templates-grown`, `up-to-date`, or `rules-changed`. **Never report `rules-changed` as a first run**: the app *is* classified, but under different field rules, so its ruled templates would come back with categories no current rule would give them. Tell the user "the field rules changed, so this is being classified again", which is a different fact from "first time seeing this app" and explains a cost they would otherwise find puzzling.

Close phase 2 in one or two lines: the template count and what it means ("16.5M records reduce to 11,558 message templates"), the record kinds ("the records are 14 kinds; the two biggest are attachments at 34.9% and assistant turns at 26.2%"), and the cache outcome in plain words (reused from an earlier run, N new templates to classify, or a first run for this app). Mention the schema only when the choice was not obvious, frequencies only when they are unavailable, the residual only when it is large enough to matter, and drift only where it will change a query; field names are for your queries, not for the user.

4. **Start the baseline queries, then branch on `CACHE_MODE`.** The baseline (the severity and logger breakdown, the fetch of any rare-severity records, and a scoped semantic scan) needs nothing but step 3's schema, so it runs now, in the background, while the templates are clustered and classified. Write its plan (a few seconds; it samples the archive), then start its pool as a background Bash call (`run_in_background`, no trailing `&`) and go on without waiting:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" baseline-plan --archive <archive-dir> \
     --schema-json '{"timestamp":"<TS>","severity":"<SEV>","logger":"<LOGGER>","message":"<MSG>"}'
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" run --retry-failed \
     --query-plan-file /tmp/clp-insights-baseline-plan.txt \
     --results-file /tmp/clp-insights-baseline-results.ndjson <archive-dir>
   ```

   Do not narrate the quick checks: their results go into the phase 4 summary. Then open phase 3 with the branch: UPTODATE → `[3/5] Classify: skipped, this app was classified on an earlier run`; GROWTH → `[3/5] Classifying the N new templates; the other M are already classified`; NEW → `[3/5] Classifying N templates, a first run for this app`.
   - **UPTODATE** — the cached plan was already fetched to `/tmp/log-shape-classification.json` (`CLASSIFICATION_FILE=`). Verify its `.schema` matches step 3's schema; if it does, skip to step 7. If it differs, treat as NEW (continue, clustering `/tmp/log-shapes.ndjson`; with `SHAPES_SOURCE=stored`, first re-run the bootstrap with `--dump` to write it).
   - **GROWTH** — only the new templates in `/tmp/log-shapes-to-classify.ndjson` need classifying; the base plan was fetched to `/tmp/log-shape-base-classification.json`. Continue to step 5.
   - **NEW** — first capture of this app; classify all of `/tmp/log-shapes-to-classify.ndjson`. Continue to step 5.

5. **Cluster the templates to classify** — truncates each template to a character limit (`MAX_CHARS` from the bootstrap, 500 by default), de-duplicates the results, and merges semantically similar templates so the classification subagent sees one representative per cluster instead of every template (in step 4's schema-mismatch case, pass `--input /tmp/log-shapes.ndjson` instead):

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" shape-cluster cluster \
     --max-chars <MAX_CHARS> \
     --input /tmp/log-shapes-to-classify.ndjson
   ```

   With field rules (below), add `--template-fields /tmp/log-shape-template-fields.ndjson --field-rules /tmp/log-shape-field-rules.json`.

   **Rule whole fields first** when the bootstrap printed `TEMPLATE_FIELDS_FILE=`, and let the ratio propose the rules rather than picking them by eye:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" shape-cluster fields \
     --template-fields /tmp/log-shape-template-fields.ndjson --freqs-file /tmp/log-shape-freqs.ndjson \
     --propose-rules /tmp/log-shape-field-rules.json
   ```

   It prints one line per text field, most templates first, with its values and its most frequent templates, and writes a proposed `field_rules` file. **A field whose template count approaches its value count is free text**: every value is its own template, so clustering it is meaningless and embedding it is waste. That is a mechanical test, not a judgement — a field at ratio 1.00 has nothing to cluster, while a field with 9,444 records and one template is boilerplate the application injects. The proposal takes any field over `--free-text-ratio` (0.8), plus any with more than `--free-text-templates` (500) above a lower floor, because a field of diff lines sits near 0.68 — plainly free text that merely shares common lines — and a pure ratio test misses it. Fields under `--free-text-min-templates` (5) are left alone: ruling one saves nothing to embed and still costs a category to review, and on data-used-as-keys it invents dozens of junk categories.

   On a real agent-log archive this takes **25,483 templates down to 879 left to embed with 39 rules**, where hand-picked rules left 8,278. So read the proposal, do not just accept it: each rule carries `proposed_by`, its ratio and both counts, the category name is mechanical (`free-text-message-content-thinking`) and meant to be renamed, and you may drop a rule whose templates you actually want told apart. Then pass it to `cluster` with both flags. A template whose values sit in ruled fields (at least 90% of them) takes the category of the ruled field holding most of them and is neither embedded nor shown to the classifier (`FIELD_RULED=N`); any other template is clustered. The classifier must rank every rule category (step 6), and `expand` refuses a rule whose category is missing from the taxonomy. The cache stores each template's category by hash, so a reused classification needs no rules; a grown archive's new templates are clustered.

Stdout prints a summary then one `{"id","count","representative"}` line per cluster — paste those lines into the classification prompt. Full memberships go to `/tmp/log-shape-clusters.json` for `expand`. Representatives and members are always FULL templates; only the embedding request uses the truncated, de-duplicated texts, so `EMBEDDED` is at most `TEMPLATES`. Embeddings come from the semantic server (nothing is installed or started locally). Exit 2 means the server is unreachable or rejected: **report the error verbatim to the user and stop** — do not diagnose it, do not start or configure a server, and do not silently switch methods. Keep the reduction (`TEMPLATES=N` → `CLUSTERS=M`) for the classifier announcement in step 6.

6. **Classify (GROWTH/NEW only), and ask for context meanwhile.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-shape-classify.md` NOW — it has the full subagent prompt and the validate → expand → merge → store commands. In short: spawn ONE classification subagent (Agent tool, model **opus**; `sonnet` if the Agent tool rejects `opus` as unavailable) with the schema, severity/logger vocabularies, and the cluster lines from step 5 (plus base taxonomy/plan labels on GROWTH). The classification is cached per app, so its quality is paid for once and reused on every later run. It returns id-based `assignments` — never log shape text — a ranked taxonomy (each category's `priority` and `why`), and a query plan whose filters are structured `match` objects — never KQL strings — each entry carrying its `category`, `priority`, and `stage` (`core` runs every time; `drill` runs only when the user focuses on its category). `clp kql check-plan` rejects any entry without a valid `match` or ranking, and with `--drift-file` also rejects one whose filter can reach less than 95% of its field's records — pass the bootstrap's `TYPE_DRIFT_FILE`. On a drift failure, re-run the classifier once with the `DRIFT_RISK` lines appended to its prompt: the fix is for the entry to filter a field *under* the drifting path, or to declare `"types"` when it deliberately targets one shape. Do not silence the check, and do not accept a plan that failed it. A `DRIFT_RISK` line that only warns needs no action, but carry it into the report where it changes what a finding covers. Announce it in one line, naming the model (`Classifying 146 groups covering 11,558 templates with opus; the longest step, and the quick checks run meanwhile`).

   **Right after spawning it, ask the context question** (AskUserQuestion; the wording is in `log-insights.md`, "Ask what the user already knows"): whether the user is chasing a known problem, has something specific to check, or is just exploring, and in the same call how well they know the system that wrote the logs, which sets `READER`. The subagent and the baseline pool keep running while the user answers. Keep the answers for steps 8–9. Never pass it to the classifier: the classification is cached per app and reused for every later capture, while the answer is about this capture. When the classifier returns: `clp kql check-plan` (on GROWTH with `--categories-from /tmp/log-shape-base-classification.json`), `clp shape-cluster expand` (on GROWTH also with `--categories-from /tmp/log-shape-base-classification.json`) gives every member template its category, by hash, and `clp shape-cache merge` writes `/tmp/log-shape-classification.json`, which the insight pass reads at once. Storing it in the cache (`clp shape-cache put`) is for the next run, not this one: start it as a background Bash call and go on without waiting. When the background store finishes, mention in your next message that the classification is cached for later runs; it needs no message of its own.

   On UPTODATE there is no classifier to wait for, so the context and reader questions are asked together with the focus question at step 8.

7. **Summarize, and start the core plan.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/log-insights.md` NOW — it has the questions, the summary, the extract, query-pool, focus and facts commands, the report-writer prompt, the save question and commands, and the report format. Run `clp extract`: it writes the core plan high priority first, the drill entries apart, and empties the focus inbox. The baseline pool started in step 4; if it is still running, wait for it to exit first (mention the wait only if it passes 30 s), since two pools at once would each size themselves from the same free memory. Then start the core plan as ONE background Bash call (`run_in_background`, no trailing `&`) with `--inbox /tmp/clp-insights-focus-inbox.ndjson`: the pool runs the core plan, takes the focus entries ahead of whatever has not started, and exits only once the focus is queued. Then open phase 4 with the summary (the reference has its shape), the one place the total and the category table appear: records and the severity split, the record kinds and their shares (these are a partition, so they may be read as shares of the whole — say the method and the residual when the partition is approximate), the category table with each category's priority, why the classifier ranked the top categories high, and, when the user gave context, which categories it points at. Record kinds and text categories are two different axes — a kind is what a record *is*, a category is what its text is *about* — so present them as two tables and never merge them into one.

8. **Ask for the focus, and queue it.** Ask in one AskUserQuestion (on UPTODATE, together with the context and reader questions): the categories the context points at (or else the whole picture) marked "(Recommended)", the other high-priority categories, and "Everything"; the automatic "Other" takes the user's own question. Each option's description says what choosing it runs: a category queues its deeper checks (say how many; with none, that you will write one to three from its templates); "Everything" queues nothing extra, because the standard checks already cover every category. Then run `clp focus` ONCE, even for "Everything": it queues the chosen categories' drill entries, plus any entries you write from the user's question or context (`--entries-file`, `match` filters checked by `clp kql`), records the focus and the context verbatim, and closes the inbox so the pool can finish. When no one can answer (a headless run, or AskUserQuestion unavailable), run `clp focus --everything` at once. Say in one line what was queued, or that nothing extra was, then stay quiet until the checks finish apart from the once-a-minute status line.

9. **Facts, and the early numbers.** When the pool prints `PLAN_STATUS`, save both tables — the baseline's (`--print-table --results-file /tmp/clp-insights-baseline-results.ndjson`) and the plan's (`--print-table`, focus entries marked) — for the report check and the report's Query log, without pasting them into the chat. Close phase 4 in one or two lines: how many checks ran, and each that failed or matched nothing, with what it costs the report. Run `clp facts` (under a second): it computes every number of the report in code, with the user's focus and context in a section of their own at the top, into `/tmp/clp-insights-facts.md`. Post the early numbers — 3 to 5 lines quoted from the facts file, the focus first — so the wait for the writer is not dead time.

Then go to **[The report](#the-report--phase-5-on-both-routes)**, which is the rest of this route's step 9 and its step 10.

---

## The report — phase 5 on both routes

This is the specialised route's steps 6–7 and the general route's step 9 (from the writer onwards) and step 10. Everything below is the same on both routes except the four things this table names; take your route's column and use it everywhere the text says "the facts file", "the report", "the writer's prompt" or "the save question".

| | specialised route | general route |
|---|---|---|
| Facts file | `/tmp/clp-session-facts.md` (from `clp session measure`, step 2) | `/tmp/clp-insights-facts.md` (from `clp facts`, step 9) |
| Report the writer writes | `/tmp/clp-session-report.md` | `/tmp/clp-insights-report.md` |
| Writer's prompt and report format | `session-insight.md`, "Report writer prompt" and "Report format" | `log-insights.md`, "Report writer prompt template" and "Report format" |
| Save question's wording | `session-insight.md`, "Ask where to save the report" | `log-insights.md`, "Ask where to save the report" |

1. **Spawn the report writer.** Open phase 5 with one line naming the model and the time (`[5/5] Writing the report with opus (~2 min)`). Both routes write with **opus**, or `sonnet` if the Agent tool rejects `opus` as unavailable: the writing is where a stronger model pays off, because a weak one drifts into derived figures and unsupported causes and each costs a correction round. Tell it the depth — `short` unless the user asked for a thorough report, since the short form is the default and a report nobody reads is worth less than a short one they do — and the reader, `READER` from the reader question, or `expert` when nobody answered it — and rely on `writing-guide/rules.md` for the prose and `references/report-style.md` for the report's shape, both of which the writer's prompt has it read. Give it your route's facts file and report format, the results of the deeper checks (the general route: the schema, the taxonomy, both results tables, and the paths `/tmp/clp-insights-facts.md` and `/tmp/log-shape-templates-by-category.txt`), the user's context and focus, and the prompt in your route's reference. Every query has run and every figure is already computed, so it runs no searches and does no arithmetic: it puts facts into words, leads with the focus, treats the user's context as a claim to check against the records, may quote no figure absent from the facts file, and writes the report itself to its path above. Never read or tail a subagent's transcript.

2. **Right after spawning it, ask where to save the report** (AskUserQuestion; your route's wording). Run `clp report save --list-formats` first (instant; it only looks for a browser to print PDF with) so every format offered is one this machine can produce: PDF only when it prints a `PDF_ENGINE` path, and a claude.ai page only when the Artifact tool is in this session's tool list. The writer keeps working while the user answers. Nobody to ask → skip the question; the report stays where the writer put it and nothing is saved or published.

3. **Check the report — general route only.** `clp report check` reads the report and flags figures mechanically; it never edits it, and there is no verifier subagent. `log-insights.md`, "Check the report", has the loop: run the script, and if it flags anything, the same writer rules on each flag and fixes the real ones in one round, then the script runs once more. Any flag still listed that the writer did not justify goes to the user as "unverified" beside the report, one line each, rather than being dropped silently. The specialised route has no mechanical check of its own — `clp report check` defaults to the insights facts file and reads the insights results tables — so do not invent one for it; its guard is that `clp session measure` computed every figure the writer is allowed to quote.

4. **Save the report as chosen, always naming the report path explicitly:**

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp" report save <your route's report path> --format <chosen>
   ```

   `clp report` is shared by both routes and its default report path is the general route's, so omitting the path on the specialised route saves the wrong file — and silently, because both reports can exist in `/tmp` at once. Add `--dest <folder-or-file>` and `--name <name>` for the location the user chose (`log-insights.md`, "Save the report", has the full form). One run writes every chosen format and never overwrites a file (it adds `-2`, `-3`, … instead) and creates missing folders; it prints `SAVED_<FORMAT>=<path>` per file. PDF is printed by a headless Chrome, Chromium or Edge without web fonts, so it needs no network; `PDF_ERROR=` means that one format failed — tell the user the reason in one line and keep the other files, and never install a browser to get PDF. For a claude.ai page, add `artifact` to `--format`: it writes a finished page that already follows the Artifact page contract, which you publish **as-is** with the Artifact tool (`file_path` that file, `icon` `"report"`, and a one-sentence `description` naming the logs and the headline figure). Do not rewrite or restyle the page, and declare no capabilities. Give the user the link the publish returns; the page is private until they share it.

5. **Close.** Three to five findings, most important first, each argument marked as one and a derived figure never quoted bare; the caveats that change how to read them, including any trap a derivation carries; and where the report is — each saved path and the claude.ai link. Do not restate the report. Then offer at most three next steps, one line each, from your route's list:

   - **Specialised route.** Drill into a specific finding (patterns in `session-forensics.md`). Review many sessions for recurring harness problems: `clp bundle-review` (see `session-forensics.md`). Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp" decompress ARCHIVE /tmp/session-decompressed`.
   - **General route.** Drill deeper on a specific template or finding, or on another category's drill entries (patterns in `references/log-shape-baseline.md`). Note that re-running on the same application skips classification (cache). Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp" decompress <archives-dir> <out-dir>`.

## Ad-hoc questions

When the user asks one specific thing rather than "what happened", skip the phases. Run step 1 to prepare the target (it compresses, and bundles a session that has agents), then go straight to the query. On a session, `session-forensics.md` has the KQL starters, the catalog SQL and the evidence commands; on any other archive, `log-shape-baseline.md` has the retrieve, count and analysis patterns. Answer, then offer the full pass.
