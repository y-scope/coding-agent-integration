# Session insight — categories, questions, prompts, report

Read this at step 2 of the `analyze-logs` skill's specialised route. It holds the seven categories, the wording of the questions to the user, the two subagent prompts, the focus-shift rule, the scorecard, and the report format.

Every figure quoted anywhere in this pass comes from the facts file that `clp session measure` writes. Nothing here recomputes a number.

## The seven categories

Pre-trained, and reported in this order. They ship with the plugin, so a session's categories are not discovered on each run the way the general route's are on its first. Each one's headline figure is what goes in the category table.

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

Right after spawning the extras subagent (step 3). One AskUserQuestion, header `Context`:

> **What do you already know about this session?**
>
> - **Chasing a known problem** — something went wrong and you know roughly what. Queues depth checks for the categories your description points at, and the report leads with them.
> - **Checking something specific** — a turn, a tool, an agent, a file, a commit. Queues checks for that thing.
> - **Evaluating it for scoring** — you want the 0–10 scorecard to compare against other sessions. Runs the scoring pass as well as the checks.
> - **Just exploring** — no particular suspicion. Queues nothing extra; you pick a focus once the checks come back.

Ask a second question in the same call, header `Reader`:

> **How well do you know Claude Code sessions? Pick one, or tell me in your own words.**
>
> - **I know them well** — the report uses terms like <three or four terms from this session> without explaining them. If those read as familiar, this is you.
> - **Explain as I go** — the report adds a sentence or two of background wherever a finding needs it, so it runs longer.

Fill in the example terms from this session's facts file, which step 2 already wrote. Take the ones its findings are likely to use: the categories that raised an alert first, then the largest figures. Use the words Claude Code itself uses, such as "cache reads", "forked agents", "stalled attempts" or "runtime interrupts", and never this plugin's own vocabulary. The terms let the user judge themselves against the report they will actually get.

The first option sets `READER` to `expert` and the second to `newcomer`. An answer typed into "Other" is the third choice: pass it to the writer verbatim as `READER`, since the user's description of what they know says more than either label. `references/report-style.md`, "Who the reader is", says what each one changes. When no one can answer, `READER` is `expert`.

Keep the context answer for the summary, the focus question and the report writer. Treat it as a claim to check against the records, never as a fact: a person's account of their own session is frequently wrong about *which* thing was slow or broken, and correcting that is the point of step 4.

## Ask for the focus

After the summary (step 4). One AskUserQuestion, header `Focus`. Build the options from the `ALERT=` lines the facts pass printed:

- Each alerting category, most severe first, marked "(Recommended)", with its headline figure in the description and what choosing it queues.
- Any extra category the subagent proposed, described as proposed and with its count.
- **Everything** — queues nothing extra; the breadth checks already cover every category.
- **Score it** — runs the scorecard pass.

The automatic "Other" takes the user's own question. Every description must be literally true about what it queues; when a category has no depth checks beyond what already ran, say so rather than implying more work.

## The focus-shift rule

This is the most useful thing the skill does, and the easiest to get wrong in either direction.

**Shift when the facts contradict the user's stated concern.** Say it in one line before asking the focus question, give both numbers, and make the shift the recommended option. Real examples:

- They say the model was slow; the facts show model time is 35% of e2e and human + idle is 42%. Lead with Time, not the model.
- They say agents kept failing; the facts show the failures are `api-400` at 0 tool calls each — a gateway configuration fault, not agent behaviour. Lead with Harness faults.
- They say the session burned too many tokens on retries; the facts show stalled attempts cost 6% of input while one aborted workflow cost 7%. Lead with the workflow, not the retries.

**Do not shift** when nothing contradicts them, when the contradiction rests on a figure the facts file does not have, or when both readings are true — then say both and let them choose. Never manufacture a shift to look observant. A run where the user was right and you said so in one line is a good run.

## Extras subagent prompt

One subagent, model **opus** (`sonnet` if the Agent tool rejects `opus`). Fill in `FACTS_FILE`, `INVENTORY_DIR` and `BUNDLE`.

