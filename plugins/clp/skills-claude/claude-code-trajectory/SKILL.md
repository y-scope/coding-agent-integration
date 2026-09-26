---
name: claude-code-trajectory
description: Analyze a Claude Code session log end to end with CLP — compress it, bundle its agents and workflows, run a fixed set of category checks, ask what to focus on, and write a report. Also lists, searches and decompresses trajectories for ad-hoc questions.
allowed-tools:
  - "Agent"
  - "AskUserQuestion"
  - "Artifact"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-schema-tree:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle-review:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-report:*)"
  - "Bash(jq:*)"
  - "Bash(grep:*)"
  - "Bash(head:*)"
  - "Bash(tail:*)"
  - "Bash(wc:*)"
  - "Bash(cat:*)"
---

# Claude Code Trajectory

End-to-end analysis of a Claude Code session: compress the log, bundle every agent and workflow that ran under it, check the same seven categories every time, ask what matters, and write a report.

> **Never debug or verify the setup. Run the workflow as asked, directly.** Do not health-check endpoints, probe the environment, inspect installs, or try to repair anything. If a command fails, stop and report the failure to the user verbatim — the error text and exit code — then let them decide. Do not install, configure, or start anything, and do not re-run a failed command hoping for a different result. An error is an acceptable outcome; a silent workaround is not.

> **Do not launch a dynamic workflow (multi-agent orchestration) for this analysis on your own initiative.** CLP answers these questions better than a workflow built on top of grep, and the orchestration makes the run slower for no gain in coverage. Use the parallelism this skill specifies — the extras subagent at step 5 and the report writer at step 8 — and fan out further only if the user asks.

The seven categories are fixed, because a Claude Code session has a fixed record structure: there is nothing to discover and nothing to cache, so there is no classification step and the run is fast. A subagent looks for anything the fixed set misses and proposes it as an extra category.

For a single ad-hoc KQL query, use the `search` skill. For a non-session log, use `log-insights`.

## References — read on demand, not up front

- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-insight.md` — read at step 4: the seven categories, the three questions' wording, the extras and report-writer prompts, the focus-shift rule, the scorecard, the report format.
- `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-forensics.md` — read when drilling into a finding, or when the user asks an ad-hoc question instead of running the full pass: KQL starters, bundle SQL, evidence commands, the harness-review checklist, and the conclusions the logs do not support.

## Supported inputs

- A session picked from the list (the default).
- A session id or a `.jsonl` path the user names — skip listing.
- A bundle directory or CLP archive the user names — skip to step 4.
- A different Claude home (`--claude-root DIR` when listing, `--claude-home DIR` when bundling). Needed whenever the sessions are not under `~/.claude`.

## Talking to the user

The user sees your messages, not the tools' output. Keep every message short, and make each one tell them something they did not know.

- **Five phases, numbered.** `[1/5] Compress`, `[2/5] Map the session`, `[3/5] Run the checks`, `[4/5] Focus`, `[5/5] Report`. Open each with one line: what it does and, when it can take over 30 s, how long. Close it with one line: what it found. A phase that is skipped gets one line saying why.
- **Lead with what was learned, not what ran.** "593 of 1,380 agent attempts stalled and were retried", not "ran the reliability SQL".
- **Progress only when it is news.** No line per query. On a step running over a minute, post one status line each time a minute passes without news.
- **Keep the plumbing out.** Never mention task IDs, output files, background shells, archive UUIDs or `KEY=VALUE` names.
- **Each figure once.** The totals and the category table appear once, in the step 6 summary; later messages refer back instead of repeating.
- **Ask only what changes the run,** and make every option's description literally true about what choosing it queues.
- **Never state a number that is not in the facts file.** Every figure in your messages and in the report comes from `clp-session facts`. If you want a number it does not have, compute it with a query and say you did.
- **Never quote a currency figure, and never sum the per-kind token column.** Cost is reported in tokens because the log's `totalCostUSD` is derived from an assumed unit price rather than what was billed, and is not always refreshed; converting to money needs the reader's own rates. And one API response can be recorded in several logs — a fork inherits its parent's transcript — so the bundle total counts each response once and is *smaller* than the per-kind rows added up. The reference in `session-insight.md` has both in full; if a figure surprises you, read it there before repeating it.
- **No scripted pleasantries or apologies.**

## Workflow

Each Bash call runs in its own shell, so shell variables do not persist between steps. **Write every path out in full** — a command with a shell variable (`$B`, `${TMPDIR}`) no longer matches this skill's allowed tools and stops for approval.

Run anything that can take over a minute in the background so you can post a status line while it runs.

1. **List and pick.** Open phase 1. Sessions, newest first:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-list-sessions"
   ```

   Add `--claude-root DIR` for a non-default Claude home, `--all` to list every session, `--project-filter TEXT` to narrow. Present the choices as a table: `IDX`, `AGENT`, modified timestamp, raw bytes, human size, session name, project/cwd, session id. One session → take it without asking. Several and the user has not said which → ask.

