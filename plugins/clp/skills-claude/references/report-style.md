# Report style

The single definition of how a report reads. Both routes' report writers read this and follow it. Do not restate these rules in other words anywhere else — two definitions of a style drift the same way two definitions of an evidence tier do.

## Default: short and skimmable

Write the **short form**. It is the default on every run. Write the **thorough form** — the fuller section list each route's "Report format" gives — only when the run's prompt says the user asked for a thorough or detailed report. A writer that has not been told the user asked for one writes short.

The reader is skimming for what to do next. Aim for a report that fits on one or two screens; no section longer than a short paragraph. A category with nothing to say gets one line saying so, not a paragraph explaining the absence.

## Rules

1. **Lead with the finding, not the method.** "**Workflow agents used 99.4% of the input tokens** — 18,685,621 of 18,792,752" — not "the facts pass computed a per-archive-kind token breakdown".
2. **Progress logically.** Each section answers one question, in the order a reader asks them: what happened, which number matters, why, what it means, how to check it. Do not open a section with background already given, and do not restate an earlier section's figure.
3. **Use simple language.** Short sentences, one idea each. Prefer the plain word — "used" not "utilised", "shows" not "evidences". Cut any phrase whose only job is to sound thorough. Keep the user's own words for their own concern. Define an unfamiliar term in a clause the first time it appears.
4. **Keep the tooling out of the body.** No query syntax, no field names, no command lines, and no tool vocabulary in the sections a reader reads for the findings — a reader who has never written a KQL query is the normal case, and the report must make sense to them. Say what was looked for in plain words, not how: "the errors grouped by their message", not `shape(toolUseResult)`. Where a term from the tooling cannot be avoided (a template, a severity, a category), gloss it in a clause the first time.
5. **Put the checks in an appendix, and link the claims to it.** The report ends with `## Appendix — checking each figure`, which holds one entry per figure quoted above: the figure, its tier, and the one command that reproduces it or the inputs its derivation uses. Every claim in the body ends with a link to its entry — `[A3](#a3-workflow-instance-spans)` — so a reader can validate anything they doubt without the body making them read a query. One link per claim, not one per number: figures that one check establishes share its entry. A reader who wants to trust the numbers and not check them skips the whole appendix.
   - **Entry headings carry letters, digits and single spaces only** (`### A3 Workflow instance spans`). Both GitHub and the plugin's own HTML saver turn a heading into an anchor, and they strip punctuation differently, so an em dash or a colon in a heading produces a link that works in one renderer and not the other.
6. **Make it skimmable.** A heading states its section's content. Front-load each sentence with the number or noun that matters. Bold the figure a reader would quote. One line per point; add a second only to carry a caveat.
7. **A table for three or more of anything** — buckets, per-tool rates, one row per category. A table scans faster than the same facts in prose, and a share column is easier to trust beside the raw counts. Skip charts: these reports are read in a terminal or a plain file, where a table is clearer than anything a chart would add.
8. **Keep every evidence tier and every trap.** Shortening never drops a marker, a derivation's trap, or a caveat that changes how a figure reads. Cut the restatement around them instead.
9. **Say nothing twice.** If the focus section already made a point, a later section refers back in one clause instead of re-explaining it.
10. **Do not wrap lines by hand.** One line per paragraph, per list item, per table row and per appendix entry, however long it runs — a 900-character paragraph is normal. The width a paragraph happens to fill is the reader's window, not yours: the saved HTML reflows text to its own column, so a paragraph broken at your column shows those breaks mid-sentence in a wider one, and the source is harder to edit and to diff. Lines break only where the markup does — table rows, headings, code blocks, and the point a list starts. An appendix command goes on one line even at 300 characters; the code block scrolls and wraps on its own.

## A worked pair

Too long, and it buries the point:

> The facts pass computed a per-archive-kind token breakdown, and it shows that the workflow-agent archive kind accounted for 18,685,621 of the 18,792,752 input tokens recorded across the bundle, which is 99.4% of the total, while the main thread accounted for the remaining 107,131 (0.6%).

Short, and it leads:

> **Workflow agents used 99.4% of the input tokens** — 18,685,621 of 18,792,752. The main thread used 107,131 (0.6%).

Both sentences are true and neither number changed. The second is the one a reader needs.
