---
name: vllm-insights
description: Analyze structurized vLLM wrapper logs from a CLP archive — or compress a folder of raw vLLM logs first — and produce actionable insights.
allowed-tools:
  - "Agent"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress:*)"
---

# vLLM Insights

End-to-end analysis of structurized vLLM wrapper logs with CLP. Use this when
the user wants to understand what a vLLM run did: errors, warnings,
performance signals, startup configuration, worker behavior, downloads, and
other actionable issues.

For a single ad-hoc KQL query, use the `search` skill instead.

## Supported inputs

- A CLP archive directory that was produced with `--structurize` (fields:
  `timestamp`, `logger`, `level`, `message`).
- A folder of raw vLLM wrapper text logs. The skill compresses it with
  `--structurize` first.
- Already-structured JSONL/NDJSON vLLM logs (compress with
  `--timestamp-key timestamp` instead of `--structurize`).

## Workflow

1. Determine the input:
   - If the user provided an archive path, use it.
   - If the user provided a folder, compress it:
     ```bash
     "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-folder" \
       --folder /path/to/vllm/logs \
       --structurize
     ```
   - If nothing was provided, ask for a folder or archive path.

2. Report compression stats when you compressed the folder:
   - `Raw input bytes`
   - `Archive bytes`
   - `Compression ratio`
   - `File size reduction`
   - `Input files`
   - `Archives dir`
   - `Archive metadata`

3. **Spawn a subagent for the insight pass.** Use the Agent tool with model
   `haiku` (fall back to `sonnet`). The subagent runs a focused KQL +
   semantic-search sequence and returns only the compact Markdown report —
   keeping the parent context clean.

   Subagent prompt template (fill in `ARCHIVE` and `GOAL`):

   ```
   Analyze this structurized vLLM CLP archive: ARCHIVE

   Search wrapper: /home/robin/coding-agent-integration/plugins/clp/bin/clp-s-search-kql
   Goal: GOAL

   Efficiency rules (follow strictly):
   - Use compound KQL instead of many separate queries: level:WARN AND message:*memory*
   - Count matches with: clp-s-search-kql ARCHIVE 'KQL' | grep -c '^{'
   - Project aggressively. Pass --project for each column you need
     (timestamp, level, logger, message). Omit --project only when you
     genuinely need the full record.
   - Records are stored in chronological order. If you need the first or last
     timestamp, project timestamp and use head/tail; do NOT sort.
   - Do NOT use --tge or --tle. The timestamp field is a string
     ("YYYY-MM-DD HH:MM:SS,mmm"), not epoch ms, so time-range flags do not work.
   - Use message:*term* for substring search. Bare message:term matches whole
     tokens only.
   - Add --ignore-case when case is uncertain.

   KQL syntax rules:
   - Available fields: timestamp, logger, level, message
   - String match: field:value
   - Substring: field:*value*   (required for message substring)
   - Prefix: field:value*
   - Phrase: "exact phrase"
   - Boolean: A AND B, A OR B, NOT A
   - No array fields exist in this archive; all fields are scalar.

   Semantic search rules:
   - Use semantic("natural language query") in KQL to find log events whose
     logtype is semantically similar to the query, even when exact keywords differ.
   - No extra flags needed — the wrapper auto-selects a working endpoint and
     shares a local cache across sessions.
   - Combine semantic() with regular KQL using AND, for example:
     semantic("GPU memory issues") AND level:WARN
   - Use semantic search for EXPLORATORY queries where you don't know the exact
     field values or keywords. It finds conceptually related events that keyword
     search would miss.
   - Use keyword KQL for TARGETED queries where you know the exact field value or
     substring (level:ERROR, message:*OOM*, etc.).
   - When a semantic query returns zero results, try rephrasing or broadening the
     query, or fall back to keyword search.
   - Use --semantic-top-k N (default 5) to control how many nearest logtypes are
     returned. Increase to 8–10 for broader recall; decrease to 2–3 for precision.
   - Use --semantic-threshold T (0.0–1.0, default 0.3) to set the minimum
     similarity floor. Lower values return more results; raise to 0.5+ for
     stricter matching.

   Required query sequence:
   1. Total records: '*'
   2. Level breakdown, one query per level:
      level:INFO, level:DEBUG, level:WARN, level:WARNING, level:ERROR
   3. Errors / exceptions (keyword):
      level:ERROR OR level:WARN OR level:WARNING OR message:*Exception* OR message:*Traceback*
   4. Semantic: error patterns:
      semantic("errors and failures")
   5. Performance signals (keyword):
      message:*ms* OR message:*latency* OR message:*throughput* OR message:*slow* OR message:*took* OR message:*second*
   6. Semantic: performance and latency:
      semantic("slow operations or performance degradation")
   7. Configuration / startup (keyword):
      message:*engine* OR message:*model* OR message:*dtype* OR message:*quantization* OR message:*tp* OR message:*pp* OR message:*cuda* OR message:*GPU*
   8. Semantic: startup and initialization:
      semantic("startup configuration and model initialization")
   9. Worker distribution: project logger and count distinct logger values.
   10. Connectivity / downloads (keyword):
       message:*download* OR message:*ModelExpress* OR message:*HF* OR message:*transport* OR message:*connection* OR message:*timeout*
   11. Semantic: network and connectivity:
       semantic("network connectivity and download issues")
   12. Memory / KV cache (keyword):
       message:*memory* OR message:*KV* OR message:*cache* OR message:*OOM* OR message:*allocation*
   13. Semantic: GPU memory and caching:
       semantic("GPU memory allocation and cache issues")
   14. Dynamo / Pyxis (if relevant):
       message:*dynamo* OR message:*pyxis*
   15. Semantic: request lifecycle:
       semantic("request prefill decode batching")

   After running the keyword + semantic queries above, compare results:
   - If a semantic query found events that the keyword query missed, note them
     in the report as "semantic-only findings".
   - If both found the same events, report only the keyword result.
   - Deduplicate across all queries to avoid double-counting.

   Return ONLY a Markdown vLLM Insights Report with these sections:
   1. Summary — total records, level counts, time span, top logger
   2. Issues & Warnings — error count, warning count, top 3 warning patterns,
      any actionable problems; include semantic-only findings that keyword
      searches missed
   3. Performance Signals — latencies, throughput, slow operations, counts;
      include semantic-only performance findings
   4. Configuration & Startup — inferred model, dtype, TP/PP, GPU, engine args;
      include any startup events found only via semantic search
   5. Worker & Health Notes — logger/worker distribution, connectivity,
      download issues; include semantic-only connectivity findings
   6. Semantic Search Coverage — for each semantic query that found events the
      keyword equivalent missed, list: the semantic query, what it found that
      keywords didn't, and count of semantic-only events
   7. Top 3 follow-up KQL queries worth running (mix keyword and semantic)
   ```

