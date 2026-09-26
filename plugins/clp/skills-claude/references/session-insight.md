# Session insight — categories, questions, prompts, report

Read this at step 4 of the `claude-code-trajectory` skill. It holds the seven categories, the wording of the three questions, the two subagent prompts, the focus-shift rule, the scorecard, and the report format.

Every figure quoted anywhere in this pass comes from the facts file that `clp-session facts` writes. Nothing here recomputes a number.

## The seven categories

Fixed, in this order. A Claude Code session has a fixed record structure, so these do not need discovering. Each one's headline figure is what goes in the category table.

| # | Category | What it answers | Headline figure |
|---|---|---|---|
| 1 | Reliability | Did the work that was started finish? | share of attempts and agents reaching a good terminal state |
| 2 | Cost | What did it consume, and how much bought nothing? | total input tokens, cache hit rate, waste share |
| 3 | Time | Where did the wall clock go? | the e2e split: model, human, tool, idle, other |
| 4 | Outcomes | What did it actually produce? | repo-confirmed commits and PRs, tests passed |
| 5 | Harness faults | Did the platform get in the way? | API errors, rejected launches, runtime-honesty mismatches |
| 6 | Human loop | How often did a person have to step in? | human interrupts, denials, blocking-question minutes |
| 7 | Rework | How much work was done more than once? | logical units retried, stalled attempts |

Four distinctions the categories depend on, all of which the facts file makes explicit — carry them into the report:

- **Runtime interrupts are not human interrupts.** Interrupts on the main thread are the person; interrupts in workflow-agent logs are the runtime's no-progress kill. A session with 654 interrupts may have had exactly 2 from the human.
- **Agent minutes are not wall-clock minutes.** Attempts run in parallel and workflows are pipelined, so summed attempt time routinely exceeds the session's span. Never present it as elapsed.
- **One API response can be recorded in several logs, so the per-kind token column does not add up to the total.** A subagent's response is written to its own transcript *and* to the main log, and a fork starts from a copy of its parent's transcript, so the same response reappears in every descendant's log — on one real session a single response had token rows in three agent transcripts plus the main log, and the worst multiplicity was six. The bundle-wide total therefore counts each response once, keyed on its `message_id`, while each per-kind row stays that log's own honest accounting. **Quote the bundle total for "what the session cost" and a per-kind row for "what this agent cost", and never sum the column.** If someone adds it up and gets a larger number, that difference is the duplication, not an error — the facts file states it and the size of it. The correction was 0.15% on that session, but it scales with how much forking happened, so do not carry that figure to another session as though it were typical.
- **Tokens are the measurement; money is not.** The logs carry a `totalCostUSD` field and the scoring ignores it deliberately: it is derived from an assumed unit price rather than what was billed, and it is not always refreshed, so it goes stale. **Do not quote a currency figure in the report, even when asked what the session cost** — give tokens, and say that converting them needs the reader's own rates, plan and provider. If the user wants money, that is a calculation they own; offer the token figures it would rest on rather than a number the log cannot support.

## Ask what the user already knows

Right after spawning the extras subagent (step 5). One AskUserQuestion, header `Context`:

> **What do you already know about this session?**
>
> - **Chasing a known problem** — something went wrong and you know roughly what. Queues deeper checks for the categories your description points at, and the report leads with them.
> - **Checking something specific** — a turn, a tool, an agent, a file, a commit. Queues checks for that thing.
> - **Evaluating it for scoring** — you want the 0–10 scorecard to compare against other sessions. Runs the scoring pass as well as the checks.
> - **Just exploring** — no particular suspicion. Queues nothing extra; you pick a focus once the checks come back.

Keep the answer for the summary, the focus question and the report writer. Treat it as a claim to check against the records, never as a fact: a person's account of their own session is frequently wrong about *which* thing was slow or broken, and correcting that is the point of step 6.

## Ask for the focus

After the summary (step 6). One AskUserQuestion, header `Focus`. Build the options from the `ALERT=` lines the facts pass printed:

