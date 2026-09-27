# Writing guide

How the prose this plugin produces is written. Three files, all plain Markdown, all meant to be edited by a person:

- **`rules.md`** — the rules. Each one is a yes-or-no test you can run over a draft: concision and flow, readability, claims and evidence, then the part that matches what you are writing. Loaded by the report writers on every run, so it stays short on purpose.
- **`humanizer.md`** — how a finished draft sounds. 25 patterns with their trigger words and before/after pairs, vendored verbatim from the `humanizer` skill under MIT so that a writer gets them without a network call and without a lossy summary. Refresh it from upstream; do not edit it to fit a house style.
- **`sound.md`** — the adapter for `humanizer.md`: which of its patterns an analysis report trips over, and which of a report's habits it would wrongly cut (a derivation's trap is not a stacked qualifier).
- **`examples.md`** — the evidence behind the rules: the sentences a reviewer rejected, quoted verbatim, with the rule each one produced. Grows over time. Read it when a rule needs justifying.

## Who reads it

`skills-claude/analyze-logs` spawns a subagent to write each analysis report. That writer reads `rules.md` and `report-style.md` before drafting, then `humanizer.md` and `sound.md` as a revision pass before saving:

- `rules.md` owns the prose — how a sentence reads, whether a claim is one the evidence supports, and, in Part 8, how the finished report lands on someone skimming it.
- `report-style.md` owns the report's shape — the short form by default, the thorough form on request, the evidence tier on every claim, the collapsed reference section and the links into it, and no query text anywhere outside that section.

Neither restates the other. If a rule seems to belong in both, it belongs in `rules.md` and the report file points at it.

## How to change it

Edit the Markdown. There is no build step and no schema: the files are read as they are, so a change takes effect on the next report.

Two conventions worth keeping. A rule is one testable line with a number, so a later reader can cite it; an example is a real rejected sentence, quoted rather than paraphrased, because the sentence teaches what the rule cannot. When you add a rule, add the example that caused it, and add the rule's number to the example.

These files are copied from `~/.claude/docs/writing-rules.md` and `~/.claude/docs/writing-examples.md`. The copies are here so that the plugin carries its own writing standard and so that anyone reading this repository finds it without knowing about a home directory. Keep the two in step, or treat these copies as canonical for report writing and say so in a commit message when they diverge.
