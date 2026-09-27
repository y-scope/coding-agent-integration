# Writing examples

The evidence behind `rules.md`. Every sentence quoted here was rejected by a reviewer, and the rule it produced is cited beside it.

This file grows. When a reviewer rejects something for a reason that is not covered yet, add the sentence here and add one line to `rules.md`. Keep the rejected sentence verbatim, because it teaches more than the rule does.

Copied into this plugin from `~/.claude/docs/writing-examples.md` so that the analysis report writers can read it. Edit it here as freely as there; if the two ever diverge, this copy is the one the plugin's reports are written from.

---

## Part 1: Readability

### 1.1 Every relative clause keeps its pronoun

The failure is worst when the subordinate clause's subject is a code identifier, because the reader parses the identifier as part of the preceding noun phrase and has to restart.

| Reject | Accept |
| --- | --- |
| the suffixed columns `SchemaTree.resolvePolymorphicConflicts` produces | the suffixed columns **that** `SchemaTree.resolvePolymorphicConflicts` produces |
| `__json_string`, the column the `GET_*` UDFs read through | `__json_string`, the column **that** the `GET_*` UDFs read through |
| a field the encoder stored under several types | a field **that** the encoder stored under several types |
| every tag `docker/metadata-action` produces | every tag **that** `docker/metadata-action` produces |

Never use a zero relative immediately before a backticked identifier. That single rule catches most instances.

### 1.2 Every complementizer is present

A bare `so` reads as "therefore" rather than "in order that", and the reader only discovers the intended reading at the end of the clause.

- Reject: `provenance` is disabled so each arch is pushed as a plain image manifest.
- Accept: `provenance` is disabled **so that** each arch is pushed as a plain image manifest.

### 1.3 Every preposition has its object

- Reject: the coordinator answers long before a worker can be scheduled onto.
- Accept: the coordinator answers long before a worker **that queries can be scheduled onto** is available.

Ask what the preposition points at. When the answer is not in the sentence, the sentence is broken and the reader cannot repair it.

### 1.4 Every pronoun binds to the noun you intended

From a comment in a container-build workflow:

> Disable provenance so that each arch is pushed as a plain image manifest rather than a single-entry manifest list, which `docker buildx imagetools create` can then combine.

`which` binds to *a single-entry manifest list*, so the comment states that `imagetools create` can combine single-entry manifest lists. The truth is the opposite, and the opposite is the entire reason provenance is disabled. Rewrite as two sentences.

This failure does not merely slow a reader down; it inverts the meaning. The same check applies to `it`, `one`, and `this`.

### 1.5 Participles are not mistakable for past-tense verbs

A reduced relative clause is fine when it cannot be misread. `a field the encoder stored` fails because `stored` first reads as the main verb. Expand it whenever the participle follows a noun that could be a subject.

### 1.6 No noun stack three deep

`the init-container image the cluster installs plugins from` stacks three noun phrases before the verb arrives.

### 1.7 The contrastive frame appears at most once

The target is the explanatory frame `X rather than Y, since Z`, used to define a thing by what it is not. One instance is a good sentence. Three is a recognizable verbal signature, and it flattens the emphasis so that nothing stands out. Reviewed drafts have used the frame four times in a single description, once per design decision.

An ordinary contrast between two named alternatives does not count against the limit. The test is whether the sentence needs the rejected alternative in order to make sense. When the reader would understand it with `rather than Y` deleted, delete it.

### 1.9 No finite verb that can be read as a plural noun

The mirror of 1.5. There the participle was mistaken for a verb; here a verb is mistaken for a noun. From a test client:

> a worker whose `version` does not match the coordinator's answers health checks but is never scheduled onto

`the coordinator's answers` reads as a noun phrase, so the sentence appears to have no main verb until `health checks` arrives and forces a restart. Repair by ending the possessive before the verb, or by splitting:

> a worker whose `version` differs from the coordinator's still answers health checks. The coordinator never schedules a query onto such a worker.

### 1.10 Reduce the clause instead of repairing it

