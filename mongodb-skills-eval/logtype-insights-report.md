# Logtype Insights Report (App-Agnostic, Logtype-Baseline)

**Skill:** `logtype-insights` (schema discovery → `stats.logtypes` baseline → classify → cache → targeted queries)
**Input:** same 7-file / 1.88 GB subset → 9.39 MB archive, 4,986,512 records (after the wrapper null-delimiter fix).
**Executor note:** Subagent delegation unavailable; the two phases (classify, insight) were executed inline. The classification cache round-trip was exercised for real.

## 1. Summary

- **Total records:** 4,986,512; span 2023-03-21T23:34:54 → 2023-03-22T04:47:54 (~5h13m); MongoDB 6.0.5 / hostb22.
- **Severity:** D3 3.68M, D2 720k, I 242k, D4 167k, D5 150k, D1 25k, W 3, E 0, F 0.
- **Top logger/component:** STORAGE (3.22M).

## 2. Logtype Baseline — the spine

- **Distinct templates: 199** (via `stats.logtypes`, reads the dictionary, not every record — instant).
- **Schema discovered** from one sample record: fields `t`, `s`, `c`, `id`, `ctx`, `msg`, `attr` → `timestamp=t.$date`, `severity=s`, `logger=c`, `message=msg`, payload leaves `attr.durationMillis/attr.ns/attr.planSummary/attr.remote`. Time-range flags work (`t.$date` is epoch).

### Top templates by frequency (project `msg`, templatize, `uniq -c`)
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
   1111 Trimmed samples. Num: <*>
   1111 Refreshing tickets. Before: <*> Now: <*>
```

### Discovered category breakdown (generic + app-specific)
- **wt-transactions** (debug noise): CUSTOM COMMIT, WT begin/commit/rollback_transaction — ~3.19M templates.
- **workload / operations**: Slow query (242,792), About to run the command, Setting/Released the Client, Taking ticket — ~1.43M.
- **write-concern** (REPL): Waiting for write concern (150,750).
- **storage**: flushed journal (9,596), Slow WT transaction (3,942), WiredTiger message (20,945).
- **startup**: MongoDB starting, Build Info, Options set by command line, Ran initializers.
- **network**: compression negotiation, Connection accepted, network-error sessions.

## 3. Classification cache (the reusable artifact)

- **app_key** = `2b94a6fde6c40eea0cf0231ca6d31a82abb8bf7319fead9149a595b89608d4f1` (sha256 of the sorted 199-template set).
- **First run: cache MISS** → classified the 199 templates into the taxonomy above, built a query plan, and stored it via `logtype-cache put` → `~/.config/.../logtype-cache/2b94a6fd….json`.
- **Immediate re-get: cache HIT** → the classification was reused verbatim; the classification step would be **skipped on every future run of this same MongoDB 6.0.5 application**. This is the amortization the skill is designed for: the one-time classification cost is paid once and reused across captures.

## 4. Issues & Warnings

- Errors/fatal: 0. Warnings: 3 (access-control disabled, vm.max_map_count too low, legacy wire opcode) — surfaced via `s:W` + projected `id,msg`.

## 5. Performance Signals — grounded, not blind

Every count below is derived from a **real template** in the baseline (no blind `msg:*term*` queries, which would return 0 on the clp-string `msg`):

- **Slow query** (template `Slow query`, id 51803): 242,792; duration 0/0/1/237ms; by ns `ycsb.usertable` 238,013; by plan `IDHACK` 91,843; by client `127.0.0.1:55458` 146,178 + `:45820` 96,406; index effectiveness 1:1:1 (87,274).
- **Slow WT transaction** (template `Slow WT transaction. Lifetime of SnapshotId <*> was <*>ms`): **3,942** — projected `msg` and grepped the template's static text (`grep -c 'Slow WT transaction'`), since `msg:` KQL is dead. This is a template the blind batteries would never think to query.
- **flushed journal**: 9,596.

## 6. Replication

- Template `Waiting for write concern. OpTime: <*>, write concern: <*>` → **150,750** (projected `msg` for `c:REPL`, grepped "write concern"). Elections: 0.

## 7. Semantic Search Coverage

Not used — the baseline (199 templates) was sufficient to ground every query. (Also, `semantic()` is broken in this environment — see `mongodb-semantic` report — so the skill correctly avoids depending on it.)

## 8. Follow-up queries (derived from templates)

1. `clp-s-search-kql --projection t.$date,attr.durationMillis,msg ARCHIVE 'id:51803' | grep '^{' | jq -r 'select((.attr.durationMillis//0)>50)|[…]|@tsv'` — slow tail (the 237ms outlier).
2. `clp-s-search-kql --projection t.$date,msg ARCHIVE 'c:WTEVICT'` — eviction events (cache pressure), a template-driven angle the blind batteries miss.
3. `clp-s-search-kql --projection msg ARCHIVE 'c:REPL' | grep '^{' | jq -r 'select(.msg|test("write concern";"i"))|.msg' | wc -l` — write-concern wait volume over time (run per-time-bucket with `--tge`/`--tle`).