# Writing rules

Rules for PR summaries, bug reports, docstrings, comments, READMEs, and the analysis reports this plugin writes. Each line is a yes-or-no test that you can run over a draft.

Every rule came from a document that a reviewer sent back. The sentences that prompted them, and the arguments for each rule, live in `examples.md`. Read that file when a rule needs justifying, when onboarding someone, or when tuning a generation process. This file stays short so that it can be loaded every time.

Parts 0, 1, and 2 apply to everything. Then read the part that matches what you are writing.

**Who edits this.** A person, by hand, the same way as the file it came from at `~/.claude/docs/writing-rules.md`. When a reviewer rejects something this file does not cover, add the sentence to `examples.md` and one line here. The copy in this plugin is what the analysis report writers read; keep it in step with the original, and prefer editing both to letting them drift.

**An analysis report reads Part 8 and one more file.** Part 8 covers how a report lands on a reader who is skimming it. `../skills-claude/references/report-style.md` owns the report's shape — the short form by default, the thorough form on request, the evidence tier on every claim, the collapsed reference section and the links into it, and the rule that no query text, field name or command appears outside that section. Read it with this file, and follow both.

## Part 0: Concision, point, and flow

- **0.1** Delete the sentence and see what breaks. When nothing is lost, it stays deleted. Applies to whole sections too.
- **0.2** No sentence restates its predecessor in different words.
- **0.3** No preamble. `It is worth noting that`, `In order to`, `This section describes`, and `As mentioned above` delete without loss.
- **0.4** Conclusion first. A reader who stops after the first paragraph still has the answer.
- **0.5** One idea per sentence. Two independent clauses sharing no subject are two sentences.
- **0.6** Each paragraph has one job that you can name in a few words.
- **0.7** Consecutive paragraphs connect. When two can be swapped without loss, write them as a list. The list reports the items that exist; never pad one to reach a round count.
- **0.8** Order by logic, not chronology: conclusion, mechanism, evidence, caveats.
- **0.9** No fact appears in two places. The second copy drifts. Count the occurrences before deleting one: "already said above" is itself a claim.

## Part 1: Readability

- **1.1** Every relative clause keeps its pronoun. Never use a zero relative before a backticked identifier. Check 1.10 first, because the best fix is often to drop the clause rather than to repair it.
- **1.2** Every complementizer is present: `so that`, `means that`, `ensures that`, `note that`.
- **1.3** Every preposition has its object.
- **1.4** Every pronoun binds to the noun you intended. `which` binds to the nearest noun phrase, and getting this wrong can invert the meaning.
- **1.5** No participle that can be misread as a past-tense verb.
- **1.6** No noun stack three deep.
- **1.7** The frame `X rather than Y, since Z` appears at most once.
- **1.8** Prose sections use full sentences, not telegraphic fragments.
- **1.9** No finite verb that can be read as a plural noun. Suspect a possessive followed by `answers`, `results`, `reports`, `matches`, `runs`, `builds`, or `checks`.
- **1.10** Reduce the clause instead of repairing it. `the warning logged by the scheduler` beats `the warning that the scheduler logs`, and `a value computed at startup` beats `a value that is computed at startup`. Deleting `that is` or `which is` before a participle is the deletion that helps; deleting `that` before a clause with its own subject is the one that hurts (1.1). Stop reducing as soon as a noun could be misread as the subject (1.5).

## Part 2: Claims and evidence

- **2.1** Every causal claim cites the code that causes it. Otherwise describe the symptom and stop.
- **2.2** No scaling law without two measurements. One sample supports a rate and nothing more.
- **2.3** Name only the subsystems whose call path you traced.
- **2.4** The stated trend matches the attached numbers.
- **2.5** Impact is whatever stops happening after the fix.
- **2.6** Amplifiers are claims, not emphasis: `pure waste`, `never`, `unbounded`, `indefinitely`.
- **2.7** State what you did not verify. "Consistent with the log" and "confirmed by running it" are different claims.
- **2.8** Every number keeps the unit that it was measured in.
- **2.9** A small bug is filed as a small bug. Severity is an observation, not a lever.

## Part 3: PR summaries