1.1 says that a relative clause keeps its pronoun. It does not say that a relative clause is the right structure. Reviewers have pointed out that a relative clause often survives only because it was never questioned, where the passive or a prepositional phrase says the same thing in fewer words and with no garden path available at all.

| Longest | Correct but heavy | Best |
| --- | --- | --- |
| the columns `Parser` produces | the columns **that** `Parser` produces | the columns **produced by** `Parser` |
| a value that is computed at startup | — | a value **computed at startup** |
| the warning that the scheduler logs | — | the warning **logged by the scheduler** |
| the entries that the registry holds | — | the entries **held in the registry** |

Two deletions look alike and are not:

- Deleting `that is` or `which is` before a participle or an adjective is **good**. `a file that is generated by the build` becomes `a file generated by the build`, and nothing can be misparsed, because no subject is left behind.
- Deleting `that` before a clause that has its own subject is **bad**, and it is the failure in 1.1. `the columns that `Parser` produces` cannot lose its `that`, because `Parser` then reads as part of the column name.

The boundary is 1.5. Stop reducing as soon as a noun sits between the head noun and the participle, because that noun will be read as a subject: `a field the encoder stored` fails for exactly this reason, while `a field stored under several types` is fine.

---

## Part 2: Claims and evidence

Part 1 asks whether the reader can parse the sentence. This part asks whether it is true. It exists because a bug report was rejected for content rather than prose, in the reviewer's words: it is not a wording problem, the situation that it describes is not necessarily correct. The report named a real symptom, then invented a plausible mechanism for it and asserted an impact that nobody had traced.

### The two rejected sentences

> **Reject.** Each completed task adds another entry to a set that never converges and never sheds, so the warning rate grows monotonically with the number of tasks the scheduler has ever dispatched.
>
> **Accept.** The scheduler completes an assignment by removing it from its in-memory registry, so only the first iteration succeeds.

The mechanism was invented, and one grep for the removal call disproves it (2.1). The growth claim rests on a single sample (2.2). It contradicts the report's own measurement two paragraphs below it (2.4). `never converges` and `never sheds` assert unbounded behaviour that nobody checked (2.6).

> **Reject.** ~3 gRPC round-trips/sec of pure waste against scheduler, storage, and the database, scaling with lifetime task count rather than current load.
>
> **Accept.** Log noise only. Jobs still succeed. No extra RPCs — the polls happen either way — and no storage or database load, since the registry is an in-memory hash map. Measured 3 warnings/sec with 4 idle execution managers, steady.

The unit was swapped from warnings to round trips (2.8). Storage and the database sit nowhere on the call path (2.3). `pure waste` describes traffic that the fix does not remove (2.5). The trend contradicts the attached measurement (2.4).

Both replacements are shorter than the sentences that they replace. Accuracy cost nothing here, because the invented material was the padding.

### 2.8 Every number keeps the unit that it was measured in

A measurement is evidence for exactly the quantity that was counted. Re-labelling it as a different quantity keeps the authority of the number while discarding the thing that earned it, which makes it the most deceptive kind of sentence in a report, because it reads as evidenced.

The report measured 3 warnings per second and published `~3 gRPC round-trips/sec of pure waste`. The figure survived and the noun changed to the expensive one. The defect added no round trip at all, because the long poll sends the same RPC whether the field is stale or not.

Point at each number and say what you counted. When the sentence names anything else, it is false even though the number is true.

### 2.9 A small bug is filed as a small bug

Both rejected sentences inflate a real but minor defect with a mechanism, a growth curve, and a blast radius, because a merely annoying bug feels too small to be worth a reader's time. The effect is the reverse: a reviewer checks the largest claim first, finds that it does not hold, and stops reading.

The corrected report opens with "Log noise only. Jobs still succeed", and is likelier to be fixed for having said so.

---

## Part 3: PR summaries

**Open with the mechanism.** A reviewer who can read the one-line fix and understand the bug immediately, yet cannot tell from the description what it is talking about, has found the failure that this rule prevents.

**Justify nothing by work that has not landed.** "The harness later in the stack covers this" asks a reviewer to trust code that they cannot read, and it goes stale the moment the stack is reordered. Naming a later change as context is fine; leaning on one as proof is not. This applies to soft references too: describing a volume mount that a later change introduces reads as if this change ships a cluster.