4. Present the subagent's report to the user. Offer to:
   - Drill deeper with another subagent pass on a specific finding.
   - Decompress the archive for raw inspection:
     ```bash
     "${CLAUDE_PLUGIN_ROOT}/bin/clp-s-decompress" \
       /tmp/vllm-archive \
       /tmp/vllm-archive-decompressed
     ```

## When to use semantic search vs keyword KQL

| Situation | Use | Why |
| --- | --- | --- |
| You know the exact field value | `level:ERROR` | Keyword is precise and fast |
| You know a substring | `message:*OOM*` | Wildcard substring is direct |
| Exploring an unfamiliar archive | `semantic("…")` | Finds conceptually related events without knowing keywords |
| Keyword search returned nothing | `semantic("…")` | May find events phrased differently than expected |
| You want breadth of coverage | Both | Combine: run keyword first, then semantic to catch what keywords missed |
| Narrowing by severity | `semantic("…") AND level:WARN` | Semantic finds the concept, KQL narrows by field |

The vLLM insight pass above runs **both** keyword and semantic queries for every
analysis category, then reports what semantic search found that keywords
missed. This maximizes coverage.

## Semantic search flags

These flags only take effect when the KQL query contains `semantic()`. The
wrapper auto-detects semantic queries and configures the endpoint and cache.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--semantic-top-k K` | 5 | Number of nearest logtypes to return. Raise (8–10) for broader recall; lower (2–3) for precision. |
| `--semantic-threshold T` | 0.3 | Minimum similarity floor (0.0–1.0). Raise to 0.5+ for stricter matching. |

The wrapper auto-selects a working semantic endpoint (local first, then
remote) and auto-enables a local embedded cache so repeated queries hit
in-process (~sub-ms). No manual configuration is needed for normal use.

## Query Starters

### Keyword queries

| Goal | KQL |
| --- | --- |
| All records | `*` |
| Errors | `level:ERROR` |
| Warnings | `level:WARN OR level:WARNING` |
| Info messages | `level:INFO` |
| Debug messages | `level:DEBUG` |
| Substring in message | `message:*term*` |
| Phrase in message | `"exact phrase"` |
| Worker by logger | `logger:sflow.task.vllm_worker_N` |
| Any worker logger | `logger:sflow.task.vllm_worker_*` |
| Startup / engine init | `message:*engine* OR message:*Initializing* OR message:*vLLM* OR message:*config*` |
| Model / dtype / parallelism | `message:*model* OR message:*dtype* OR message:*quantization* OR message:*tp* OR message:*pp*` |
| GPU / CUDA | `message:*cuda* OR message:*GPU* OR message:*device*` |
| Memory / KV cache | `message:*memory* OR message:*KV* OR message:*cache* OR message:*OOM*` |
| Performance / latency | `message:*ms* OR message:*latency* OR message:*throughput* OR message:*slow* OR message:*took*` |
| Requests / prefill-decode | `message:*request* OR message:*prefill* OR message:*decode* OR message:*batch* OR message:*sequence*` |
| Connectivity / downloads | `message:*download* OR message:*ModelExpress* OR message:*HF* OR message:*transport* OR message:*connection*` |
| Dynamo / Pyxis | `message:*dynamo* OR message:*pyxis*` |
| Failures / exceptions | `message:*error* OR message:*Error* OR message:*Exception* OR message:*Traceback* OR message:*failed*` |

### Semantic queries

| Goal | KQL |
| --- | --- |
| Slow operations | `semantic("slow operations")` |
| Download failures | `semantic("download or connection failures")` |
| GPU memory issues | `semantic("GPU memory issues")` |
| Errors and failures | `semantic("errors and failures")` |
| Performance degradation | `semantic("performance degradation or bottlenecks")` |
| Startup / initialization | `semantic("startup configuration and model initialization")` |
| Network connectivity | `semantic("network connectivity and download issues")` |
| Request lifecycle | `semantic("request prefill decode batching")` |
| Crashes / fatal exits | `semantic("process crash or fatal error")` |
| Configuration drift | `semantic("unexpected configuration or misconfiguration")` |

### Combined (semantic + keyword)

| Goal | KQL |
| --- | --- |
| Warnings about memory | `semantic("GPU memory issues") AND level:WARN` |
| Errors during startup | `semantic("startup initialization") AND level:ERROR` |
| Slow operations (errors only) | `semantic("slow operations") AND level:ERROR` |
| Download issues (warnings+) | `semantic("download failures") AND (level:WARN OR level:ERROR)` |

Combine any starter with a user-supplied term using `AND`, for example:
`level:WARN AND message:*memory*`.

## Analysis Patterns

CLP searches the compressed archive — unmatched records are never decompressed.
Push logic into KQL rather than fetching all records and post-filtering in
shell or Python.

**For analyses that run 3+ queries, spawn a subagent:**
- Prefer Haiku model (`haiku`); fall back to Sonnet (`sonnet`) if unavailable.
- Brief the subagent with the archive path and the analysis goal.
- Ask it to return only the structured Markdown report and follow-up queries.
- This keeps the parent context lean.

**Count matches without fetching full records:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'level:ERROR' | grep -c '^{'
```