2. **Compress the selected `IDX`:**

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
     --selection-file /tmp/clp-s-session-selection-...tsv \
     --session-index <IDX> \
     --timestamp-key timestamp
   ```

   Close phase 1 in one line: raw size → archive size, the ratio, and the session's time span (`92.9 MB → 8.8 MB (10.6×); 3 days, 2026-08-24 to 2026-08-27`).

3. **Map the session.** Open phase 2. Check whether it launched agents or workflows:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --count ARCHIVE 'message.content.name:Agent OR message.content.name:Workflow'
   ```

   **Any count → build a bundle.** The main log records only each launch; what each agent did, how long it ran and what failed is in other files.

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-bundle" /tmp/yscope-clp-bundles/<SESSION_ID> build --session-id <SESSION_ID>
   ```

   Add `--claude-home DIR` for a non-default Claude home. Building takes seconds (a 290 MB session with 1,426 agent transcripts: about 12 s). If that directory already holds a bundle (it has `manifest.json`) and the session has not changed since, reuse it; rebuild with `--force` only when it has. A catalog from an older plugin version is refused with the command that rebuilds it (`clp-bundle BUNDLE rebuild`) — run it.

   Relay any `REPAIRED` line to the user: that log had NUL bytes from a lost write, which the build removed (the source file is untouched). A build that stops on a record cut off mid-write usually means the session is still running — say so and build once it has stopped.

   **No agents or workflows** → no bundle; the main archive is the whole session. Say so in one line and carry on: the categories that need a bundle will report as not applicable.

   Close phase 2 in one line: what the session is made of (`46 agents, 38 workflow launches, 1,380 attempts across 3,484 files`), or that it is single-threaded.

4. **Read the reference and start the checks.** Read `${CLAUDE_PLUGIN_ROOT}/skills-claude/references/session-insight.md` NOW. Open phase 3, then run the facts pass — it computes every number the report can quote, in code, so nothing is left to arithmetic:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-session" facts --bundle /tmp/yscope-clp-bundles/<SESSION_ID>
   ```

   Pass `--archive ARCHIVE` instead when there is no bundle, and add `--axes` when the user wants scores (see step 7). It takes a few seconds; on a large session run it in the background. Its stdout gives the headline `KEY=VALUE`s and one `ALERT=<category>:<slug> value=… threshold=…` line per category whose headline metric crosses a bad threshold. Those alerts order the focus options at step 6 and nothing else: they are not scores, and one does not enter the report without you saying what fired it.