- Each alerting category, most severe first, marked "(Recommended)", with its headline figure in the description and what choosing it queues.
- Any extra category the subagent proposed, described as proposed and with its count.
- **Everything** — queues nothing extra; the standard checks already cover every category.
- **Score it** — runs the scorecard pass.

The automatic "Other" takes the user's own question. Every description must be literally true about what it queues; when a category has no deeper checks beyond what already ran, say so rather than implying more work.

## The focus-shift rule

This is the most useful thing the skill does, and the easiest to get wrong in either direction.

**Shift when the facts contradict the user's stated concern.** Say it in one line before asking the focus question, give both numbers, and make the shift the recommended option. Real examples:

- They say the model was slow; the facts show model time is 35% of e2e and human + idle is 42%. Lead with Time, not the model.
- They say agents kept failing; the facts show the failures are `api-400` at 0 tool calls each — a gateway configuration fault, not agent behaviour. Lead with Harness faults.
- They say the session burned too many tokens on retries; the facts show stalled attempts cost 6% of input while one aborted workflow cost 7%. Lead with the workflow, not the retries.

**Do not shift** when nothing contradicts them, when the contradiction rests on a figure the facts file does not have, or when both readings are true — then say both and let them choose. Never manufacture a shift to look observant. A run where the user was right and you said so in one line is a good run.

## Extras subagent prompt

One subagent, model unset. Fill in `FACTS_FILE` and `BUNDLE`.

```
Read the session facts file at FACTS_FILE. It covers seven fixed categories:
reliability, cost, time, outcomes, harness faults, human loop, rework.

Your job is to find what those seven miss in this particular session — and
usually there is nothing, which is a fine answer.

Look for: record kinds, attachment types or subtypes the facts file does not
account for; error or interrupt shapes that do not fit the categories above;
tool or harness behaviours that recur but are not counted; anything in the
catalog's launch_error or attrs fields that has no home in the seven.

Useful commands (write paths in full):
  clp-bundle BUNDLE sql "SELECT ..."   (schema is in session-forensics.md)
  clp-s-search-kql --unique attachment.type ARCHIVE '*'
  clp-s-search-kql --unique subtype ARCHIVE '*'

Return at most THREE proposed extra categories. For each: a name, one sentence
on what it covers, the count of records or nodes behind it, and one example id
or uuid someone can open. Propose nothing that is already a headline figure of
one of the seven. Return "none" if the seven cover this session.

Return only the proposals. No preamble, no raw JSON, no method notes.
```

## Report writer prompt

One subagent, model unset. Fill in the paths and the user's own words.

```
Write the analysis report for a Claude Code session to /tmp/clp-session-report.md

Facts file (every number you may quote): FACTS_FILE
Deeper check results: RESULTS
The user's context, in their words: "CONTEXT"
Their chosen focus: FOCUS
Extra categories found: EXTRAS

Rules:
- Every figure must appear in the facts file. You may not compute, estimate,
  infer or round a number that is not there. No arithmetic of any kind.
- Never quote a currency figure, even if asked what the session cost. Cost is in
  tokens: the log's totalCostUSD comes from an assumed unit price, not from what
  was billed, and is not always refreshed. Say that converting needs the
  reader's own rates.
- Never add up the per-kind token column. One API response can be recorded in
  several logs, because a fork inherits its parent's transcript, so the bundle
  total counts each response once and is smaller than those rows summed. Quote
  the bundle total for the session and a per-kind row for one agent. The facts
  file states the difference and why; if a reader adds the column up and gets
  more, that gap is the duplication and not an error.
- Lead with the focus. The other categories follow in the fixed order:
  reliability, cost, time, outcomes, harness faults, human loop, rework.
- Treat the user's context as a claim to check against the records, not as
  fact. If the records contradict it, say so plainly with both figures.
- Label every inference as an inference. The logs record activity, not value:
  they cannot tell you whether the work was good, only what happened.
- For each finding say whether it is a harness, provider, model, task or
  environment problem — or that the logs cannot tell them apart.
- Do not conclude a workflow succeeded from status "completed"; do not read an
  order of work from phase_order edges; do not claim one agent's output fed
  another. session-forensics.md has the full list.
- Report a commit or PR as existing only where the facts file says the
  repository confirmed it, and say how it matched.

Format: see "Report format" below — follow it exactly.
Write the file. Return only its path and a three-line summary.
```