- Say what changed and why. Do not restate the diff line by line.
- Length tracks the change, not the diff. Twenty mechanical call-site updates are one sentence. A one-line fix to a subtle bug can need a screen.
- The body is read twice: once against the diff, and once at release time by someone who no longer has the branch. Rule 0.4 serves both, so put the answer in the first paragraph.
- Open with the mechanism, written so that a reviewer can check it against the diff.
- Every validation claim was actually run, and the summary names the command.
- Re-run the numbers before you submit. A test count goes stale silently as the branch moves, and it is the cheapest claim for a reviewer to disprove.
- Deferring to CI is not running it. Write "not exercised locally; the CI build covers it" rather than "covered by CI", which reads as a check that you performed.
- Remove stale claims when the scope changes. Label or drop a validation link from before a review round.
- Do not justify or trim a change on CI, runtime, or resource cost; treat them as negligible. Two exceptions, both routinely over-cut: the change's *subject* is the cost, or the cost explains a design choice *inside* the change. Test it by deleting the clause — if no reason is left standing, put it back.
- Do not restate what a linked issue says. Link and move on.
- Justify nothing by work that has not landed. A reviewer can only check the branch in front of them.
- Describe the change, not the review that produced it. Arguments belong in the review reply.
- Follow the repo's PR template when it has one. Point reviewers at the files that carry the risk.
- Titles: Conventional Commits, first character capitalized, trailing period, imperative form.

## Part 4: Bug reports and issues

- The title names the component that contains the defect, not the one that shows the symptom.
- The mechanism opens the report, with the smallest code excerpt that makes the defect visible.
- One log sample, trimmed to the fields that carry information.
- Cut evidence that needs a paragraph of interpretation to avoid misleading the reader.
- Reproduction steps are the shortest path to the symptom, not a transcript.
- Symptom and mechanism stay separate. One is an observation; the other is a conclusion that needs 2.1's citation.
- The impact section is the highest-risk paragraph. Every part 2 rule applies, and 2.5 settles its content.
- Length tracks the defect. A one-line fix deserves about a screen.

## Part 5: Docstrings and code comments

- A docstring does not name its own subject. Write `Returns the active worker count`, not `This function returns the active worker count`. The same deletion applies to `This file`, `This module`, `This class`, `This struct`, `This trait`, `This method`, and `This test`. The comment is attached to the thing, so the reader already knows what it documents.
- The first line says what the thing is or what it does, not what it is for. A class, struct, or module opens with a noun phrase; a function or method opens with its effect. Usage framing belongs in the body, if anywhere.
- Say what the reader cannot already see. Do not restate the signature, the parameter names, or the types.
- Explain why the code is this way, especially when the reason is invisible locally.
- Part 1 matters most here, because the line-length limit tempts you to delete function words, and a wrong comment is worse than none.
- Do not write a comment that an ordinary refactor would invalidate.
- Do not write in the past tense about a hazard that is gone.
- Give a reason that the next reader can check. Name the tool behaviour, not the conclusion.

## Part 6: READMEs and user-facing docs

- The first two sentences say what the thing is and who it is for, before any prerequisite.
- Assume a reader who has not read the code and does not know the internal vocabulary.
- Every command block is copy-pasteable as written, and was run in that form.
- Prerequisites appear before the first command that needs them.
- Each command says what it produces, so that the reader can tell whether it worked.
- Describe behaviour that exists now. Planned work does not belong in a README.
- Prefer a link to the authoritative source over a copy, because the copy goes stale.
- Do not add a defensive note about a failure mode that has not happened.
- When a change makes a README statement false, fixing it is part of that change.
- No marketing adjectives and no claims of ease.

## Part 7: What not to add

- Add a test, doc section, or tracking issue only when it pins behaviour that broke or could silently regress. Ask what bug it would have caught.
- "The code became reachable or testable" is not by itself a reason to test it.
- Weigh the scaffolding against the change even when the test would have caught the bug.
- Do not widen a change with repo-wide style refactors.

## Part 8: Analysis reports

How a report lands on someone skimming it. Parts 0 to 2 decide whether a sentence is worth keeping; these decide whether a reader finds it. `../skills-claude/references/report-style.md` owns the sections and their order, and does not repeat these.

