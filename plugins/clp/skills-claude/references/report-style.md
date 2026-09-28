# Report style

The shape of an analysis report. `writing-guide/rules.md` owns the prose — how a sentence reads, and whether a claim is one the evidence supports. Read that file too, and follow both; where a rule seems to be in both, the writing guide's wording is the definition and this file only adds what is particular to a report.

The section list for each route is in that route's reference under "Report format": `session-insight.md` for a Claude Code session, `log-insights.md` for any other archive.

## Default: short and skimmable

Write the **short form**. It is the default on every run. Write the **thorough form** only when the run's prompt says the user asked for a thorough or detailed report. A writer that has not been told the user asked for one writes short.

The reader is skimming for what to do next. Aim for a report whose body fits on one or two screens; no section longer than a short paragraph. A category with nothing to say gets one line saying so, not a paragraph explaining the absence.

## Who the reader is

The run asks the user how well they know the system that wrote the logs, and the writer's prompt carries the answer as `READER`: `expert`, `newcomer`, or the user's own description in their words.

- **`expert`** is the default, and the report every other rule in this file describes. The reader knows the system, so a term of it is named and not explained: a Claude Code user knows what a fork or a cache read is, and a CockroachDB operator knows what a lease is.
- **`newcomer`** keeps the same sections, the same order and the same findings, and adds the background each finding needs, where it needs it. The first time a term of the system appears, one or two sentences say what it is. Each finding gets one sentence on why it matters. The body may run past two screens to fit them.
- **The user's own words**, such as "I run Postgres but I'm new to CockroachDB" or "skip the basics, explain the caching", describe what the reader knows. Name what falls inside it, and explain what falls outside it the way a newcomer's report would. Where the words leave a term uncovered, go by what they imply about the reader; a DBA new to CockroachDB knows what a transaction is and not what a range lease is.

What stays the same for every reader:

- **Explain only what a finding uses.** No glossary section, no tour of the system, and no background for a category with nothing to say. A newcomer who reads a paragraph on how caching works and then finds no caching finding has been lectured, not helped.
- **A definition is not an argument; an explanation often is.** "A cache read is the part of a prompt the provider already had" says what a word means and needs no marker. "So the session paid full price for most of its prompts" is a claim about this system's behaviour, and it is domain knowledge that says so, under rule 2 below.
- **Background never carries a number.** Every figure still comes from the facts file, whoever the reader is.
- **The tooling stays out either way.** Rule 4 is about this plugin's vocabulary, which no reader should need. This section is about the vocabulary of the system the logs came from.

## The report's own rules

Parts 0, 1 and 2 of `writing-guide/rules.md` cover how the sentences read, and its Part 8 covers how a report lands on someone skimming it — the headings, the rounding, the placement of a trap or a citation, and what a table owes its reader. These five cover what a report is, on top of both.

1. **Lead with the finding.** The opening takes the single most important thing the analysis found, with its figure, so that a reader who stops there has the answer. That is rule 0.4 of the writing guide applied to a report.
2. **Mark the arguments, and carry every trap.** An inference or a piece of domain knowledge says so where it appears, in words. A measured or derived claim says nothing: it links to its entry in the reference section, which is a better offer than a badge because the reader can act on it, and the entry states which of the two it is. A derived figure still carries the trap that keeps it honest — the one that says what the number is not. Shortening never drops a trap or an argument's marker; cut the restatement around them instead. This is the rule that separates a report from prose: the reader must be able to tell a count from an argument. Rules 2.10 and 8.10 of the writing guide are the definition.
3. **A table for three or more of anything** — buckets, per-tool rates, one row per category. A table scans faster than the same facts in prose, and a share column is easier to trust beside the raw counts. Skip charts: these reports are read in a terminal, a plain Markdown file or a saved page, where a table is clearer than anything a chart would add.
4. **Keep the tooling out of the body.** No query syntax, no field names, no command lines, and no tool vocabulary in the sections a reader reads for the findings. A reader who has never written a query is the normal case. Say what was looked for in plain words, not how: "the errors grouped by their message", not `shape(toolUseResult)`. Gloss an unavoidable term in a clause the first time it appears.
5. **Put the checks in a reference section, and link every claim into it.** The report ends with `## Reference`, holding one entry per claim: the figure, its tier, and the one command that reproduces it or the inputs its derivation uses. The link wraps the figure the claim rests on — `stayed open [357.4 hours](#r1-wall-clock-span)` — so a reader chasing a number reaches for the number, rather than decoding a bracketed label trailing the sentence. One link per claim, not one per number: figures that one check establishes share its entry, and a reader who trusts the numbers never opens the section. It is called the reference and not the appendix because a report may later want a real appendix, and two things with one name is how a reader ends up in the wrong one.
   - **The entries are collapsed by default.** The heading stays visible and the entries sit inside a `<details>` block, whose `<summary>` says what opening it is for. A reader who trusts the figures never meets a command; one who doubts a figure opens the entry their claim pointed at, and the saved page opens the enclosing block for them when the link lands inside it. `<details>` and `<summary>` go on lines of their own — the saver passes exactly those two tags through as markup, and everything else stays escaped, because a report quotes log text that must never become markup.
   - **Entry headings carry letters, digits and single spaces only** (`### R3 Workflow instance spans`). Both GitHub and the plugin's own HTML saver make the anchor from the heading and strip punctuation differently, so an em dash or a colon produces a link that works in one renderer and silently fails in the other.

Rule 0.9 of the writing guide settles repetition: if the focus section made a point, a later section refers back to it in one clause instead of making it again.

## A worked pair

The failure these rules prevent, twice over: a sentence that restates its method instead of its finding, and a figure quoted without the denominator that gives it meaning.

> The facts pass computed a per-archive-kind token breakdown, and it shows that the workflow-agent archive kind accounted for 18,685,621 of the 18,792,752 input tokens recorded across the bundle, which is 99.4% of the total, while the main thread accounted for the remaining 107,131 (0.6%).

> **Workflow agents used 99.4% of the input tokens** — 18,685,621 of 18,792,752. The main thread used 107,131 (0.6%).

Both sentences are true and neither number changed. The second one is a report.
