# Comparative Review: Four MongoDB Insight Skills

A side-by-side evaluation of **`mongodb-grep`**, **`mongodb-kql`**, **`mongodb-semantic`**, and **`logtype-insights`**, each run against the **same input**: a 7-file, 1.88 GB subset of `~/clp-demo-mongodb` (4,986,512 `mongod` records, ~5h13m, a YCSB benchmark on a single MongoDB 6.0.5 primary). The four per-skill reports live alongside this one in `mongodb-skills-eval/`.

Mirrors the previous vLLM four-skill evaluation, now on MongoDB logs with the logtype skill generalized and caching-enabled.

## What changed in this revision (vs the first review)

Three issues raised after the first review were addressed:
1. **No token count → subagent model fixed.** The first review ran inline because every Agent spawn failed with `unknown model group: yscope-default-subagent`. Root cause: `CLAUDE_CODE_SUBAGENT_MODEL=yscope-default-subagent` in `/home/robin/.ccs/yscope-default.settings.json`, but only `yscope-default-{haiku,opus,sonnet}` are valid model groups. **Fixed** the settings file → `yscope-default-haiku`. The current session's env is frozen at launch, so full subagent-run token totals still require a session restart to take effect; this revision measures subagent **input-prompt** token costs directly via the API instead (§5).
2. **Repo updated.** Merged `origin/main` (commit `5b95220` — clp+ skills, `dev` skill, `references/`, `--experimental`/`--clp-s-bin` wrapper options). No conflicts with local work.
3. **Semantic search fixed.** It was broken (`Unknown OUTPUT_HANDLER`). Root cause: the wrapper passed `--semantic-cache-dir`/`--semantic-cache-cold-capacity` that clp-core 0.12.1 doesn't support. **Fixed** the wrapper to probe `clp-s s --help` and skip those flags when unsupported. Semantic now works and finds signals the keyword battery missed (§3.2, §4).

## Method & caveats

1. **Subagent delegation** was unavailable this session (env frozen). All four skills' prescribed query batteries were executed **inline** (codex-style); the subagent config is fixed for the next session. Token cost is therefore reported as **subagent input-prompt tokens** measured via the API (§5), not full subagent-run totals.
2. **Scale:** the full corpus is 65 GB / 186M records (vs 57 MB in the vLLM eval). Skills ran on a representative 7-file / 5M-record subset; full-corpus ground truth gathered earlier (186M records, 9.25M "Slow query", 5.2M REPL, 0 E/F) is consistent with the subset.
3. **A wrapper bug was found and fixed mid-run** (§3.1). CLP-skill numbers are from the corrected, full-data archive.

## 1. Verdict (up front)

| Skill | Setup | Coverage | Finding quality | Determinism | Cost | Status |
|---|---|---|---|---|---|---|
| `logtype-insights` | CLP compress + baseline + cache | 100% (after fix) | **Best** — 199-template spine, grounded queries, reusable cached classification | High (semantic optional, unused) | ~1 dump + handful of targeted queries | ✅ Works; cache amortizes |
| `mongodb-semantic` | CLP compress + KQL + semantic | 100% (after fixes) | **Good + semantic-only signals** — finds D1/D2 assertions/checkpoints keyword misses | Medium (endpoint; cache unavailable on 0.12.1) | keyword + 7 semantic round-trips | ✅ Works (after wrapper compat fix) |
| `mongodb-kql` | CLP compress (keyword only) | 100% (after fix) | Good — exact, matches grep; precise severity | High (no network) | compress + ~20 cheap queries | ✅ Works (after wrapper fix) |
| `mongodb-grep` | none (raw jq/grep) | 100% (all files, always) | Good — but 44 false-positive "exception" hits | High (no network) | ~13 jq passes over 1.88 GB (slowest) | ✅ Works; only zero-setup option |

**Headline (revised):** `logtype-insights` remains the strongest (grounded, waste-free, reusable cached classification). `mongodb-semantic` is no longer broken — after the wrapper compat fix it works and is the **only** skill that surfaced the D1/D2 `User assertion` (12), `Completed unstable checkpoint` (18), and `Assertion while executing command` (2) signals that `s:E OR s:F` (=0) hides. `mongodb-kql` is the reliable keyword workhorse. `mongodb-grep` is the zero-setup but slower/noisier option.

