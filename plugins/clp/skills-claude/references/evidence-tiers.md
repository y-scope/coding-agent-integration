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

## Two things that look measured and are not

**A category is classification output, not data.** In the insights pipeline a model groups templates into categories and ranks them; in the trajectory pipeline the seven categories are a fixed design choice. Either way the *name* and the *ranking* are judgement, while the *record counts within* a category are measured. Report them that way: the count is checkable, the grouping is a claim about what the records mean. A category table presented as though the whole thing were counted is the most easily missed violation of these tiers.

**A score is a policy mapping, not a measurement.** `clp session score` maps a derived value onto 0–10 through a ladder in the scale file. The value is checkable; the score is only as good as the threshold someone chose, which is why each axis carries a `basis` and why the raw value is always shown beside the score. Never report a score without its value, and never call a score a measurement.

## Where the tier has to appear

- **In the report**, on every claim, and in the closing **Reference — checking each figure**, whose entries give each claim's figure and the one command that reproduces it, linked from the claim itself. That section is what makes the rest arguable rather than trusted.
- **In chat**, on anything you assert to the user — the phase summaries, the findings at the close, the descriptions in a question. A figure you quote in conversation is quoted just as loudly as one in the report.
- **Nowhere by inventing a fifth label.** If a claim does not fit one of the four, it is usually an inference that has not been recognised as one.