The reader arrives doubting the numbers and short of time. Every rule below buys one of those two back.

- **8.1** The headings and the bold openers, read alone, give the findings in order of importance. Where a section has one finding, the finding is the heading: `Nothing the session claimed to commit reached the repository` beats `Outcomes`. The fixed structural headings a report format mandates are exempt.
- **8.2** Round a ratio to the precision the reader can act on, normally one decimal place. `28.3%` beats `28.2671%`, which reads as machine output and changes no decision. Never round a count, always keep the exact value in the figure's reference entry, and treat this as the only arithmetic allowed: never combine two numbers, convert a unit, or recompute a share.
- **8.3** Every ratio carries the counts it came from, and carries them first: `5,312,160 of 18,792,752 (28.3%)`. A bare percentage cannot be checked at a glance, and a reader who cannot check it discounts it.
- **8.4** The finding comes before its qualification. A trap, caveat or exclusion follows the claim it qualifies. It never opens the paragraph and never sits between the claim and its number, because a reader who stops after one sentence should leave holding the finding rather than the warning.
- **8.5** A claim's evidence tier and its reference link sit together at the end of the sentence, never mid-clause and never stranded on a line of their own after a table. The eye should leave a sentence at the full stop.
- **8.6** At most two consecutive paragraphs open with a bold lead-in. Six in a row emphasise nothing. Bold the one finding per section that a skimmer must not miss, and let the rest open in plain text.
- **8.7** A table cell holds one fact. A cell that needs a full sentence and two citations belongs in prose, and a row carrying two findings is two rows.
- **8.8** A table that has one row worth more than the others says so in the table. Telling the reader in the paragraph underneath which row to look at means the table failed to.
- **8.9** Name what was searched and what was not. A reader judges a report by its blind spots, so the limits are a section, not an apology folded into a sentence.

## Trailers

Banned outright, in every repo, with no per-repo exception to check:

- `Co-Authored-By:` naming an AI, in any commit message.
- `Generated with <tool>`, `🤖 Generated with ...`, `Created by ...`, and every other tool-attribution line, in commit messages and in PR and issue bodies alike.
- Any trailer that records which tool wrote the text rather than a fact about the change.

A commit message is a subject and a body. A PR or issue body ends with its last section.

Trailers carrying real information stay: `Fixes #N`, `Co-Authored-By:` naming an actual human collaborator, and `Signed-off-by:` where a repo requires a DCO sign-off.

## Wrapping

- PR, issue, and review-comment bodies are never hard-wrapped. One unbroken line per paragraph and per bullet.
- A new Markdown file is not hard-wrapped either. Do not reflow to satisfy MD013 unless the repo's lint task enforces it.
- An existing hard-wrapped file keeps its convention. Rewrap only the paragraphs that you were already editing. Table rows are never wrapped.
- Commit message bodies vary by repo, so confirm rather than assuming.
- An analysis report is a new Markdown file, so it is not wrapped by hand: one line per paragraph, list item, table row and reference entry, however long it runs. The renderers reflow text to their own column, and a paragraph broken at the author's column shows those breaks mid-sentence wherever the reader's column is wider.

## Sound

Rules for how a finished draft sounds, after it satisfies Part 0. They change the phrasing, not the length or the facts.

- **No em-dash as a default joint.** It is a recognisable verbal signature when it carries clause after clause. A period, a comma or a colon usually does the work.
- **No bold lead-in on a bullet that does not need one.** Bold the figure or the term a reader would search for, not the first three words of every line. Rule 8.6 puts a ceiling on consecutive bold paragraph openers for the same reason.
- **Check every list against 0.7 before keeping it.** A list that reports the items that exist stays; one padded to look thorough is cut to the items that exist. Bold lead-ins commonly appear on exactly the padded ones.
- The `humanizer` skill (`npx skills add blader/humanizer --global`, then `/humanizer`) does this pass mechanically over a draft. Run it when the report is for someone outside the team, or when a draft reads as generated. It never adds facts, so it cannot repair a claim that Part 2 rejects.