5. **Spawn the extras subagent, and ask the context question.** The seven categories are fixed, but a session can hold something none of them covers. Spawn ONE subagent (Agent tool, model unset so it uses the session's model) with the facts file and the prompt in the reference: it looks for record kinds, error shapes and behaviours the fixed categories miss, and returns at most three proposed extra categories, each with a count and one example id. It returns nothing when the fixed set covers everything, which is the common case.

   **Right after spawning it, ask the context question** (AskUserQuestion; wording in the reference): is the user chasing a known problem, checking something specific, evaluating the session for scoring, or just exploring. The subagent keeps working while they answer. Keep the answer for steps 6–8. A headless run, or no AskUserQuestion → skip the question and give the whole picture.

   Close phase 3 in one line: how many checks ran, plus any extra category the subagent proposed.

6. **Summarize, then ask for the focus.** Open phase 4 with the summary — the one place the totals and the category table appear. The reference has its shape: the session's span and what it was working on, the category table with each category's headline figure, and which categories raised alerts.

   **Check the context against the facts first.** If the user said they were chasing something and the facts point elsewhere, say so in one line before asking, with both numbers, and make the shift the recommended option — this is the most useful thing the skill does. If nothing contradicts them, do not manufacture a shift.

   Then ask the focus question (AskUserQuestion; wording in the reference): the alerting categories marked "(Recommended)", the rest, "Everything", and "Score it". Each option's description says what choosing it runs. Run the deeper checks for what they chose, using the drill patterns in `session-forensics.md`. Say in one line what you queued.

7. **Score, if asked.** When the user picked "Score it", or asked for scores at any point:

   ```bash
   "${CLAUDE_PLUGIN_ROOT}/bin/clp-session" score --bundle /tmp/yscope-clp-bundles/<SESSION_ID> --format table
   ```

   It runs the measurement pass, applies the scale's ladders, writes `/tmp/clp-session-scores.json` for a dashboard, and prints a table for you. It validates the scale first and **refuses on a bad one** — relay the `SCALE_PROBLEM` lines to the user rather than falling back to the default.

   **The division of labour is the point.** Measuring is `clp-session facts`; mapping a value to a score through a declared ladder is `clp-session score`, because it is a table lookup and arithmetic; choosing the thresholds is the customer's, in the scale file. What is left for you is what none of them can do: what the scores mean, which of platform, provider, model, task or environment each low axis belongs to, and anything the ladder has no rung for. Do not recompute a score or a group mean by hand — quote the tool's.

   Add `--cohort task_type=… --cohort repo=…` when the user has said what kind of work this was; a trend needs sessions grouped by similar shape, and nothing in the log infers that reliably. The JSON records which cohort keys were left unset so a dashboard can tell "not grouped" from "grouped as null".

   **Also pass `--cohort supervision=autonomous|supervised|mixed`.** Some axes are only meaningful under one intent, and the scale gates them on it: `C4 autonomy` is scored only for a session meant to run unattended, because in a supervised session the same number is a design property and not a fault. Take the mode from what the user has already said if it is clear; otherwise ask, since scoring is opt-in and guessing the intent from the log is unreliable. Undeclared is a valid answer — C4 then goes unscored with that reason, which is better than inventing a mode. The value is still measured and reported in the Time category either way.

   Report the **group means separately and never a composite** — the groups have different owners, and averaging them hides which one is at fault. Show each raw value beside its score. Mark `C1` cohort-relative. Say how many axes each mean rests on, and name the scale file and its `scale_version`.

8. **Write and save the report.** Open phase 5. Spawn the report writer (Agent tool, model unset) with the facts file, the results of the deeper checks, the user's context and focus, and the prompt in the reference. Every figure is already computed, so it runs no searches and does no arithmetic: it puts facts into words, leads with the focus, treats the user's context as a claim to check against the records, may quote no figure absent from the facts file, and writes to `/tmp/clp-session-report.md`.

   **Right after spawning it, ask where to save** (AskUserQuestion; wording in the reference). Run `clp-report save --list-formats` first (instant) so every format offered is one this machine can produce: PDF only when it prints a `PDF_ENGINE` path, and a claude.ai page only when the Artifact tool is in this session's tool list. Nobody to ask → the report stays at `/tmp/clp-session-report.md`.

   Then save it as chosen with `clp-report save`: one run writes every chosen format and never overwrites a file; for a claude.ai page it writes a finished page that you publish with the Artifact tool as-is. A format that fails gets one line with the reason; the others are still saved.

9. **Close.** Three to five findings, most important first, with inferences labelled; the caveats that change how to read them; and where the report is — each saved path and the claude.ai link. Do not restate the report. Then offer at most three next steps, one line each:
   - Drill into a specific finding (patterns in `session-forensics.md`).
   - Review many sessions for recurring harness problems: `clp-bundle-review` (see `session-forensics.md`).
   - Decompress for raw inspection: `"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" ARCHIVE /tmp/session-decompressed`.

## Ad-hoc questions

When the user asks one specific thing about a session rather than "what happened", skip the phases. Compress (steps 1–2), bundle if there are agents (step 3), then go straight to the query: `session-forensics.md` has the KQL starters, the catalog SQL, and the evidence commands. Answer, then offer the full pass.