**Project only the columns you need:**
```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" \
  --projection timestamp,level,logger,message \
  ARCHIVE 'level:WARN'
```

**Compound KQL — one query instead of several:**
```bash
# Warnings about memory
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'level:WARN AND message:*memory*'
# Dynamo or Pyxis messages
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'message:*dynamo* OR message:*pyxis*'
```

**Important: no time-range flags for structurized vLLM archives**
The `timestamp` field is a string (`"YYYY-MM-DD HH:MM:SS,mmm"`), not epoch ms.
Do not use `--tge` / `--tle`. To obtain the span, project `timestamp` and use
`head -n 1` / `tail -n 1` on the JSONL output.

**Use semantic search for exploratory questions:**
```bash
# Find events conceptually related to slow operations
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("slow operations")'
# Combine semantic with keyword filters for precision
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" ARCHIVE 'semantic("download failures") AND level:WARN'
# Broaden recall with --semantic-top-k
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --semantic-top-k 8 ARCHIVE 'semantic("GPU memory issues")'
# Tighten precision with --semantic-threshold
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --semantic-threshold 0.5 ARCHIVE 'semantic("errors and failures")'
```

**Run both keyword and semantic queries, then diff the results:**
```bash
# Keyword search for memory issues
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --project timestamp,level,message \
  ARCHIVE 'message:*memory* OR message:*OOM* OR message:*cache*'
# Semantic search for memory issues (may find differently-phrased events)
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --project timestamp,level,message \
  ARCHIVE 'semantic("GPU memory allocation and cache issues")'
# Compare: events found by semantic but not keyword are "semantic-only findings"
```

## Report format

Present subagent results in this order:

1. **Summary** — total records, level counts, archive span, top logger.
2. **Issues & Warnings** — errors, warnings, top repeated messages, anything
   that needs action; include semantic-only findings that keyword searches
   missed.
3. **Performance Signals** — latencies, throughput, slow operations, counts;
   include semantic-only performance findings.
4. **Configuration & Startup** — inferred model, dtype, TP/PP, GPUs, engine
   args; include any startup events found only via semantic search.
5. **Worker & Health Notes** — logger distribution, connectivity, downloads;
   include semantic-only connectivity findings.
6. **Semantic Search Coverage** — for each semantic query that found events the
   keyword equivalent missed, list: the semantic query, what it found that
   keywords didn't, and count of semantic-only events.
7. **Follow-up queries** — 2–3 concrete queries (mix keyword and semantic) the
   user can run next.