**Describe the change, not the review that produced it.** When a review round changes the code, rewrite the affected sentence rather than appending a rebuttal to the reviewer's objection. Arguments belong in the review reply, where the reviewer is looking and where the record already lives. A description that defends an absence — why some validation is *not* there — answers a question that the next reader never asked.

**Cost is not a justification, except when it is the bug.** A log-volume defect that fills a disk makes the measurement the subject, and it belongs in the summary.

**The cost rule is the one that gets over-applied.** Twelve independent passes over twelve PR bodies produced three identical mistakes, all deletions that removed the reason along with the cost. Both shapes are worth recognising.

The first is a change whose subject *is* the cost. From a PR titled "Disable Abseil test builds":

> **Do not cut.** The connector only needs Abseil's libraries and headers. Building Abseil's test targets adds dependency-build work and CI resource usage without affecting the packaged connector artifacts.

Delete the second sentence and the PR states no reason to exist. Its title announces a build-cost change; the cost is the subject, not a justification bolted onto one.

The second is a cost that explains a design choice *inside* the change rather than arguing for the change:

> **Do not cut.** The default is `false` because case-sensitive matching is faster: an exact match is a direct dictionary lookup, whereas a case-insensitive match must case-fold the query and scan the dictionary for all case variants.

A reviewer asking "why is the default `false`?" has no answer once this goes. The test: delete the cost clause and read what remains. If a reason is still standing, the cut was right. If the sentence now asserts a decision with nothing behind it, put it back.

**A test count is the claim most likely to be stale.** One summary read "`mvn test` for the connector's suites: 35 passed" while the suite gave 36 — the branch had moved under a number that was correct when written. Nothing about the sentence looks wrong, which is why it survives review until someone runs it. Re-run counts before submitting; do not carry them forward across a rebase or a review round.

**"Covered by CI" is not a run.** Four PRs in one stack carried "C++ compilation is covered by the CI native build", which names no command, asserts nothing the author did, and cannot be checked from the body. Rule 2.7 already asks for the distinction, so write it: "C++ compilation was not exercised locally; the CI native build covers it." Same information, no borrowed credibility.

**Count before you call something a duplicate.** An editing pass proposed deleting "field names remain case-sensitive" as "the third statement" of that fact. It appeared once. Deleting a fact that is stated once is worse than leaving one stated twice, so grep before you cut, and treat the duplication claim as a claim under Part 2 like any other.

**Length tracks the change, not the diff.** The complaint that produced this rule, from a reviewer reading a run of generated PR bodies: "I'm seeing some PRs that describe every single change at a level of detail that's unnecessary." A generator holding the diff will narrate the diff, because every hunk looks like something to report. The reviewer already has the diff. What they cannot recover from it is the mechanism and where the risk sits, and an inventory of hunks is the one thing a PR body never needs to supply.

**The second reader is the release manager.** From the same complaint: "When we prepare a release, we sometimes need to read the PR description, but if each PR has a description that's 500 words, that's going to be difficult." That reader arrives months later without the branch, looking for one sentence about what shipped. Rule 0.4 already asks for conclusion first; this is who it is for. A body that opens with its conclusion costs them a paragraph, and one that opens with preamble costs them all of it.

---

## Part 4: Bug reports and issues

An issue has a different job from a PR summary. The PR argues that a change is correct; the issue has to make a stranger believe that a defect exists and be able to find it.

**Title the component that contains the defect.** Filing against the service that emits a warning, when the bug is in the client that sends the stale field, sends the next reader to the wrong code.

**Cut evidence that needs interpretation.** A histogram that looks like six concurrent failures but is really one failure sampled over time costs more than it proves. A full JSON envelope repeated three times is not more convincing than one line.

**Length tracks the defect.** One report that failed at 586 words worked at 275.

---

## Part 5: Docstrings and code comments

### A docstring does not name its own subject

A docstring is attached to the thing that it documents, so naming that thing again is pure overhead. It costs the opening words of every doc comment in the codebase, which is where the reader's attention is highest.