## 2. The four skills at a glance (by design)

| Dimension | `mongodb-grep` | `mongodb-kql` | `mongodb-semantic` | `logtype-insights` |
|---|---|---|---|---|
| Tooling | `jq`/`grep` on raw JSON | CLP compress + keyword KQL | CLP compress + KQL + `semantic()` | CLP compress + `stats.logtypes` baseline + classify + cache |
| `msg` field | plain text (no limit) | clp-string (`msg:term`→0; project+grep) | clp-string; semantic reaches it | clp-string; baseline reads it directly |
| Hard constraint | no CLP | no semantic | none | semantic optional (not used here) |
| Reusable artifact | none | none | none | **cached classification** (per-app) |

## 3. Critical findings & fixes

### 3.1 `clp-s-compress-folder` silently compressed only the FIRST file of a multi-file folder — found & fixed
- **Symptom:** compressing the 7-file/1.88 GB subset produced a 1.16 MB archive with **683,022 records** — exactly file 1 (14% of the data). Files 2–7 silently dropped (1/260 = 0.4% of the full corpus).
- **Root cause:** the wrapper wrote the file list **null-delimited** (`find -print0`), but `clp-s -f` expects **newline-delimited** → clp-s read only the first null-terminated entry. **Proof:** newline list of 2 files → 1,359,713 records; null list of the same 2 files → 683,022.
- **Why the vLLM eval missed it:** it compressed a single 57 MB file.
- **Fix applied:** one null→newline conversion in `clp-s-compress-folder` (after the discovery/structurize paths). After the fix, the same subset compresses to 9.39 MB with all **4,986,512 records** (verified by decompress-count and the last file's 04:47 timestamp). The `--structurize` path shares the bug and is fixed by the same conversion. This affected all CLP skills (incl. the vLLM CLP skills).

### 3.2 `mongodb-semantic` was broken by a wrapper/clp-s flag incompatibility — fixed
- **Symptom:** every `semantic("…")` query failed with `Unknown OUTPUT_HANDLER: <archive>`.
- **Root cause:** the wrapper passed `--semantic-cache-dir`/`--semantic-cache-cold-capacity` by default; clp-core **0.12.1** does **not** support those flags (absent from `clp-s s --help`), causing a positional misparse. (Same class of bug the vLLM eval's `vllm-insights` hit, §4.4.)
- **Fix applied to `clp-s-search-kql`:** probe `clp-s s --help` for `--semantic-cache-dir`; if unsupported, skip the cache flags (remote-only `/v1/similarity`) with a warning. Semantic now works by default on 0.12.1 and keeps the local cache on newer binaries that support it.
- **Result:** semantic search works and produced **semantic-only findings** (§4) — `User assertion` (12, ASSERT), `Completed unstable checkpoint` (18, RECOVERY), `Assertion while executing command` (2, COMMAND), all at D1/D2 severity invisible to `s:E OR s:F`.

### 3.3 "Slow query" ≠ slow (debug-verbosity semantics) — corrected in the skills
`msg:"Slow query"` (id 51803) is emitted for **every** operation (242,792 here) at debug COMMAND verbosity; `attr.durationMillis` is 0–3ms (p95=1) with one 237ms outlier. The skills were corrected to filter by `id:51803` and report the workload (verb / `attr.ns` / `attr.planSummary` / `attr.remote` / index effectiveness), not just a >100ms threshold. `logtype-insights` needs no such fix — it discovers the `Slow query` template from the baseline.

### 3.4 `msg` clp-string; `attr.*` leaves searchable; `attr` object is not
`msg:term`/`msg:*term*` → 0 (project+grep). Nested leaves are KQL-searchable (`attr.ns:ycsb.usertable`→238,013, `attr.planSummary:IDHACK`→91,843, `attr.remote:*`). Projecting the `attr` **object** returns `{}` — must project leaves. Verb counted via existence `attr.command.<verb>:*` (the object can't be projected).

### 3.5 `logtype-insights` classification cache works on real data
5M-record archive: schema discovery → `stats.logtypes` = **199 templates** → `app_key = sha256(sorted templates)` → **cache MISS** → classified + stored → **immediate re-get = HIT**. The one-time classification is reusable across future captures of the same MongoDB 6.0.5 app. This realizes the amortization the vLLM `logtypes` skill only claimed.

### 3.6 Subagent model config — fixed (takes effect next session)
`CLAUDE_CODE_SUBAGENT_MODEL` was `yscope-default-subagent` (unmapped). Changed to `yscope-default-haiku` in `/home/robin/.ccs/yscope-default.settings.json`. The current session's env is frozen at launch, so subagent spawning still fails here; a session restart activates it. (Note: this gateway routes `yscope-default-haiku` → `nemotron-3-nano:30b-cloud`, so future subagent-run token totals reflect Nemotron-30B, not the vLLM eval's Haiku — not directly comparable.)

### 3.7 Repo updated
Merged `origin/main` (`5b95220`: clp+ skills, `dev` skill, `references/`, `--experimental`/`--clp-s-bin` wrapper options, `--projection` repeatable, `--project` removed). No conflicts with the local MongoDB-skill work or the wrapper fixes above.

## 4. Finding-by-finding coverage (same 4,986,512-record subset)

| Finding (ground truth) | grep | kql | semantic | logtype |
|---|---|---|---|---|
| Total 4,986,512 | ✅ | ✅ (after fix) | ✅ | ✅ |
| Severity (D3-dominant, 0 E/F) | ✅ | ✅ | ✅ | ✅ |
| Errors/fatals = 0 | ✅ | ✅ exact | ✅ | ✅ |
| Free-text "exception" matches | ⚠ 44 false positives | — | — | — |
| Warnings (3 startupWarnings) | ✅ | ✅ | ✅ | ✅ |
| **User assertion (12, ASSERT, D1)** | ❌ | ❌ | ✅ **semantic-only** | ❌ |
| **Completed unstable checkpoint (18, RECOVERY, D2)** | ❌ | ❌ | ✅ **semantic-only** | ❌ |
| **Assertion while executing command (2, COMMAND, D1)** | ❌ | ❌ | ✅ **semantic-only** | ❌ |
| "Slow query" = every op (debug verbosity) | ✅ | ✅ | ✅ | ✅ (template) |
| Slow-query duration dist + workload | ✅ | ✅ | ✅ | ✅ |
| Write-concern waits 150,750 | ✅ | ✅ | ✅ | ✅ (template) |
| Elections = 0 | ✅ | ✅ | ✅ | ✅ |
| 199-template dictionary + spine | ⚠ templatize only | ⚠ templatize only | ⚠ templatize only | ✅ **`stats.logtypes`** |
| Classification cache (reusable) | ❌ | ❌ | ❌ | ✅ **proven** |

**Coverage (revised):** `logtype` best (template spine + cache); `semantic` now adds genuine value (the only skill surfacing D1/D2 assertions/checkpoints); `kql` and `grep` are strong on the structured signals but blind to those low-severity conceptual events. `grep` uniquely mis-fires on free-text exceptions (44 false positives vs exact `s:E/F`=0).

## 5. Token cost (subagent input-prompt tokens, measured via the API)

Full subagent-run totals (à la the vLLM eval's 62k–150k across tool rounds) require the subagent fix to take effect after a session restart. What's measurable now is the **subagent input-prompt token cost** — the prompt each skill's Haiku subagent receives — measured by sending each skill's prompt template (with the real data the subagent would be pasted) to `/v1/messages` (`max_tokens=1`) and reading `usage.input_tokens`:

| Skill | Subagent input-prompt tokens | Notes |
|---|---|---|
| `mongodb-grep` | ~3,551 | fixed query-sequence prompt |
| `mongodb-kql` | ~3,570 | fixed query-sequence prompt |
| `mongodb-semantic` | ~2,877 | fixed prompt (extraction slightly shorter) |
| `logtype-insights` — classify phase | ~7,445 | includes the **199-template baseline** pasted in |
| `logtype-insights` — insight phase | ~1,060 | taxonomy + query plan pasted in |

**Reading:** the three blind-battery skills have comparable ~3k input-prompt cost. `logtype-insights` splits into a one-time **classify** pass (~7.4k, includes the 199-template baseline) and a recurring **insight** pass (~1.1k). On a **cache hit** (same app, run ≥2), the classify pass is skipped → recurring input-prompt cost drops to ~1.1k — the cheapest, mirroring the vLLM eval's amortization finding. These are input-prompt only; the full run adds output + tool-round I/O (the vLLM eval's totals were ~20–40× the prompt size due to tool rounds), so treat these as relative prompt-cost, not full-run totals.

## 6. Cost / determinism

| | grep | kql | semantic | logtype |
|---|---|---|---|---|
| Compress | none | ~1–2 min → 9.4 MB (200×) | same | same |
| Query cost | ~13 `jq` passes over 1.88 GB (slowest) | ~20 queries on 9.4 MB (fast) | same + 7 semantic endpoint round-trips | 1 `stats.logtypes` + ~5 targeted (fastest per analysis) |
| Network | none | none | required (remote-only on 0.12.1) | none |
| Determinism | high | high | medium | high |
| Reusable artifact | none | none | none | **cached classification** |

## 7. Skill deficiencies & fixes

1. **`clp-s-compress-folder` null-delimiter bug** (§3.1) — **fixed**. Shared wrapper bug affecting all CLP skills.
2. **`clp-s-search-kql` semantic cache-flag incompat** (§3.2) — **fixed** (probe + skip on unsupported binaries).
3. **`mongodb-grep` free-text exception grep over-counts** (44 payload false positives). Fix: lead with `s:E OR s:F` and treat the free-text grep as secondary.
4. **`attr` object projection returns `{}`** — always project `attr.<leaf>` paths.
5. **`--count` not exposed by the wrapper** — counting needs `grep -c '^{'` over dumped records (fine for small sets, expensive for whole-archive distributions on huge archives).
6. **`logtype-insights` cache key is exact-match** on the template set — a shorter capture (subset of templates) misses; future: key on the logger set + incrementally classify new templates.
7. **Subagent config** (§3.6) — **fixed** in settings; takes effect next session.

## 8. Recommendations by use case

| Situation | Use | Why |
|---|---|---|
| Deepest grounded report; CLP available | **`logtype-insights`** | 199-template spine, no blind queries, reusable cached classification, no endpoint dependency |
| Surface low-severity assertions/checkpoints `s:E/F` hides | **`mongodb-semantic`** | the only skill that found the D1/D2 ASSERT/RECOVERY signals; needs the (now-fixed) endpoint |
| Fast deterministic keyword pass | **`mongodb-kql`** | exact counts, precise severity, no network |
| No CLP, quick raw read | **`mongodb-grep`** | zero setup; mind free-text false positives + multi-GB jq cost |
| Repeatedly analyzing the same MongoDB app | **`logtype-insights`** (cache) | one-time classification amortizes away |

## 9. One-line summary

> On a 5M-record / 1.88 GB MongoDB (YCSB) subset, `logtype-insights` gave the deepest grounded report (199-template baseline + reusable cached classification, proven miss→store→hit); `mongodb-semantic` — after a wrapper fix that stops `clp-s-search-kql` passing `--semantic-cache-dir` to clp-core 0.12.1 (which doesn't support it) — now works and was the **only** skill to surface the D1/D2 `User assertion` (12), `Completed unstable checkpoint` (18), and `Assertion while executing command` (2) signals hidden by `s:E OR s:F`=0; `mongodb-kql` was the reliable keyword workhorse; and `mongodb-grep` was the zero-setup but slower, noisier option (44 free-text false positives). The review also found and **fixed** a critical shared-wrapper bug (`clp-s-compress-folder` passed a null-delimited file list to `clp-s -f`, silently archiving only the first file of any multi-file folder — 14% here, 0.4% of the full corpus — masked in the vLLM eval by its single-file input), **fixed** the subagent model config (was an unmapped `yscope-default-subagent` group), and **updated** the repo to `origin/main` (`5b95220`). Subagent input-prompt token costs were measured (~3k for grep/kql/semantic; ~7.4k one-time + ~1.1k recurring for logtype, dropping to ~1.1k on a cache hit); full subagent-run totals need a session restart to activate the subagent fix.