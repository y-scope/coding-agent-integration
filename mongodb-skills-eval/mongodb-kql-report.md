# MongoDB KQL Insights Report

**Skill:** `mongodb-kql` (native CLP compress + keyword KQL only, no semantic)
**Input:** `~/clp-demo-mongodb/mongo-subset` (7 files, 1.88 GB) → compressed natively with `--extensions '*' --timestamp-key t.$date` → 9.39 MB archive (200×), **4,986,512 records**.
**Executor note:** Subagent delegation unavailable (`unknown model group: yscope-default-subagent`); the skill's KQL battery was executed inline.

> **Critical caveat — resolved during the run:** the first compression produced an archive with only **683,022 records (file 1 only, 14%)**, not 4.99M. Root cause: `clp-s-compress-folder` wrote the file list **null-delimited** (`find -print0`) but `clp-s -f` expects **newline-delimited**, so only the first file was ingested. Confirmed directly (newline list of 2 files → 1,359,713 records; null list → 683,022) and **fixed** in the wrapper (one null→newline conversion). All numbers below are from the corrected full archive. See the comparison report.

## 1. Summary

- **Total records:** 4,986,512 (search `*` = 4,986,512; decompress-count = 4,986,512 — consistent)
- **Time span:** 2023-03-21T23:34:54 → 2023-03-22T04:47:54 (~5h13m)
- **MongoDB:** 6.0.5, host `hostb22`, port 27017

### Severity (KQL `s:<value>`)
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

## 2. Issues & Warnings

- **Errors/fatal: 0** (`s:E OR s:F`). KQL severity filtering is exact — no false positives (contrast `mongodb-grep`'s 44 free-text "exception" matches).
- **Warnings: 3** (projected `id,msg` for `s:W`): `22120` access-control disabled · `5123300` vm.max_map_count too low · `5578800` legacy wire opcode.

## 3. Performance Signals — logged operations

- **"Slow query" (`id:51803`): 242,792.** Filtered by `id:51803` (not `msg:`, which is a clp-string and returns 0).
- **Duration dist:** n=242,792, min=0, median=0, p95=1ms, max=237ms.
- **By namespace** (`attr.ns`): `ycsb.usertable` 238,013 · `ycsb.$cmd` 4,566 · `admin.$cmd` 185 · `config.system.sessions` 11.
- **By plan** (`attr.planSummary`): `IDHACK` 91,843 · `EOF` 4 (writes have no planSummary).
- **By client** (`attr.remote`): `127.0.0.1:55458` 146,178 · `127.0.0.1:45820` 96,406 · …
- **By command verb** (existence `attr.command.<verb>:*`): insert 146,173 · find 87,278 · update 4,568 · ismaster 183 · dropDatabase 9 · listIndexes 8 · buildinfo 2. (`attr.command` is a nested **object** — projecting it returns null; counted each verb via an existence query on its leaf, per the skill.)
- **Index effectiveness** (`attr.keysExamined,attr.docsExamined,attr.nreturned`): 87,274 at 1:1:1, 4,566 at 1:1:0, 7 at 0:0:0.

## 4. Replication & Elections

- **REPL (`c:REPL`): 150,755.** Top: `Waiting for write concern. OpTime: {replOpTime}, write concern: {writeConcern}` = **150,750**. Elections/stepdowns: **0** (steady-state primary).

## 5. Storage & WiredTiger

- `c:STORAGE OR c:WT OR c:WTWRTLOG OR c:WTCHKPT OR c:WTRECOV OR c:WTTS`: ~3.34M. Dominant: `CUSTOM COMMIT` 1,772,320, `WT begin/commit_transaction` 715k/623k, `WT rollback_transaction` 91,876, `flushed journal` 9,596, `Slow WT transaction …` 3,942.

## 6. Connections & Network

- `c:NETWORK OR c:ACCESS`: 435. `Starting server-side compression negotiation` 181 · `Compression negotiation not requested` 178 · `Connection accepted` 11 · `Terminating session due to error` 6 · `Session from remote encountered a network error during SourceMessage` 6.

## 7. Configuration & Startup

- `id:4615611` "MongoDB starting": pid 29265, port 27017, dbPath `/var/lib/mongodb`, host `hostb22`. Build Info `id:23403`: version 6.0.5.
- Note: projecting the `attr` **object** wholesale returns `{}` (only leaf paths like `attr.version`/`attr.host`/`attr.port` project). The skill prescribes leaf projection; a naive `--projection attr` returns nothing.

## 8. Logtype dictionary

- `stats.logtypes`: **199 distinct templates** (works natively on the Mongo archive — logtypes built from `msg`).

## 9. Top templates (project `msg`, templatize, `uniq -c`)

```
1772320 CUSTOM COMMIT <*>
 715525 WT begin_transaction
 623649 WT commit_transaction
 242792 Slow query
 238223 About to run the command
 150750 Waiting for write concern. OpTime: <*>, write concern: <*>
  91876 WT rollback_transaction
  20945 WiredTiger message
   9596 flushed journal
   3942 Slow WT transaction. Lifetime of SnapshotId <*> was <*>ms
```

## 10. Follow-up KQL queries

1. `clp-s-search-kql --projection t.$date,attr.durationMillis,attr.ns,msg ARCHIVE 'id:51803' | grep '^{' | jq -r 'select((.attr.durationMillis//0)>50)|[…]|@tsv'` — the genuinely-slow tail.
2. `clp-s-search-kql ARCHIVE 'c:WTEVICT'` — WiredTiger eviction (cache pressure).
3. `clp-s-search-kql --projection t.$date,msg,attr.remote ARCHIVE 'c:NETWORK' | grep '^{' | jq -r 'select(.msg|test("error|Terminating";"i"))|[…]|@tsv'` — the 6 network-error sessions.