---
name: vllm-insights
description: Analyze structurized vLLM wrapper logs from a CLP archive — or compress a folder of raw vLLM logs first — and produce actionable insights.
---

# vLLM Insights

End-to-end analysis of structurized vLLM wrapper logs with CLP. Use this when
the user wants to understand what a vLLM run did: errors, warnings,
performance signals, startup configuration, worker behavior, downloads, and
other actionable issues.

For a single ad-hoc KQL query, use the `search` skill instead.

## Supported inputs

- A CLP archive directory that was produced with `--structurize` (fields:
  `timestamp`, `logger`, `level`, `worker`, `message`).
- A folder of raw vLLM wrapper text logs. The skill compresses it with
  `--structurize` first.
- Already-structured JSONL/NDJSON vLLM logs (compress with
  `--timestamp-key timestamp` instead of `--structurize`).

## Workflow

1. Determine the input:
   - If the user provided an archive path, use it.
   - If the user provided a folder, compress it:
     ```bash
     ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-compress-folder \
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

3. **Run the insight pass.** Execute a focused KQL sequence and return a
   compact Markdown report.

   Search wrapper:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'KQL_QUERY'
   ```

   Efficiency rules (follow strictly):
   - Use compound KQL instead of many separate queries: `level:WARN AND message:*memory*`
   - Count matches with: `clp-s-search-kql ARCHIVE 'KQL' | grep -c '^{'`
   - Project aggressively. Pass `--project` for each column you need
     (timestamp, level, logger, worker, message). Omit `--project` only when
     you genuinely need the full record.
   - Records are stored in chronological order. If you need the first or last
     timestamp, project timestamp and use `head`/`tail`; do NOT sort.
   - Do NOT use `--tge` or `--tle`. The timestamp field is a string
     (`"YYYY-MM-DD HH:MM:SS,mmm"`), not epoch ms, so time-range flags do not
     work.
   - Use `message:*term*` for substring search. Bare `message:term` matches
     whole tokens only.
   - Add `--ignore-case` when case is uncertain.
   - Use `semantic("query")` for exploratory searches or when exact field
     values are unknown.

   KQL syntax rules:
   - Available fields: `timestamp`, `logger`, `level`, `worker`, `message`
   - String match: `field:value`
   - Substring: `field:*value*` (required for message substring)
   - Prefix: `field:value*`
   - Phrase: `"exact phrase"`
   - Boolean: `A AND B`, `A OR B`, `NOT A`
   - No array fields exist in this archive; all fields are scalar.

   Required query sequence:
   1. Total records: `*`
   2. Level breakdown, one query per level:
      `level:INFO`, `level:DEBUG`, `level:WARN`, `level:WARNING`, `level:ERROR`
   3. Errors / exceptions:
      `level:ERROR OR level:WARN OR level:WARNING OR message:*Exception* OR message:*Traceback*`
   4. Performance signals:
      `message:*ms* OR message:*latency* OR message:*throughput* OR message:*slow* OR message:*took* OR message:*second*`
   5. Configuration / startup:
      `message:*engine* OR message:*model* OR message:*dtype* OR message:*quantization* OR message:*tp* OR message:*pp* OR message:*cuda* OR message:*GPU*`
   6. Worker distribution: project logger and count distinct logger values.
   7. Connectivity / downloads:
      `message:*download* OR message:*ModelExpress* OR message:*HF* OR message:*transport* OR message:*connection* OR message:*timeout*`
   8. Memory / KV cache:
      `message:*memory* OR message:*KV* OR message:*cache* OR message:*OOM* OR message:*allocation*`
   9. Dynamo / Pyxis (if relevant):
      `message:*dynamo* OR message:*pyxis*`

4. Present the results as a Markdown vLLM Insights Report with these sections:
   1. **Summary** — total records, level counts, time span, top logger
   2. **Issues & Warnings** — error count, warning count, top 3 warning
      patterns, any actionable problems
   3. **Performance Signals** — latencies, throughput, slow operations, counts
   4. **Configuration & Startup** — inferred model, dtype, TP/PP, GPU, engine
      args
   5. **Worker & Health Notes** — logger/worker distribution, connectivity,
      download issues
   6. **Top 3 follow-up KQL queries worth running**

5. Offer to drill deeper on a specific finding or decompress for raw
   inspection:

   ```bash
   ~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-decompress \
     /tmp/vllm-archive \
     /tmp/vllm-archive-decompressed
   ```

## Query Starters

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
| Semantic: slow operations | `semantic("slow operations")` |
| Semantic: download failures | `semantic("download or connection failures")` |
| Semantic: GPU memory | `semantic("GPU memory issues")` |

Combine any starter with a user-supplied term using `AND`, for example:
`level:WARN AND message:*memory*`.

## Analysis Patterns

CLP searches the compressed archive — unmatched records are never decompressed.
Push logic into KQL rather than fetching all records and post-filtering in
shell or Python.

**Count matches without fetching full records:**
```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'level:ERROR' | grep -c '^{'
```

**Project only the columns you need:**
```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql \
  --projection timestamp,level,logger,message \
  ARCHIVE 'level:WARN'
```

**Compound KQL — one query instead of several:**
```bash
# Warnings about memory
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'level:WARN AND message:*memory*'
# Dynamo or Pyxis messages
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'message:*dynamo* OR message:*pyxis*'
```

**Important: no time-range flags for structurized vLLM archives**
The `timestamp` field is a string (`"YYYY-MM-DD HH:MM:SS,mmm"`), not epoch ms.
Do not use `--tge` / `--tle`. To obtain the span, project `timestamp` and use
`head -n 1` / `tail -n 1` on the JSONL output.

**Use semantic search for exploratory questions:**
```bash
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("slow operations")'
~/.codex/marketplaces/yscope/plugins/clp/bin/clp-s-search-kql ARCHIVE 'semantic("download failures") AND level:WARN'
```

## Report format

Present results in this order:

1. **Summary** — total records, level counts, archive span, top logger.
2. **Issues & Warnings** — errors, warnings, top repeated messages, anything
   that needs action.
3. **Performance Signals** — latencies, throughput, slow operations, counts.
4. **Configuration & Startup** — inferred model, dtype, TP/PP, GPUs, engine
   args.
5. **Worker & Health Notes** — logger distribution, connectivity, downloads.
6. **Follow-up queries** — 2–3 concrete KQL queries the user can run next.