## Report format

```markdown
# Session <name> — <span>

<Two or three sentences: what the session was working on, over what period,
and the single most important thing the analysis found.>

## <Focus category>
<Leads. The user's focus or the shift you proposed.>

## Reliability
## Cost
## Time
## Outcomes
## Harness faults
## Human loop
## Rework
<The remaining six in fixed order, each: headline figure with its denominator,
what it means, and one example id to open. A category with nothing notable gets
one line saying so.>

## What the logs cannot tell you
<Quality of the work. Whether the outcome was right. What it cost in money, as
opposed to tokens. Anything needing external data — review acceptance, revert
rate, post-merge CI. Always present. Add here any caveat the facts file raised
about its own figures: notably, when many token-bearing records carry no
`message_id` the bundle total may still double-count, and the facts file says
how many there were.>

## Query log
<Each check that ran, its result, and each that failed or matched nothing.>
```

## Scoring

**The script measures; you score.** `clp-session facts --axes` computes each axis's raw value and the components behind it, and assigns nothing. You map each value to 0–10 using a scale file, because a customer's thresholds are their own: what counts as an acceptable stall rate or cache hit rate is a policy decision, not a measurement.

### Find the scale

Load the first of these that exists, and say in the report which one you used and its `scale_version`:

1. a path the user names
2. `./.clp-scoring-scale.json`
3. `.claude/clp-scoring-scale.json`
4. `${CLAUDE_PLUGIN_ROOT}/scoring-scale.json` — the shipped default

Validate it before trusting it: `clp-session facts --axes --check-scale --scale FILE` prints `SCALE_OK`, or one `SCALE_PROBLEM=` line per defect (a missing axis, a malformed or non-exhaustive ladder). A customer scale that fails the check is reported to the user as-is; do not fix it silently and do not fall back to the default without saying so.

### Apply it — with the tool, not by hand

```bash
clp-session score --bundle BUNDLE --format table [--scale FILE] [--cohort task_type=… --cohort repo=…]
```

It measures, applies each ladder, computes the group means, writes `/tmp/clp-session-scores.json`, and prints a table. **Do not map a value to a score or average a group yourself** — a ladder lookup and a mean are exactly the arithmetic that goes wrong, and the tool records which rung matched so a reader can check it.

For reference, the rule it implements: rungs are ordered highest score first, and the first whose bound the value satisfies wins — `min` for `higher_is_better`, `max` for `lower_is_better`. An axis whose value is `n/a` is not scored; it goes to `unscored` with its reason and leaves its group's mean resting on fewer axes. A group with no scored axes has a `null` mean, never 0.

It validates the scale before scoring and **exits without writing on a bad one**. Relay its `SCALE_PROBLEM` lines to the user; never fall back to the default scale silently.

**A penalty belongs in the scale, never in a script or in your head.** An earlier version docked B2 two points when no dedicated search tool was used, and C3 two points for thin test coverage. Both were removed: a penalty is a threshold decision, so it belongs in the customer's ladder. The measurement that motivated each one still rides in that axis's `components` text. If a low score looks unjustified, say so in the report and propose a stricter ladder — do not subtract points yourself, and do not put one back into either script.

**What the tools cannot do, and you must.** They produce numbers, not meaning. Yours is: what the pattern of scores says, which of platform, provider, model, task or environment each low axis belongs to, whether an axis is low for a reason the ladder has no rung for, and what to do about it. A scorecard relayed without that is a table, not an analysis.