```
Read the session facts file at FACTS_FILE. It covers the seven pre-trained categories: reliability, cost, time, outcomes, harness faults, human loop, rework.

Your job is to find what those seven miss in this particular session — and usually there is nothing, which is a fine answer.

Start from the inventory in INVENTORY_DIR, which `clp bootstrap --fields-only` read from every record of the session:
  clp-insights-schema-tree.txt          every field path with its type and record count (FIELD lines)
  clp-insights-field-counts.ndjson      the top-level fields grouped by how many records carry them
  clp-insights-type-drift.ndjson        the paths stored under more than one type
A field or a drifting path the facts file never mentions is where to look first. A field that names what a record is (`type`, `subtype`, `attachment.type`, ...) is worth listing the values of, and checking each value against the facts file.

Look for: record kinds the facts file does not account for; error or interrupt shapes that do not fit the categories above; tool or harness behaviours that recur but are not counted; anything in the catalog's launch_error or attrs fields that has no home in the seven.

Useful commands (write every path in full, `clp`'s own included, since this agent does not inherit the plugin root):
  clp bundle BUNDLE sql "SELECT ..."   (schema is in session-forensics.md)
  clp search --unique FIELD ARCHIVE '*'   (the values a field takes)
  clp search --limit 5 ARCHIVE 'FIELD:"VALUE"'   (a few records with one value)

Return at most THREE proposed extra categories. For each: a name, one sentence on what it covers, the count of records or nodes behind it, and one example id or uuid someone can open. Propose nothing that is already a headline figure of one of the seven. Return "none" if the seven cover this session.

Return only the proposals. No preamble, no raw JSON, no method notes.
```

## Report writer prompt

One subagent, model **opus** (`sonnet` if the Agent tool rejects `opus`). Fill in the paths, the user's own words, `DEPTH` — `short` unless the user asked for a thorough report — and `READER`, from the `Reader` question: `expert`, `newcomer`, or the user's own words.

```
Write the analysis report for a Claude Code session to /tmp/clp-session-report.md

Facts file (every number you may quote): FACTS_FILE
Depth check results: RESULTS
The user's context, in their words: "CONTEXT"
Their chosen focus: FOCUS
Extra categories found: EXTRAS
Report depth: DEPTH
Reader: READER

Rules:
- Read both style files before you write, and follow both: the plugin's writing guide (`writing-guide/rules.md`, from the plugin root) for the prose — Parts 0, 1 and 2 apply to everything, and Part 8 is the one for a report — and `references/report-style.md` for the report's own shape. Where a rule seems to be in both, the writing guide's wording is the definition.
- Before you save the file, run the sound pass over your draft: read `writing-guide/humanizer.md` (the 25 patterns, vendored verbatim) and `writing-guide/sound.md` (what they mean for a report), then follow humanizer's own four-step process, including reading the draft aloud and writing the final version by stating each point naturally instead of patching flagged phrases. This step is not optional and it is where a draft stops reading as generated. Vary sentence length; do not make every sentence the same size. The three that bite hardest in a report: a heading restated by the sentence under it, a claim carrying its denominator, its trap and its citation in one sentence, and `rather than` / `, not` / `instead of` kept where the contrast corrects nothing the reader believes.
- Every figure must appear in the facts file. You may not compute, estimate or infer a number that is not there. No arithmetic of any kind, with one exception that rule 8.2 of the writing guide defines: you may round a ratio to one decimal place for the body, and only for the body, leaving the exact value in its reference entry. Counts are never rounded.
- Mark the arguments, not the facts. An inference or a piece of domain knowledge says so where it appears, in words — "(inference)", "which suggests", "this is a reading of the records". A measured or derived claim carries no marker in the body: its link into the reference section is the offer to check it, and its entry states which of the two it is. The four tiers, the rules, and the three cases that look measured but may not be (a category, a score, and a value that may be a placeholder, which is a caveat and never a finding) are in references/evidence-tiers.md — read it and follow it; do not paraphrase it. Rules 2.10 and 8.10 of the writing guide govern how the marker is written.
- Close the report with `## Reference`: one entry per claim in the body, `### R3 Workflow instance spans`, opening with its tier and carrying the figure and the one command that reproduces it — a derived figure names its inputs and formula instead. Link every claim in the body into its entry by wrapping the figure the claim rests on, `stayed open [357.4 hours](#r1-wall-clock-span)`, so a reader chasing a number reaches for the number; never trail the sentence with a bracketed label. One link per claim, not one per number. Wrap the entries in a `<details>` block whose `<summary>` says what opening it is for, with both tags on lines of their own, so the section is collapsed until a reader opens it or follows a claim's link into it. Entry headings use letters, digits and single spaces only, because GitHub and the plugin's HTML saver strip punctuation differently and a dash or a colon breaks the link in one of them.
- Never quote a currency figure, even if asked what the session cost. Cost is in tokens: the log's totalCostUSD comes from an assumed unit price, not from what was billed, and is not always refreshed. Say that converting needs the reader's own rates.
- Never add up the per-kind token column. One API response can be recorded in several logs, because a fork inherits its parent's transcript, so the bundle total counts each response once and is smaller than those rows summed. Quote the bundle total for the session and a per-kind row for one agent. The facts file states the difference and why; if a reader adds the column up and gets more, that gap is the duplication and not an error.
- Lead with the focus. The other categories follow in the fixed order: reliability, cost, time, outcomes, harness faults, human loop, rework.
- Treat the user's context as a claim to check against the records, not as fact. If the records contradict it, say so plainly with both figures.
- Label every inference as an inference. The logs record activity, not value: they cannot tell you whether the work was good, only what happened.
- For each finding say whether it is a harness, provider, model, task or environment problem — or that the logs cannot tell them apart.
- Do not conclude a workflow succeeded from status "completed"; do not read an order of work from phase_order edges; do not claim one agent's output fed another. session-forensics.md has the full list.
- Report a commit or PR as existing only where the facts file says the repository confirmed it, and say how it matched.
- Write the short form unless DEPTH says thorough. Read references/report-style.md and follow every rule in it: lead each section with the finding, plain words and short sentences, one line per point, a table for three or more of anything. Shortening never drops an argument's marker, a derivation's trap, or a caveat that changes how a figure reads — cut the restatement around them instead, and never say a thing twice.
- Write for READER, as "Who the reader is" in references/report-style.md describes. An expert gets the terms of Claude Code named and not explained. A newcomer gets one or two sentences on a term the first time a finding uses it, and one sentence on why each finding matters, with the explanation of how Claude Code behaves labelled as domain knowledge. Anything else in READER is the user's own description of what they know: name what it covers, and explain what it doesn't. Either way, explain only what a finding uses.
- Keep the tooling out of the body. No query text, no field names, no commands outside the reference section, and no term a reader would have to know this tool to understand — write for someone who has never written a query. Say what was looked for in plain words, not how. The reference section is where the how goes.
- Do not wrap lines by hand. One line per paragraph, list item, table row and reference entry, however long it runs; the renderers reflow text themselves. The sketches below are wrapped only because they are instructions to you.

