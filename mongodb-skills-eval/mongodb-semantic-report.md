# MongoDB Semantic Insights Report

**Skill:** `mongodb-semantic` (native CLP compress + KQL **+ semantic search**)
**Input:** same 7-file / 1.88 GB subset → 9.39 MB archive, 4,986,512 records (after the wrapper null-delimiter fix).
**Executor note:** Subagent delegation unavailable this session (`unknown model group: yscope-default-subagent`); battery executed inline. Subagent config since fixed (see comparison report).

## ✅ Headline: semantic search works (after a wrapper compat fix)

The first runs failed every `semantic("…")` query with `Unknown OUTPUT_HANDLER: <archive>`. Root cause: the `clp-s-search-kql` wrapper passed `--semantic-cache-dir` / `--semantic-cache-cold-capacity` by default, but the installed `clp-s` (clp-core **0.12.1**) does **not** support those flags (absent from `clp-s s --help`), causing a positional misparse. **Fix applied to the wrapper:** it now probes `clp-s s --help` for `--semantic-cache-dir` support and skips the cache flags (falling back to remote-only `/v1/similarity`) when unsupported, emitting a warning. After the fix, semantic search works by default:

```
warning: clp-s does not support --semantic-cache-dir; using remote-only semantic search (no local cache)
{"msg":"Slow query"} …  (results returned)
```

The endpoint auto-selected `https://ca-central-1-semantic-cache.yscope.ai` and is reachable. (This is the same toolchain incompatibility the previous vLLM eval's `vllm-insights` hit — §4.4 — now fixed at the wrapper so the skills don't need a per-call `--semantic-cache-dir none` workaround.)

## 1–9. Keyword findings (identical to `mongodb-kql`)

The keyword battery is the same as `mongodb-kql`, so keyword findings match: total 4,986,512; severity D3-dominant, 0 E/F; 3 startup warnings; 242,792 "Slow query" (id 51803, duration 0/0/1/237ms, ycsb.usertable IDHACK + insert + update, 2 main local clients, index effectiveness 1:1:1); 150,750 write-concern waits, 0 elections; ~3.34M storage/WT; 199 templates.

## 10. Semantic Search Coverage — semantic-only findings (the value)

Each `semantic()` query was run with `--semantic-top-k 10`. Semantic search found several issue signals the keyword battery (`s:E OR s:F` = 0, and component-specific queries) **missed**, because they live at D1/D2 severity under the `ASSERT`/`RECOVERY`/`COMMAND` components — not `E`/`F`:

| semantic query | semantic-only finding | count | severity/component | keyword missed? |
|---|---|---|---|---|
| `errors failures exceptions fatal` | **User assertion** | 12 | D1 / ASSERT / id 23074 | ✅ keyword `s:E/F`=0; no `c:ASSERT` query |
| `errors failures exceptions fatal` | **Completed unstable checkpoint.** | 18 | D2 / RECOVERY | ✅ no `c:RECOVERY` query |
| `errors failures exceptions fatal` | Terminating session due to error / Session from remote encountered a network error / Connection ended / Ending session | 6 each | I / NETWORK | ⚠ keyword `c:NETWORK` found these, but not grouped as failures |
| `connection failures network errors authentication denied` | **Assertion while executing command** | 2 | D1 / COMMAND | ✅ keyword `c:COMMAND` was scoped to `id:51803` |
| `connection failures …` | Access control is not enabled for the database | 1 | W / CONTROL | (also found by `s:W`) |
| `index build creation` | Index build: done building; Reconciling collection and index idents; Creating profile collection | 1–2 each | I / INDEX | (also findable via `c:INDEX`) |

**The actionable semantic-only signals:** `User assertion` (12, ASSERT), `Completed unstable checkpoint` (18, RECOVERY), and `Assertion while executing command` (2, COMMAND) — these are exactly the kind of low-severity-but-meaningful events that a blind `s:E OR s:F` keyword filter (which returned 0) hides, and that semantic search surfaces by concept. `c:ASSERT` has 24 records total; the keyword battery never queried it.

Other semantic results confirmed/extended the keyword picture: `semantic("slow query long running operation")` → 242,792 Slow query + lifecycle ("thread awake", "Starting thread"); `semantic("WiredTiger cache eviction…")` → 48 "WiredTiger message"; `semantic("startup…")` → "MongoDB starting" + "Setting the Client" (238k).

## 11. Cost note

Semantic adds one embedding round-trip per `semantic()` query (7 here) on top of the keyword battery. The local in-process cache is unavailable on clp-core 0.12.1 (flag unsupported), so each semantic query hits the remote endpoint. On a clp-core build that supports `--semantic-cache-dir`, repeated semantic queries would hit in-process.

## 12. Follow-up

1. `clp-s-search-kql --projection t.$date,c,msg ARCHIVE 'c:ASSERT'` — pull all 24 ASSERT records semantic surfaced (the "User assertion" / "Assertion while executing command" tail).
2. `clp-s-search-kql --projection t.$date,msg ARCHIVE 'c:RECOVERY'` — the "Completed unstable checkpoint" events with timestamps.
3. `clp-s-search-kql --projection t.$date,msg,attr.remote ARCHIVE 'semantic("connection failures") AND c:NETWORK'` — the 6 network-error sessions with their remote endpoints.