| Reject | Accept |
| --- | --- |
| This function returns the number of active workers. | Returns the number of active workers. |
| This file contains the gRPC scheduler client. | The gRPC scheduler client. |
| This struct represents a task assignment. | A task assignment. |
| This method is called when the poll times out. | Called when the poll times out. |
| This test verifies that stale assignments are dropped. | Verifies that stale assignments are dropped. |

The same deletion applies to `This module`, `This class`, `This trait`, and `This macro`. Language conventions already agree: PEP 257 asks for `Return the sum`, and rustdoc summaries conventionally open with a verb or a noun phrase.

`this` is fine when it points at something other than the documented entity, as in `this is safe only because the caller holds the lock`. The rule targets `this <entity-noun>` naming the very thing that the comment sits on.

### The first line says what the thing is, not what it is for

The previous rule says to delete `This class`. This one says what to put in its place. A class, struct, or module is a thing, so its first line is a noun phrase; a function or method is an action, so its first line is the effect. Purpose framing answers a question the reader has not asked yet, and defers the one they have.

| Reject | Accept |
| --- | --- |
| Runs statements against one coordinator. (on a class) | A client that runs statements against one coordinator. |
| Used to hold the connection details until a statement runs. | Stores the connection details. Nothing connects until a statement runs. |
| Helper for bringing the cluster up in tests. | Brings the cluster up and yields a connected client. |
| For converting SQL predicates into KQL. (on a module) | SQL-to-KQL predicate conversion. |

Purpose also ages worse than identity. What a class is stays true while its callers change, so a first line written as "used for X" goes stale the first time someone uses it for Y. PEP 257 asks a class docstring to summarize behaviour and a function docstring to prescribe its effect, and rustdoc summaries follow the same split.

**No past tense about a hazard that is gone.** A review round that removes a trap should remove the note about it, not leave a record. A comment explaining that a construct "would have received this task's own arguments" documents the fix's own history, and a reader who never saw the old code cannot tell whether the hazard is live.

**Give a reason the next reader can check.** When the reason is a tool behaviour rather than a fact about this codebase, say which behaviour, because that is the part nobody can derive from the surrounding lines. `Task resolves a bare {{.VERSION}} from the environment as well as from the command line` earns its place; `named vars are safer` does not.

---

## Part 6: READMEs

Part 1 matters most here. A README's readers are the least equipped to recover from a garden-path sentence, because they cannot check the prose against code that they have not read.

**No defensive notes.** A warning about a stale cached container image was cut from one README because the failure mode had not happened.

**No claims of ease.** "Simply run" tells the reader nothing, and it reads badly when the step then fails for them.

---

## Part 7: What not to add

**Weigh the scaffolding against the change.** One test that genuinely failed before a fix and passed after it was still removed, because its fake service had to implement every RPC in the trait, plus a real server on an ephemeral port, to pin a one-token change.

**Speculative artifacts get cut.** Unit tests over straight-line validation, a README warning about a stale image, and a tracking issue behind a `TODO` were all cut from one session's work as bloat.

---

## Part 8: Analysis reports

Every sentence below is quoted from one generated session report. None of them is wrong: each states a true figure at its correct evidence tier, and each was still rejected for what it costs the reader.

### 8.1 The headings carry the findings

That report's headings read: `Time and cost — your focus`, `The seven categories`, `Extra categories`, `What the logs cannot tell you`. Only the first says anything. A reader skimming the headings learns the shape of the report and none of its content, and the strongest finding in it — that nothing the session claimed to commit reached the repository — is eleven lines inside a section called `The seven categories`.

The heading is the cheapest line in a report to make useful, because a skimmer reads every one of them.

### 8.2 Round a ratio for the reader

| Reject | Accept |
| --- | --- |
| The cache served 28.2671% of the input | The cache served 28.3% of the input |
| 21,198.3 of 21,219.6 end-to-end minutes (99.9%) | 21,198 of 21,220 minutes (99.9%) |

Six significant figures on a ratio reads as a value copied out of a tool rather than a finding someone stands behind, and no decision changes between 28.2671% and 28.3%. The exact value belongs in the figure's reference entry, where a reader who is reproducing it wants every digit.

