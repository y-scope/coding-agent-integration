# MongoDB Grep Insights Report

**Skill:** `mongodb-grep` (raw `jq`/`grep`, no CLP)
**Input:** `~/clp-demo-mongodb/mongo-subset` — 7 rotated `mongod` files (every ~40 min across the run), 1.88 GB raw.
**Executor note:** Subagent delegation was unavailable in this environment (`unknown model group: yscope-default-subagent`), so the skill's prescribed `jq`/`grep` query battery was executed inline (codex-style). This report reflects the skill's query design, not subagent overhead.

## 1. Summary

- **Total records:** 4,986,512 (JSON lines with `t` and `s`)
- **Time span:** 2023-03-21T23:34:54 → 2023-03-22T04:47:54 (~5h13m)
- **MongoDB:** 6.0.5, host `hostb22`, port 27017, dbPath `/var/lib/mongodb`, pid 29265
- **Workload:** YCSB benchmark against `ycsb.usertable` (point `find` by `_id` + `insert` + `update`), single primary, clients on `127.0.0.1`.

### Severity
| s | count |
|---|---|
| D3 | 3,680,404 |
| D2 | 720,329 |
| I | 242,871 |
| D4 | 167,182 |
| D5 | 150,741 |
| D1 | 24,982 |
| W | 3 |
| E | 0 |
| F | 0 |

### Components (top)
STORAGE 3,219,273 · `-` 628,348 · COMMAND 476,504 · QUERY 330,056 · WRITE 159,921 · REPL 150,755 · WTWRTLOG 19,434 · WTEVICT 1,169 · NETWORK 433 · WTCHKPT 310 · FTDC 122 · INDEX 52 · ASSERT 24 · CONTROL 9 · ACCESS 2.

## 2. Issues & Warnings

- **Errors/fatal: 0** (`s:E`/`s:F`). No exceptions, assertions, or tracebacks in any record.
- **Warnings: 3** (all `startupWarnings` at boot):
  - `22120` Access control is not enabled for the database — data/config access unrestricted.
  - `5123300` vm.max_map_count is too low (current 65530, recommended ≥102400, maxConns 51200).
  - `5578800` Legacy wire-protocol op code used — client driver may need an upgrade.
- **Free-text exception grep caveat:** `grep -rhE 'Exception|Assertion|Fatal|abort|Traceback'` matched **44 lines**, but these are **false positives** — the words appear inside `attr` payloads (e.g. `RegisterErrorExtraInfoFor…` in the startup `Ran initializers` record) and in `ASSERT` debug records, not in any `s:E`/`s:F` record. The structured `s:E OR s:F` count (0) is the truth. This is grep's characteristic blind spot on JSON logs: free-text matches hit payload text, not severity.

## 3. Performance Signals — logged operations

- **"Slow query" (id 51803): 242,792 records.** At debug COMMAND verbosity every operation is logged as "Slow query"; `attr.durationMillis` is the real duration.
- **Duration distribution:** n=242,792, min=0, median=0, p95=1ms, **max=237ms** (one genuine outlier).
- **By command verb:** insert 146,173 · find 87,278 · update 4,568 · `q` (internal) 4,569 · ismaster 183 · dropDatabase 9 · listIndexes 8 · buildinfo 2.
- **By namespace:** `ycsb.usertable` 238,013 · `ycsb.$cmd` 4,566 · `admin.$cmd` 185 · `config.system.sessions` 11.
- **By plan:** `none` 150,945 (writes) · `IDHACK` 91,843 (the finds) · `EOF` 4.
- **By client:** `127.0.0.1:55458` 146,176 (inserter) · `127.0.0.1:45820` 96,406 (finder) · a handful of short-lived conns.
- **Index effectiveness** (`keysExamined:docsExamined:nreturned`): 87,274 at **1:1:1** (perfect IDHACK point lookups), 4,566 at 1:1:0 (updates), 4 at 0:0:0. Indexing is optimal.

## 4. Replication & Elections

- **REPL records: 150,755.** Top message: `Waiting for write concern. OpTime: {replOpTime}, write concern: {writeConcern}` — **150,750** (99.997% of REPL).
- **Elections/stepdowns/rollbacks: 0.** This is a steady-state primary under heavy write load; the REPL signal is write-concern latency, not elections.

## 5. Storage & WiredTiger

- **STORAGE/WT records: ~3.34M.** Dominant templates: `CUSTOM COMMIT {demangleName_typeid_change}` 1,772,320 · `WT begin_transaction` 715,525 · `WT commit_transaction` 623,649 · `WT rollback_transaction` 91,876 · `flushed journal` 9,596 · `Slow WT transaction. Lifetime of SnapshotId {snapshotId} was {transactionTime}ms` 3,942 · `Trimmed samples` 1,111.
- The `WTEVICT` component (1,169) and `Slow WT transaction` (3,942) are the operationally interesting storage signals; the rest is high-volume transaction debug noise.

## 6. Connections & Network

- **NETWORK records: 433.** `Starting server-side compression negotiation` 181 · `Compression negotiation not requested by client` 178 · `Connection accepted` 11 · `client metadata` 11 · `Terminating session due to error` 6 · `Session from remote encountered a network error during SourceMessage` 6. The 6 "network error during SourceMessage" events are worth a glance but are on transient connections.

## 7. Configuration & Startup

- `id:4615611` "MongoDB starting": pid 29265, port 27017, dbPath `/var/lib/mongodb`, host `hostb22`, 64-bit.
- `id:23403` "Build Info": version **6.0.5**, gitVersion c9a99c12…, OpenSSL 1.1.1, allocator tcmalloc, distmod ubuntu1804.
- No auth, no TLS enforcement (per startup warnings).

## 8. Top repeated messages (logtype-equivalent)

```
1772320 CUSTOM COMMIT <*>
 715525 WT begin_transaction
 623649 WT commit_transaction
 242792 Slow query
 238223 About to run the command
 238220 Setting the Client
 238215 Released the Client
 238207 Received interrupt request for unknown op
 150773 Taking ticket.
 150750 Waiting for write concern. OpTime: <*>, write concern: <*>
 150741 Set last op to system time
  91876 WT rollback_transaction
  87276 Using classic engine idhack
  20945 WiredTiger message
   9596 flushed journal
   3942 Slow WT transaction. Lifetime of SnapshotId <*> was <*>ms
```

## 9. Follow-up commands

1. `find $F -type f -print0 | xargs -0 jq -r 'select(.id==51803 and (.attr.durationMillis//0)>50) | [.t."$date",.attr.durationMillis,.attr.ns,.attr.command|to_entries[0].key]|@tsv'` — the genuinely-slow tail (only the 237ms outlier is >50ms here).
2. `find $F -type f -print0 | xargs -0 jq -r 'select(.c=="WTEVICT") | [.t."$date",.msg]|@tsv'` — WiredTiger eviction events (potential cache pressure).
3. `find $F -type f -print0 | xargs -0 jq -r 'select(.c=="NETWORK" and (.msg|test("error|Terminating";"i"))) | [.t."$date",.msg,.attr.remote] | @tsv'` — the 6 network-error sessions with their remote endpoints.