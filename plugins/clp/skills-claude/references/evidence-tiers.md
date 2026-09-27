# Evidence tiers

The single definition of how a claim is labelled. Both analysis skills and both report writers use it; do not restate it in your own words anywhere else, because two definitions drift and the drift is the exact failure this exists to prevent.

A reader must never have to guess which kind of claim they are reading.

## The four tiers

| Tier | What it is | How a reader checks it |
|---|---|---|
| **measured** | read from the records: a count, a sum, a field value | run the query the facts file names |
| **derived** | arithmetic over measured values: a share, a rate, a per-unit figure | its inputs and its formula, both named |
| **inference** | a claim about what caused something *in these records* | nothing reproduces it; it is an argument from the figures |
| **domain knowledge** | a claim about *this kind of system*, not about these records | nothing here; it rests on knowing the application |

The first two are verifiable and must be. The last two are arguments and must be labelled as such. Mixing them is not a style problem: a reader who cannot tell a count from an opinion has no way to disagree with the opinion.

## Rules

**A derived figure is never quoted bare.** "42.5% of the session was spent waiting" is not reportable. "Human and idle together are 42.5% of the 2,899 end-to-end minutes, and the buckets do not double-count" is, because it can be checked and it can be argued with.

**A derivation carries its trap.** Summed agent minutes are not elapsed time, because attempts run in parallel. Repository-confirmed commits are mostly attributed by time and subject rather than proven by a printed sha. Per-kind token rows do not sum to the bundle total, because a fork inherits its parent's transcript. A true number quoted without its trap is how a report becomes wrong while every figure in it is correct.

**Domain knowledge may not supply a number.** No rate, no duration, no count. Those come from the facts file or they do not appear.

**Domain knowledge never overrules a measurement.** Where what is known about the system disagrees with what the records show, report the records and say the expectation did not hold.

**An inference names what would settle it.** A causal claim that cannot be tested against these records should say so, and say what other evidence would decide it.

## Three things that look measured and may not be

**A category is classification output, not data.** In the insights pipeline a model groups templates into categories and ranks them; in the trajectory pipeline the seven categories are a fixed design choice. Either way the *name* and the *ranking* are judgement, while the *record counts within* a category are measured. Report them that way: the count is checkable, the grouping is a claim about what the records mean. A category table presented as though the whole thing were counted is the most easily missed violation of these tiers.

**A score is a policy mapping, not a measurement.** `clp session score` maps a derived value onto 0–10 through a ladder in the scale file. The value is checkable; the score is only as good as the threshold someone chose, which is why each axis carries a `basis` and why the raw value is always shown beside the score. Never report a score without its value, and never call a score a measurement.

**A value can be a placeholder.** A source that does not measure something often writes a fixed value into the field instead of leaving it out. The value is then recorded faithfully, and it still says nothing about what happened. Be suspicious when a field that should vary does not: it is 0 on every record, or empty, or one round number such as 4096 or 1000, or the same across hundreds of records when each record describes different work. A duration of exactly 0 or a timestamp that never advances fits the same pattern. So does a count that is exactly the configured limit. There is often a reason nobody reading the logs knows, like a provider or proxy that does not report the field, and the user may know it.

- **Treat it as a caveat, not a finding.** Quote it as logged, next to one sentence saying why it may not be real, and build no conclusion on it. A derived figure resting on it inherits the doubt, and so does a score.
- **Ask when it matters.** If it would change the headline, a recommended focus or a score, ask the user whether the field is really measured by that source. Give the background in the question: which field, how many records, the value, and why a source might write that. Do not ask about a suspicious value that changes nothing.
- **Never settle it yourself.** Without an answer it stays a caveat. A guess in either direction is worse: calling it real reports a problem the session may not have, and calling it a placeholder throws away a real measurement.

The number is exactly what the records say. Whether it is a measurement is a separate claim, and the user is often the only one who can settle it.

## Where the tier has to appear

- **In the report body, on the arguments.** An inference or a piece of domain knowledge says so where it appears, in words. A measured or derived claim carries no marker: it links to its entry in the report's closing Reference section, and that is a stronger offer than a badge, because the reader can act on it. Rule 2.10 of `../../writing-guide/rules.md` is the definition and the reasoning; a body that marks every claim buries the five that are arguments under the fifty that are facts.
- **In every reference entry, on every claim.** Each entry opens with its tier and gives the figure with the one command that reproduces it, or a derived figure's inputs and formula. This is where measured and derived are told apart, because it is where someone reproducing the number needs to know which they are doing. That section is what makes the rest arguable rather than trusted.
- **In chat**, on anything you assert to the user — the phase summaries, the findings at the close, the descriptions in a question. Use the words, not letters. A figure you quote in conversation is quoted just as loudly as one in the report.
- **Nowhere by inventing a fifth label.** If a claim does not fit one of the four, it is usually an inference that has not been recognised as one.

This file decides which of the four a claim gets, and that is all it decides. How the label is printed, and whether it is printed at all, is a writing question that `../../writing-guide/rules.md` settles in rules 2.10 and 8.10.
