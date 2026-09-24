# YScope CLP Plugin

Plugin for compressing, searching, and decompressing coding-agent session log archives with [CLP](https://github.com/y-scope/clp) (Compressed Log Processor).

CLP is the open-source platform for log archive storage, search, and analytics. Pre-release builds may also include licensed YScope extensions.

## API Surface

The plugin exposes only:

- list recent Claude Code and Codex session JSONL files.
- compress one selected session with `clp-s c --timestamp-key timestamp`.
- compress log files from an arbitrary folder with `clp-s c --remove-path-prefix FOLDER -f FILE_LIST OUTPUT_DIR`.
- search local CLP archives with KQL (including `semantic("query")`) and stdout results.
- dump an archive's logtype dictionary with the `stats.log_shapes` query.
- decompress a local CLP archive directory.

It does not expose full-project compression, reducers, network/file output handlers, results-cache writes, indexing, conversion, remote decompression, metadata sinks, or arbitrary `clp-s` option passthrough.

## Skills

| Skill | Scope |
| --- | --- |
| `compress` | Compress a session JSONL file into a CLP archive directory. |
| `compress-folder` | Compress log files from an arbitrary folder into a CLP archive directory. |
| `search` | Search CLP archives with KQL, including `semantic("query")`. |
| `logtype-insights` | App-agnostic log analysis driven by the archive's logtype dictionary: dump the templates, classify them (cached), then run targeted queries derived from templates that are known to exist. |
| `decompress` | Decompress a CLP archive directory for raw inspection. |
| `claude-code-trajectory` | End-to-end Claude Code session analysis: list → compress → search → decompress, plus Claude-specific query starters. |
| `codex-trajectory` | Same workflow for Codex session logs, plus Codex-specific query starters. |

Future use-cases will add their own skill directories under `skills-claude/` (or `skills-codex/` if the use-case is agent-specific).

## Install

Hosted installer:

```bash
curl -fsSL https://installer.yscope.ai/coding-agent-plugin.sh | bash
```

Local marketplace:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp

claude plugin marketplace add "$PWD" --scope user
claude plugin install clp@yscope --scope user

codex plugin marketplace add "$PWD"
codex plugin add clp@yscope
```

Local plugin session:

```bash
claude --plugin-dir ./plugins/clp
```

## Wrappers

Restricted-passthrough wrappers around `clp-s` — these are the security boundary (flag allowlist, path validation, env hardening):

- `bin/clp-s-list-sessions`
- `bin/clp-s-compress-session`
- `bin/clp-s-compress-folder`
- `bin/clp-s-search-kql`
- `bin/clp-s-decompress`

The wrappers prefer `CLP_S_BIN`, then plugin-local `bin/clp-s`, then plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`.

Local helpers (not `clp-s` passthroughs — they invoke `clp-s` only through the wrappers above, or not at all):

- `bin/clp-detect-logs` — reads the first 128 KiB of each log file (read-only). JSON (two or more objects parsed) is reported with its structure, its timestamp field and its first records, every string cut to 128 characters; text is reported with its first lines, each cut to 256 characters, and whether they match a bundled `--structurize` format (vLLM). It suggests a `clp-s-compress-folder` command per group of files that need the same settings. `--parser FILE` dry-runs an agent-written parser on the lines read. See [Log Files](#log-files).
- `bin/structurize.py` — converts text logs to structured JSONL: the built-in vLLM formats, or any format through `--parser FILE`, a Python file defining `parse_line(line)`. Used by `clp-s-compress-folder --structurize`; not called directly.
- `bin/logtype-cache` — persistent cache of the `logtype-insights` classification, with incremental update when an archive grows. See [Logtype Cache](#logtype-cache).
- `bin/logtype-insights-bootstrap` — one-command bootstrap for the `logtype-insights` skill: schema-discovery sample, per-field value distributions, logtype dictionary dump, per-template frequencies from the counts stored in the archive, and the classification-cache probe, summarized as grep-able `KEY=VALUE` lines.
- `bin/logtype-cluster` (+ `logtype-cluster.py`) — groups semantically similar logtypes using embeddings from the semantic server so the LLM classifies one representative per cluster. See [Logtype Cluster](#logtype-cluster).
- `bin/logtype-insight-extract` — builds the `logtype-insights` insight-pass inputs from a cached classification: templates grouped by category (top N by frequency, truncated), the query plan (one entry per line, reporting entries without a valid `match` filter as `QUERY_PLAN_INVALID=`), and, with frequencies, the exact records per category (`/tmp/logtype-category-totals.json`) and the top templates overall and per category with their counts and categories (`/tmp/logtype-top-templates.json`). Bounded, so classifications holding huge near-duplicate templates stay fast.
- `bin/logtype-baseline-plan` — adds the app-agnostic baseline to a query plan: it samples the schema's severity and logger values from the head of the archive, then appends a `count` per common value, a `count` for the residual (everything else), a `then` rule that fetches the records behind a small severity residual, and one scoped semantic entry. It never uses `--unique`, which scans every record.
- `bin/logtype-query-plan-run` — the query pool. It executes the plan through `clp-s-search-kql`, rendering each entry's `match` filter to KQL with `kql-build`, printing each entry's result as it completes and recording the rendered KQL, count, share of records, status (`ok`, `zero`, `error`, `timeout`, non-selective), elapsed time, peak memory and samples in `/tmp/logtype-query-results.ndjson`. Searches run concurrently under memory control: the first runs alone, its peak resident memory is measured, and another starts only while free memory can take one more (sampled twice a second; a search is paused and requeued if memory runs short). An entry's `then` rule can add a follow-up query once its result is in, `--retry-failed` retries a failed entry once, and `--jobs N` pins the concurrency. `--print-table` renders the recorded results as Markdown.
- `bin/logtype-insight-facts` — computes every number of the insight report in code (totals, severity and logger breakdowns with check lines, the category table with its sum and gap, top templates per category, the fetched warnings and errors grouped by message shape) into `/tmp/logtype-insight-facts.md`, so the report writer only puts them into words.
- `bin/logtype-report-check` — checks a `logtype-insights` report against its inputs and prints `FLAG` lines for a figure absent from the facts and results table (with the two listed figures it sums to), a share never printed as a share, a count found only beside different wording, a timestamp the inputs lack, and a KQL filter on a field the archive does not have. The skill hands its `FLAG` lines to a haiku verifier subagent, which dismisses the ones the facts support and checks the claims in words that a script cannot.
- `bin/clp-compress-status` — reports the state (`running`, `done`, `failed`, `died`) and progress of a `clp-s-compress-folder` run from the status file it keeps beside the archive directory; `clp-s-compress-folder` also prints a `[compress]` heartbeat with progress and an estimated time left (`--heartbeat SECONDS`).
- `bin/kql-build` (+ `lib/kql_build.py`) — renders a structured JSON filter (`all`/`any`/`not`, `eq`/`contains`/`prefix`/`exists`/`gt`…, scoped `semantic`) to KQL with every value quoted and escaped and every group parenthesized, so query plans never carry model-written KQL. `kql-build render '<filter>'` prints the KQL; `kql-build check-plan FILE` validates every query_plan entry. `logtype-cache put`/`put-merged`/`set-plan` refuse plans that fail the same check.
- `bin/kql-validate-wildcards` — the search wrapper's guard against an unquoted wildcard value with a space (`field:*a b*`, which clp-s reads as natural language); rejects the query before it runs.

## Session Workflow

List recent sessions:

```bash
./plugins/clp/bin/clp-s-list-sessions
```

Defaults:

- sources: Claude `~/.claude/projects` and Codex `${CODEX_HOME:-~/.codex}/sessions`.
- limit: latest 5, sorted by session file mtime descending.
- Claude subagents: excluded unless `--include-subagents`.
- manifest: written to `/tmp`.

When presenting choices, always include `IDX`, `AGENT`, modified timestamp, raw bytes, human size, session name, project/cwd, and session ID.

Check archive root:

```bash
./plugins/clp/bin/clp-s-compress-session --show-archives-root
```

Default archive root is `${TMPDIR:-/tmp}/yscope-clp-archives`. Use it without asking. Ask only when the user wants persistent storage or a different root.

Compress the selected row:

```bash
./plugins/clp/bin/clp-s-compress-session \
  --selection-file /tmp/clp-s-session-selection-...tsv \
  --session-index 1 \
  --timestamp-key timestamp
```

After compression, report these lines:

- `Raw input bytes`
- `Archive bytes`
- `Compression ratio`
- `File size reduction`
- `Archives dir`
- `Selected session`
- `Archive metadata`

Use the printed top-level `Archives dir` for search and decompression. The wrappers resolve the inner `clp-s` archive directory automatically. Metadata in `.yscope-clp-archive.json` maps archive to session file, agent, roots, timestamp key, SHA-256, compression stats, command, and resolved inner archive.

## Log Files

Compressing log files is two steps with a decision between them. First, see what each file holds (read-only; it reads the first 128 KiB of each file, so it is instant even on many-GB logs):

```bash
./plugins/clp/bin/clp-detect-logs /var/log/myapp /data/vllm_worker_3.log
```

It says whether each path is a file or a folder. Per file, if two or more JSON objects parse from the start, it is JSON: the report lists every field path with its type, the field holding a timestamp in every record and the kind of value, and the first records with every string cut to 128 characters (still valid JSON). Otherwise it is text: the report shows the first 20 lines, each cut to 256 characters, and says whether they match a bundled format that `--structurize` converts on its own (vLLM). For text without one, the agent works out the structure from those lines and writes a parser (below). The report ends with a `SUGGEST` command per group of files that need the same settings and `SKIP` lines for files that can't go in as they are. Then compress with the flags chosen from that report:

```bash
./plugins/clp/bin/clp-s-compress-folder --path /var/log/myapp --timestamp-key ts
./plugins/clp/bin/clp-s-compress-folder --path /data/vllm_worker_3.log --structurize
```

`--path` takes a log file or a folder and can be repeated; the files found in all of them go into one list, which `clp-s c -f` compresses into one archive.

Defaults:

- extensions (for folders): `log,jsonl,json,txt,ndjson,out,err` (override with `--extensions`, or use `--extensions '*'` to include every regular file). A file named with `--path` is always included.
- jsonl detection: on. A file whose first lines are JSON objects but whose name is not `.json`/`.jsonl`/`.ndjson` is handed to `clp-s` as a staged `*.jsonl` copy, because `clp-s` picks its parser by file name.
- recursive: yes (use `--no-recursive` for the top level of each folder only).
- structurize: off (pass `--structurize` for text logs — see below).
- timestamp key: none (pass the field `clp-detect-logs` reports as `--timestamp-key KEY`; required for time-range search).
- archive root: `${TMPDIR:-/tmp}/yscope-clp-archives` (override per-run with `--archives-root DIR`). Ask only when the user wants persistent storage or a different root.
- `--dry-run` is read-only: it prints the plan and converts, stages and compresses nothing.

### Text logs

`clp-s` only ingests JSON, so text logs are converted first. `--structurize` runs each text file through `bin/structurize.py`, producing JSONL with `timestamp/logger/level/message` and setting `--timestamp-key timestamp` automatically. A file that is already JSON is compressed as it is, never converted:

```bash
./plugins/clp/bin/clp-s-compress-folder --path /var/log/vllm --structurize
```

`structurize.py` knows the vLLM formats (sflow-wrapped `vllm_worker` lines and raw `vllm serve` output). For any other text format, write a Python file that defines `parse_line(line)`: it returns a dict with at least `timestamp` (ISO 8601 or epoch) and `message` for a line that starts a record, or `None` for a line that continues the previous one. Test it on the lines the detector reads, then compress with it:

```bash
./plugins/clp/bin/clp-detect-logs --parser /tmp/myapp_parser.py /var/log/myapp/app.log
./plugins/clp/bin/clp-s-compress-folder --path /var/log/myapp/app.log --structurize --parser /tmp/myapp_parser.py
```

Each converted file prints `[structurize] <file>: N records`; a file that can't be converted is skipped with a warning that gives the reason.

After compression, report:

- `Raw input bytes`
- `Archive bytes`
- `Compression ratio`
- `File size reduction`
- `Input files`
- `Archives dir`
- `Archive metadata`

The resulting archive is compatible with `clp-s-search-kql` and `clp-s-decompress`. Use the printed top-level `Archives dir` for search and decompression. Metadata in `.yscope-clp-archive.json` records the input paths and their common directory, extensions, file count, parser, compression stats, command, and resolved inner archive.

Useful commands:

```bash
./plugins/clp/bin/clp-s-compress-folder --show-archives-root
./plugins/clp/bin/clp-s-compress-folder --set-archives-root ~/clp-archives
./plugins/clp/bin/clp-s-compress-folder --path /var/log/myapp --dry-run
./plugins/clp/bin/clp-s-compress-folder --path ./logs --extensions log,txt
```

## Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'level:error'
```

Allowed controls: `--tge`, `--tle`, `--ignore-case`, `--archive-id`, `--projection`, `--semantic-endpoint`, `--semantic-top-k`, `--semantic-threshold`, `--embedding-batch-size`.

Use single quotes around KQL in shell commands. Numeric comparisons use infix syntax, for example `durationMs >= 30000`.

## Semantic Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'semantic("slow database queries")'
```

Semantic search finds log events whose logtype is semantically similar to a natural language query, even when exact keywords differ. Use `semantic("query")` in KQL and combine with regular KQL using `AND`, e.g. `'semantic("errors") AND level:error'`.

Semantic search requires an embedding server that is **already running**. The plugin never starts one — no Docker container is spun up and no embedding model is downloaded locally. The wrapper health-checks the endpoint before running a semantic search; if it is unavailable, the search fails with a clear error.

Endpoint resolution, highest precedence first:

1. `--semantic-endpoint URL` (inline)
2. `CLP_SEMANTIC_ENDPOINT`
3. the `semantic-endpoint` config file — `~/.config/yscope-clp-plugin/semantic-endpoint`, one URL per line, blank lines and `#comments` ignored (override the path with `CLP_SEMANTIC_ENDPOINT_FILE`)
4. the built-in remote endpoint — `https://ca-central-semantic-cache.yscope.ai`, used if it passes the health check

An endpoint named by 1–3 that fails its health check is a hard error: the wrapper will not silently fall back to a different host, since that would send log text somewhere the user did not choose. Only the built-in defaults in 4 are probed and skipped on failure. A server you host yourself (including one on `localhost`) must be named explicitly; it is not auto-detected.

```bash
# Pin an endpoint once, for every wrapper and session:
echo 'https://embeddings.internal.example.com' \
  > ~/.config/yscope-clp-plugin/semantic-endpoint
```

URLs must be HTTPS, a `localhost`/loopback address, or a `*.yscope.ai` host. The same resolution drives `logtype-cluster` (see below), so one setting covers both semantic search and logtype clustering.

Other semantic flags: `--semantic-top-k K` (default 5), `--semantic-threshold T` (default 0.3, range 0.0-1.0), and `--embedding-batch-size N` (default auto).

## Decompress

```bash
./plugins/clp/bin/clp-s-decompress \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

## Logtype Insights

`logtype-insights` analyzes an archive by first dumping its **logtype dictionary** — the complete vocabulary of distinct message templates, with variables replaced by `<*>`:

```bash
# stats.log_shapes (shapes API, clp-core >= 0.13) dumps the dictionary as raw
# {"archive_id","count","id","shape"} lines; the wrapper adds the required
# --experimental automatically. Shape strings encode variables in an
# archive-dependent form (raw placeholder bytes on regular archives,
# %rule.name% TextShape placeholders on clpp archives) — `logtype-cache
# normalize` detects the encoding per line and renders both to the canonical
# {"logtype":"...<*>..."} NDJSON.
# The wrapper prints archive-metadata header lines to stdout, so filter to
# JSON records with grep '^{' first. The legacy `stats.logtypes` spelling is
# rejected: shapes-API binaries silently return nothing for it.
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | ./plugins/clp/bin/logtype-cache normalize > /tmp/logtypes.ndjson
jq -s 'length' /tmp/logtypes.ndjson
```

This reads the dictionary rather than every record, so it is cheap regardless of archive size: a run with millions of records typically has tens to a few hundred templates. Every subsequent query is derived from a template that is known to exist, instead of guessing keywords that may not appear at all.

The skill is app-agnostic — it discovers the schema (timestamp/severity/logger/ message field names) from a sample record, so it works on structurized text archives and native-JSON archives alike.

The skill's mechanical preamble is packaged as one command:

```bash
./plugins/clp/bin/logtype-insights-bootstrap /tmp/archive
```

It samples records for schema discovery, prints per-field value distributions, dumps + normalizes the dictionary, sums the per-template counts that clp-s stored at compression time, probes the classification cache, and prints a grep-able `KEY=VALUE` summary (`LOGTYPE_COUNT=`, `FREQS=`, `CACHE_MODE=`, `TO_CLASSIFY=`, `MAX_CHARS=`, output-file paths). `FREQS=UNAVAILABLE` means an archive was compressed before clp-s stored those counts; recompress it to get frequencies.

Note that `message:term` is an exact match against the whole field value, same as `field:term` on any field, so it correctly returns 0 unless a message equals exactly `term` — the message field being stored as a CLP-string doesn't change that. Exact match is faster, so prefer it whenever you know a field's full value; **wildcard** only for a substring match — `message:"*term*"` works and returns real hits, and message content almost always needs it, since it's free text. `semantic("…")` also searches the logtypes directly and is a good complement to wildcard search for concept-shaped questions.

### Logtype Cache

Classifying templates into categories and deriving a query plan is the expensive step, and it is a property of the *application*, not the individual capture — the same build emits the same templates every run. `bin/logtype-cache` persists that classification, keyed by `sha256` of the sorted distinct logtype strings **capped at a character limit** (default 500, `--max-chars` / `$CLP_LOGTYPE_MAX_CHARS`) and de-duplicated — the same treatment the templates get before they are embedded, so the key fingerprints the *embedded* vocabulary. The placeholder-rendered form is hashed, so fingerprints are stable across binary generations. Stored `templates[].logtype` are always the full, byte-exact strings; the character limit affects only the fingerprint and the embedding request. (Consequence: a template whose tail changes beyond the limit does not change the fingerprint, so it does not register as growth.)

```bash
LC=./plugins/clp/bin/logtype-cache
# Dump the dictionary and render it to canonical logtype NDJSON in one pipe:
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | "$LC" normalize > /tmp/logtypes.ndjson
"$LC" count --logtypes-file /tmp/logtypes.ndjson   # distinct templates
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | "$LC" freqs                         # {"count":N,"logtype":...}, most frequent first
"$LC" diff  --logtypes-file /tmp/logtypes.ndjson   # UPTODATE | GROWTH | NEW
"$LC" list                                          # cached entries + lineage
"$LC" show <APP_KEY>
```

(`key`, `count`, and `diff` also accept a raw `stats.log_shapes` dump directly — lines carrying a `shape` field are rendered on the fly — but store and pass around the normalized form so every tool sees identical strings.)

`diff` prints one tab-separated header line, followed by NDJSON `{"logtype":"…"}` lines for the templates that still need classifying:

| Header | Meaning |
| --- | --- |
| `UPTODATE\t<app_key>\t<count>` | Fingerprint unchanged — reuse the cached classification, nothing to classify. A full template that differs only past the character limit is appended with the category of the truncated form it shares, so the entry stays complete. |
| `GROWTH\t<app_key>\t<base_key>\t<count>\t<new_count>` | Archive grew from `<base_key>` — only the `<new_count>` new templates follow and need classifying. |
| `NEW\t<app_key>\t<count>` | No compatible base — all `<count>` templates follow. |

`<count>` is always the TRUE full template count, not the de-duplicated one. The GROWTH subset test is performed on the truncated template sets, so a change that only affects bytes past the character limit reports UPTODATE rather than GROWTH.

On GROWTH the new classification is merged into the base entry with `put-merged --base-key BK --key NK` (templates, taxonomy, and query plan are unioned; `grown_from` records the lineage), so a growing archive only ever costs the classification of its newly-added templates.

Every stored query plan entry carries a structured `match` filter rather than a KQL string (see `bin/kql-build`). `put`, `put-merged`, and `set-plan` refuse an entry without a valid `match` and store nothing. `set-plan --key K` replaces only entry K's query plan, leaving its templates and taxonomy untouched; the `logtype-insights` skill uses it once to repair a plan cached before plans used `match`.

Cache location: `~/.config/yscope-clp-plugin/logtype-cache/`, overridable with `$CLP_LOGTYPE_CACHE_DIR`, or per-command with `--cache-dir` on the subcommands that read or write the cache (`diff`, `get`, `put`, `put-merged`, `set-plan`, `list`, `show`). `normalize`, `count`, and `key` only transform/hash the input and do not accept it. `--max-chars` (default 500, or `$CLP_LOGTYPE_MAX_CHARS`) is accepted by the subcommands that compute or stamp the fingerprint (`key`, `diff`, `put`, `put-merged`); it must match the limit given to `logtype-cluster`, or embedding and cache fingerprints diverge.

### Logtype Cluster

Classification cost scales with the number of templates the LLM must label. `bin/logtype-cluster` shrinks that two ways: it truncates each template to a character limit and de-duplicates the results, so identical prefixes are only embedded once, then embeds the distinct texts through the semantic server's `/v1/embeddings` endpoint and greedily groups them at a cosine-similarity threshold, so the LLM classifies one representative per cluster (by cluster id) and `expand` propagates the category to every member mechanically — byte-exact, because the LLM never echoes logtype strings. Representatives and members are always the FULL templates; truncation applies only to what is embedded.

Embeddings come from the same already-running server that powers semantic search. Nothing is installed, downloaded, or started locally — the clustering is pure Python standard library, with no third-party dependency.

```bash
LTC=./plugins/clp/bin/logtype-cluster
"$LTC" cluster --max-chars 500 --input /tmp/logtypes-to-classify.ndjson
"$LTC" expand --clusters /tmp/logtype-clusters.json \
  --classification /tmp/logtype-class.json     # id-based assignments from the LLM
```

`cluster` prints `CLUSTERS=`, `TEMPLATES=` (full count), `EMBEDDED=` (distinct truncated texts actually sent), and `MAX_CHARS=`.

- Endpoint: `--semantic-endpoint`, then `$CLP_SEMANTIC_ENDPOINT`, then the `semantic-endpoint` config file, then the built-in remote endpoint — the same chain as [Semantic search](#semantic-search). The launcher resolves and health-checks it, then passes it down.
- Model contract: `BAAI/bge-base-en-v1.5`, int8[768] — matching what `clp-s` advertises, so both hit the same server-side cache. Threshold: cosine 0.80 (override with `--threshold` or `$CLP_LOG_CLUSTER_THRESHOLD`; raise to 0.85–0.90 to split more, lower to merge more). `--batch-size` sets texts per request (default 256); `--max-request-bytes` (`$CLP_LOGTYPE_MAX_REQUEST_BYTES`, default 100 000 000) caps the encoded size of any single request body.
- Truncation: `--max-chars` (`$CLP_LOGTYPE_MAX_CHARS`, default 500) caps each template at that many UTF-8 characters before embedding; it must match the value used by `logtype-cache`, which fingerprints the same truncated set.
- `expand` is stdlib-only and fully offline — it needs no endpoint — and validates that every cluster id is assigned exactly once before writing anything (exit 2 otherwise), which protects the logtype cache from partial classifications.
- Exit codes for `cluster`: 0 ok, 1 input problem, 2 the embedding server is unreachable/rejected, 3 usage error. `--help` and `expand` never touch the network.
- `setup` has been removed; it now exits 2 with a pointer to the endpoint settings. Existing venvs under `~/.config/yscope-clp-plugin/venvs/logtype-cluster` are no longer used and can be deleted.

## Query Starters

For session-log analysis (which tools fired, what failed, how long a turn took, what context was used), see the per-use-case trajectory skills:

- Claude Code: `claude-code-trajectory` skill (in the installed plugin)
- Codex: `codex-trajectory` skill (in the installed plugin)

For harness/test/patch failures and Docker/resource issues, see the `Trajectory` sections in those skills — both have a "Query Starters" table covering SWE-bench runs, test failures, patch failures, and Docker issues.

For semantic search suggestions, see the `Semantic Search` section in the `search` skill.

For broad trajectory debugging, suggest a subagent when available. Ask it to run the query sequence and return only the archive path, queries, top findings, and next useful queries.