The rule is narrow on purpose. Rounding is the one arithmetic a report writer may do, because it cannot invent a figure that was not measured; combining two numbers can.

### 8.3 A ratio carries its counts

- Reject: Two tools fail far above the average, at 23.1% and 14.3%.
- Accept: Web search failed 3 of 13 times (23.1%), web page fetch 2 of 14 (14.3%).

The accepted sentence also kills the finding it looked like it had: a 23.1% failure rate sounds alarming until the denominator shows it is three calls. A percentage without its counts hides exactly the cases where the percentage should not have been computed.

### 8.4 The finding comes before its qualification

> Attempt time sums to 133 minutes across 18 attempts against the 38.1 minutes those two workflows were open [derived]; trap: the attempts ran in parallel, so that sum measures fan-out, not duration.

The trap is doing real work here and must stay. What fails is the order inside the paragraph when the trap arrives before the reader has the point, and the version that opens `Trap: a turn's elapsed time is its last entry minus its first` spends its first sentence on a caveat to a claim the reader has not read yet.

### 8.5 The tier and the link sit at the end

| Reject | Accept |
| --- | --- |
| took **18,792,752 input tokens**, 99.4% of them on the workflow agents rather than the main conversation [measured, share derived] [R2](#r2-input-tokens-by-path) | took **18,792,752 input tokens** [measured], 99.4% of them on the workflow agents rather than the main conversation [derived] [R2](#r2-input-tokens-by-path) |

A tier marker mid-clause breaks the sentence where it is still being read. The worst case in that report is a citation with no sentence at all: a table is followed by a line opening `[R25](#r25-errors-per-tool). Trap: the rate counts returned results, not calls`, which asks the reader to parse a link label as a subject.

### 8.6 Two bold openers in a row, not six

One section of that report opened six consecutive paragraphs in bold: **Nearly all the elapsed time is one gap inside one turn.** **The real work is a 96-minute window.** **Two runs are almost the whole bill.** **Nothing was paid for and thrown away.** **The cache served 28.2671% of the input.** **The cheap model did the heavy lifting.**

Each one is a good sentence and rule 0.4 asked for it. Together they flatten the section, because a skimmer's eye is drawn to all six equally and so ranks none of them. The fix is not to delete the sentences; it is to bold the one the reader must not miss.

### 8.7 One fact per cell

> | Reliability | 18 of 18 attempts ended ok [R14](#r14-attempts-by-status); 0 of 656 issued tool calls came back without a result [R15](#r15-tool-calls-with-no-result) | [M] |

Two findings, two citations and a semicolon in a cell whose neighbours hold one short clause. A table's value is that every row is read at the same speed, and one crowded cell costs that for the whole table.

### 8.8 The table ranks its own rows

That report's seven-row category table is followed by a paragraph opening **Outcomes is the row to look at.** The sentence is correct and the table should have said it: seven rows that look identical make the reader read all seven to find out that one of them matters.

### 8.9 Name the blind spots

The rule's positive example is from the same report, which is why the rule exists. Its `What the logs cannot tell you` section states that the records show activity rather than value, that money was never measured, and that a compaction, a truncated read and a refused permission are all unknown rather than absent.

A reader trusts a report that volunteers its limits more than one that reads as complete, and the distinction between unknown and zero is the one a reader will otherwise get wrong.

### 8.10 One notation for the tiers

Two reports written from the same facts file, a day apart, labelled their claims differently, and the first was inconsistent with itself: 51 markers spelled out as `[measured]`, `[derived]`, `[inference]` and `[domain knowledge]` in the prose, and 7 abbreviated to `[M]` and `[D]` in the category table, with `[measured, share derived]` appearing mid-clause twice. The second used `[M]`, `[D]`, `[I]` and `[K]` in all 55 places.

Neither report was wrong, because nothing had ever said which form to use. The second is the better read — fifty spelled-out markers crowd the sentences they qualify — but it is unreadable on first contact without a legend, since nothing in it says what `[K]` means.

The rule exists because the tier is the part of a report a sceptical reader looks for first. A notation that changes between the prose and the table invites them to wonder whether the difference means something.
