# Logtype + Semantic vs Logtype + KQL

**Question:** the logtype method (dump `stats.logtypes` → classify templates → retrieve per category) can retrieve records with either KQL (project `msg` + grep template static text, plus scalar filters) **or** semantic search (`semantic("category description")`). How do the two retrieval modes compare when everything else (baseline, classification, cache) is held constant?

**Setup:** same archive as the other reports — 4,986,512 records, 199-template `stats.logtypes` baseline, classification cached (app_key `2b94a6…`, miss→store→hit). The retrieval step was run two ways:
- **logtype + kql:** one templatize-frequency pass (`--projection msg` → templatize → `uniq -c`) gives exact counts for every template; per-category counts = sum of member templates. Local, deterministic.
- **logtype + semantic:** one `semantic("category description")` query per category (`--semantic-top-k 10`, and a `top-k 30` follow-up), counting returned records and inspecting which templates come back.

Semantic search works in this environment after the wrapper compat fix (see `mongodb-semantic-report.md`).

## 1. Per-category results

| Category | logtype + KQL (exact, sum of member templates) | logtype + semantic (top-k=10) | Verdict |
|---|---|---|---|
| **storage / WiredTiger** | CUSTOM COMMIT 1,772,320 + WT begin 715,525 + WT commit 623,649 + WT rollback 91,876 + WiredTiger message 20,945 + flushed journal 9,596 + Slow WT txn 3,942 ≈ **3,237,853** | **20,962** — only `WiredTiger message` (20,945) + a few; **missed all 3.19M WT-transaction templates** | semantic **catastrophically incomplete** |
| **write-concern / replication** | Waiting for write concern **150,750** | **392,965** — Waiting for write concern 150,750 **+ `About to run the command` 238,223 (wrong) + Slow WT txn 3,942 (wrong)** | semantic **imprecise** (238k false matches) |
| **slow-query / workload** | Slow query **242,792** (+ Using classic engine idhack 87,276 if grouped) | **330,085** — Slow query 242,792 + Using classic engine idhack 87,274 + a few | semantic broader, ~reasonable (groups the plan log) |
| **startup / config** | MongoDB starting 1 + Build Info 1 + Options set by command line 1 = **3** | **55** — `Setting the Client` 238,220, `WiredTiger message` 19, `CUSTOM COMMIT` 4 … **`MongoDB starting` not retrieved** | semantic **missed the actual startup record** |
| **errors / assertions** | User assertion 22 + Completed unstable checkpoint 18 + Assertion while executing command 2 + Internal assertion 2 = **44** | **67** — User assertion 22 + Completed unstable checkpoint 18 + Terminating session 6 + WiredTiger message 5 (wrong) + Slow query 2 (wrong) | semantic found the assertions **but with false matches; missed `Internal assertion`/`Assertion while executing command`** |
| **unstable-checkpoint / recovery** | Completed unstable checkpoint **18** | **174** — WiredTiger message 150 (**wrong**) + Completed unstable checkpoint 18 + Invalidating user cache 1 | semantic **noisy** (150 false matches) |
| **network / connections** | compression negotiation 181+178 + Connection accepted 11 + Connection ended 6 + Terminating session 6 + Session from remote 6 ≈ **388** | **423** — compression negotiation 181+178 + Connection accepted 11 + User assertion 20 (wrong) + session errors 6 each | semantic ~right **but with false matches** |

## 2. Is the incompleteness a `top-k` artifact or an embedding mismatch?

It's an **embedding mismatch**, not a top-k limit. With `--semantic-top-k 30`:
- **storage:** still no `CUSTOM COMMIT` / `WT begin_transaction` / `WT commit_transaction` (the 3.19M records). Instead it pulled in *wrong categories* — `Waiting for write concern` (150,750), `User assertion` (8), `Assertion while executing command` (2). The embeddings of `WT begin_transaction` (abbreviated "WT") and `CUSTOM COMMIT {demangleName_typeid_change}` (cryptic) are not nearest to "WiredTiger transaction commit begin journal checkpoint".
- **startup:** still no `MongoDB starting` (count 1, present in the baseline). It returned `Setting the Client` (238,220), `Using classic engine idhack` (87,276), `WiredTiger message` (96) — templates that share the common words "starting"/"setting" drown out the actual `MongoDB starting` record.

So raising top-k does not recover the missed templates — it only adds more false matches.

## 3. Verdict

**logtype + KQL is the strictly superior retrieval mode; semantic is a conditional classification aid, not a primary retrieval method.**

| Dimension | logtype + KQL | logtype + semantic |
|---|---|---|
| **Precision** | Exact — per-template counts from the templatize-frequency pass; no false matches | Imprecise — returns conceptually-nearest logtypes, pulling in wrong categories (`About to run the command` for write-concern; `WiredTiger message` for errors/startup/unstable-checkpoint; `User assertion` for network) |
| **Coverage** | Complete — captures every template, including the 3.19M WT-transaction records AND the 1-record `MongoDB starting` | **Incomplete** — missed the 3.19M `CUSTOM COMMIT`/`WT begin`/`WT commit` storage templates and the `MongoDB starting` startup record (embedding mismatch, not fixable by top-k) |
| **Determinism** | High (local computation) | Low (depends on the embedding model + endpoint) |
| **Cost** | 1 local O(records) pass (templatize-frequency) + optional per-template project+grep | 1 endpoint round-trip per category (N), each decompressing matching records; local cache unavailable on clp-core 0.12.1 |
| **Low-volume/novel signals** | Found (`User assertion` 22, `Completed unstable checkpoint` 18, `Assertion while executing command` 2, `Internal assertion` 2) — exactly, from the baseline | Found the 22 + 18, but missed `Internal assertion`/`Assertion while executing command` in its top-6 and surrounded them with false matches |

The one place semantic was *reasonable* was the slow-query category, where it grouped `Slow query` with `Using classic engine idhack` (the plan-summary log) — a conceptually-related template. Everywhere else it was either noisier than KQL or materially incomplete.

## 4. Why this validates the `logtype-insights` skill's existing design

The `logtype-insights` skill already prescribes: *"Run `semantic()` ONLY when a template's category is ambiguous or to group similar templates — never as the default. The baseline is small; prefer classifying it directly."* This evaluation is empirical confirmation:

- The **baseline** (`stats.logtypes` + the templatize-frequency pass) is the spine — it gives exact counts for all 199 templates in one local pass, including the high-volume WT-transaction templates and the single-record startup events that semantic retrieval misses entirely.
- **KQL project+grep** (on the message field, scoped by `level:`/`logger:`/`attr.*` scalar filters) is the correct per-template retrieval — exact and complete.
- **Semantic** earns its place only as an **optional aid during classification** — e.g., to vote on an ambiguous template's category or to cluster similar templates (the slow-query↔idhack grouping). As the *primary* retrieval it is worse on every axis (precision, coverage, determinism, cost).

## 5. Recommendation

Keep `logtype-insights` as-is: logtype baseline + KQL project+grep as the default retrieval, semantic conditional/optional for ambiguous-template classification. Do **not** add a "logtype + semantic as primary retrieval" variant — it would be strictly worse (imprecise, incomplete on the dominant WT-transaction templates, endpoint-dependent). If a future embedding model ranks `WT begin_transaction`/`CUSTOM COMMIT`/`MongoDB starting` near their conceptual queries, semantic's coverage gap would shrink — but its precision problem (wrong-category nearest-neighbors) is inherent to nearest-logtype retrieval and would remain.