The JSON carries a `cohort` object with an `_unset` list, so a dashboard can tell an ungrouped session from one grouped as null. Pass the cohort keys whenever the user has said what kind of work the session was; nothing in the log infers task type reliably.

### Declare the supervision mode, or C4 goes unscored

Some axes are only meaningful under one intent, and the scale gates them on a cohort key. `C4 autonomy` is gated on `supervision`, which is `autonomous`, `supervised` or `mixed`. **Autonomy is not a virtue on its own**: a session meant to run unattended that kept stopping for a person scored badly, but a deliberately supervised session shows the same number as a design property, not a fault. So unless `supervision=autonomous` is declared, C4 is not scored and its group mean rests on one fewer axis.

**Ask for it when you score.** Scoring is already opt-in, so one more question on that path is cheap, and guessing the intent from the log is not reliable. If the user's earlier context already makes it clear, use that instead of asking again. If they decline or cannot say, leave it undeclared: an unscored axis with a stated reason is right, and inventing a mode to fill the gap is not.

The raw value is measured and reported either way — `clp-session facts` never applies the gate — so the human-and-idle share stays visible in the Time category even when the axis is not scored. Note also what the gate does **not** do: it accounts for intent, not for quality. The logs cannot tell you whether unattended work was any good, only whether it was delivered.

| Group | Axes | Owner |
|---|---|---|
| A. Platform | execution reliability, provider stability, cache efficiency, config correctness, runtime honesty | infra / gateway |
| B. Behavior | tool proficiency, tool selection fitness, rework rate, context discipline, orchestration efficiency | model / harness |
| C. Outcome | delivery throughput, delivery integrity, verification rigor, autonomy, self-recovery | shared |
| D. Cost | cost per outcome, waste ratio, cache recovery, model-mix fitness, cost concentration | infra + orchestration |

### Present it

- **Show the raw value next to every score.** The value is the measurement and the score is an interpretation of it against this scale; a reader must be able to disagree with the second without doubting the first.
- **Say what each score's threshold rests on.** Every axis in the scale carries a `basis` — `definitional`, `mechanism`, `observed` or `judgement` — and a `rationale`. A low score on a `definitional` axis is strong evidence; a low score on a `judgement` axis is a starting threshold someone chose, and a reader is entitled to push back on it. Quote the `rationale` for any axis you build an argument on, and report the `basis_summary` so it is clear how much of the scorecard rests on judgement. Never present a judgement-based score as though it were measured.
- **Point at calibration when judgement dominates.** If most scored axes are `judgement`, say so and name the fix: `clp-bundle-review --json` over the customer's own sessions gives the percentiles to replace those rungs with, within a cohort. A scorecard built mostly on defaults is a baseline, not a verdict.
- **Report the four group means separately. Never a single composite.** The groups have different owners; averaging them hides which one is at fault. A session whose platform scores low and whose outcome scores higher delivered *despite* its platform, and one number would say the opposite.
- **`C1` is cohort-relative.** Throughput per model-hour only compares within sessions of similar task shape. Mark it every time.
- **`A3` and `D3` are the same measurement.** Cache hit rate is both a platform fault and the largest cost lever; the duplication is deliberate. Say so rather than letting it look like an error.
- **Scores compare sessions; they do not judge one.** A single session's scorecard is a baseline. Trends need a cohort key — task type, repository, duration band — recorded per session, and a period resting on one or two sessions is about those sessions, not a trend.
- **Alerts are not scores.** The `ALERT=` lines print the value and the threshold that fired them, and exist only to order the focus question. Do not put an alert in the report without saying what fired it.

## Ask where to save the report

Right after spawning the writer (step 8). One AskUserQuestion, header `Save`, options built from `clp-report save --list-formats`:

> **Where should the report go?**
>
> - **Markdown file** — written next to the bundle, or a path you name.
> - **A claude.ai page** — a shareable page. Offer only when the Artifact tool is available.
> - **PDF** — offer only when `--list-formats` prints a `PDF_ENGINE` path.
> - **Leave it in /tmp** — no copy; the report stays where the writer put it.