Format: "Report format" below has both forms — write the short one unless DEPTH says thorough.
Write the file. Return only its path and a three-line summary.
```

## Report format

Two forms. Write the short one unless DEPTH says the user asked for a thorough report. Both mark their arguments and carry each derivation's trap, and both put the tiers in the reference entries; `report-style.md` governs how either one reads.

### Short form — the default

Exactly these sections, in this order. The reference section is the last of them and the only place a command, a query or a field name may appear; there is no `## Checks` section and no query log in the short form — a reader who wants the whole audit trail is asking for the thorough form, and a writer who adds sections anyway has turned a short report into a long one.

```markdown
# Session <name>, <span>

<Three or four short sentences, strongest finding first, one finding per sentence. Do not stack what the session did, how long it ran and what it cost into one sentence. The link wraps the figure: `stayed open [357.4 hours](#r1-wall-clock-span)`.>

## <Focus category, named as the finding rather than the category>
<Leads. The user's focus or the shift you proposed: the figure first, then what it means, then the one example id worth opening. A short paragraph or a short list.>

## The seven categories

| Category | Headline |
|---|---|
| Reliability | <headline with its denominator, the figure carrying the link> |
| Cost | |
| Time | |
| Outcomes | |
| Harness faults | |
| Human loop | |
| Rework | |

<One row each, the figure in each row linking to its reference entry. A category with nothing notable says so in its row. Bold the one row that matters most, so the table ranks itself instead of a paragraph underneath naming the row. Anything needing more than a row — per-tool error rates, a bucket split, the largest offenders — goes directly under the table, and only where it changes what the reader would do.>

## Extra categories
<Only when the extras subagent proposed some: one bullet each, with the count and one example id. Say they come from the extras pass, not the facts file, and never merge their counts into a headline figure.>

## What the logs cannot tell you
<Three or four lines: quality of the work, money as opposed to tokens, and anything needing external data. Add any caveat the facts file raised about its own figures — notably, when many token-bearing records carry no `message_id` the bundle total may still double-count, and the facts file says how many there were.>

## Reference

<details>
<summary>How to check each figure. A figure's link in the report opens its entry here.</summary>

### R1 Attempts by status
<Opens with the tier — measured — then 18 of 18 ended ok, then the one command that reproduces it, from the facts file's verification section, indented as a code block. A derived figure names its inputs and formula here instead of, or beside, a command. The tier lives here and not in the body, because this is where someone reproducing the number needs it.>

### R2 <Plain name, letters digits and single spaces only>
<The next claim's check. One entry per claim in the body, in the order the claims appear; figures one check establishes share the single entry they both link to.>

</details>
```

**Entry headings must be letters, digits and single spaces only** (`### R3 Workflow instance spans`, not `R3 — Workflow instance spans: 22.9 and 15.2 min`). Both GitHub and the plugin's HTML saver turn a heading into an anchor and strip punctuation differently, so an em dash or a colon yields a link that resolves in one and not the other.

### Thorough form — only when asked

The same report with every figure argued in full: the seven categories as their own sections in the fixed order (`## Reliability` … `## Rework`, each with the headline figure, its denominator, what it means and one example id), then

```markdown
## Reference
<The same reference section the short form ends with, collapsed the same way and linked from the body the same way: one heading per claim, `### R3 Workflow instance spans`, carrying the headline figure, the one command that reproduces it, and a derived figure's inputs and formula. Measured figures name their query; derived figures name what they are computed from. This section is what makes the rest arguable rather than trusted.>

## Query log
<Each check that ran, its result, and each that failed or matched nothing.>
```

## Scoring

**The script measures; you score.** `clp session measure --axes` computes each axis's raw value and the components behind it, and assigns nothing. You map each value to 0–10 using a scale file, because a customer's thresholds are their own: what counts as an acceptable stall rate or cache hit rate is a policy decision, not a measurement.

### Find the scale

Load the first of these that exists, and say in the report which one you used and its `scale_version`:

1. a path the user names
2. `./.clp-scoring-scale.json`
3. `.claude/clp-scoring-scale.json`
4. `${CLAUDE_PLUGIN_ROOT}/scoring-scale.json` — the shipped default

Validate it before trusting it: `clp session measure --axes --check-scale --scale FILE` prints `SCALE_OK`, or one `SCALE_PROBLEM=` line per defect (a missing axis, a malformed or non-exhaustive ladder). A customer scale that fails the check is reported to the user as-is; do not fix it silently and do not fall back to the default without saying so.

### Apply it — with the tool, not by hand

```bash
clp session score --bundle BUNDLE --format table [--scale FILE] [--cohort task_type=… --cohort repo=…]
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

The raw value is measured and reported either way — `clp session measure` never applies the gate — so the human-and-idle share stays visible in the Time category even when the axis is not scored. Note also what the gate does **not** do: it accounts for intent, not for quality. The logs cannot tell you whether unattended work was any good, only whether it was delivered.

| Group | Axes | Owner |
|---|---|---|
| A. Platform | execution reliability, provider stability, cache efficiency, config correctness, runtime honesty | infra / gateway |
| B. Behavior | tool proficiency, tool selection fitness, rework rate, context discipline, orchestration efficiency | model / harness |
| C. Outcome | delivery throughput, delivery integrity, verification rigor, autonomy, self-recovery | shared |
| D. Cost | cost per outcome, waste ratio, cache recovery, model-mix fitness, cost concentration | infra + orchestration |

### Present it

- **Show the raw value next to every score.** The value is the measurement and the score is an interpretation of it against this scale; a reader must be able to disagree with the second without doubting the first.
- **Say what each score's threshold rests on.** Every axis in the scale carries a `basis` — `definitional`, `mechanism`, `observed` or `judgement` — and a `rationale`. A low score on a `definitional` axis is strong evidence; a low score on a `judgement` axis is a starting threshold someone chose, and a reader is entitled to push back on it. Quote the `rationale` for any axis you build an argument on, and report the `basis_summary` so it is clear how much of the scorecard rests on judgement. Never present a judgement-based score as though it were measured.
- **Point at calibration when judgement dominates.** If most scored axes are `judgement`, say so and name the fix: `clp bundle-review --json` over the customer's own sessions gives the percentiles to replace those rungs with, within a cohort. A scorecard built mostly on defaults is a baseline, not a verdict.
- **Report the four group means separately. Never a single composite.** The groups have different owners; averaging them hides which one is at fault. A session whose platform scores low and whose outcome scores higher delivered *despite* its platform, and one number would say the opposite.
- **`C1` is cohort-relative.** Throughput per model-hour only compares within sessions of similar task shape. Mark it every time.
- **`A3` and `D3` are the same measurement.** Cache hit rate is both a platform fault and the largest cost lever; the duplication is deliberate. Say so rather than letting it look like an error.
- **Scores compare sessions; they do not judge one.** A single session's scorecard is a baseline. Trends need a cohort key — task type, repository, duration band — recorded per session, and a period resting on one or two sessions is about those sessions, not a trend.
- **Alerts are not scores.** The `ALERT=` lines print the value and the threshold that fired them, and exist only to order the focus question. Do not put an alert in the report without saying what fired it.

## Ask where to save the report

Right after spawning the writer (step 6). One AskUserQuestion, header `Save`, options built from `clp report save --list-formats`:

> **Where should the report go?**
>
> - **Markdown file** — written next to the bundle, or a path you name.
> - **A claude.ai page** — a shareable page. Offer only when the Artifact tool is available.
> - **PDF** — offer only when `--list-formats` prints a `PDF_ENGINE` path.
> - **Leave it in /tmp** — no copy; the report stays where the writer put it.
