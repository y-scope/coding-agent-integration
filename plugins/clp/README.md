# YScope CLP Plugin

Plugin for compressing, searching, and decompressing coding-agent session log archives with [CLP](https://github.com/y-scope/clp) (Compressed Log Processor).

CLP is the open-source platform for log archive storage, search, and analytics. Pre-release builds may also include licensed YScope extensions.

## API Surface

The plugin exposes only:

- list recent Claude Code and Codex session JSONL files.
- compress one selected session with `clp-s c --timestamp-key timestamp`.
- compress log files from an arbitrary folder with `clp-s c --remove-path-prefix FOLDER -f FILE_LIST OUTPUT_DIR`.
- search local CLP archives with KQL (including `semantic("query")`) and stdout results.
- dump an archive's log shape dictionary with the `stats.log_shapes` query.
- decompress a local CLP archive directory.

It does not expose full-project compression, reducers, network/file output handlers, results-cache writes, indexing, conversion, remote decompression, metadata sinks, or arbitrary `clp-s` option passthrough.

## Skills

| Skill | Scope |
| --- | --- |
| `compress` | Compress a session JSONL file into a CLP archive directory. |
| `compress-folder` | Compress log files from an arbitrary folder into a CLP archive directory. |
| `search` | Search CLP archives with KQL, including `semantic("query")`. |
| `log-insights` | App-agnostic log analysis driven by the archive's log shape dictionary: dump the templates, classify them (cached), then run targeted queries derived from templates that are known to exist. |
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

`clp-s` projects leaf columns only: a column inside an array (`message.content.text` when `message.content` is an array of blocks) or an object column (`message`) projects nothing, although a filter on it matches. `clp-s-search-kql` therefore rewrites each `--projection` column from the archive's schema tree (`stats.schema_tree`, read from the archive's metadata in milliseconds) with `bin/lib/projection.py`: a column inside an array becomes the array, which comes back whole; an object becomes the leaf columns under it. Each rewrite is explained on stderr as a `projection: ...` line.

Local helpers (not `clp-s` passthroughs — they invoke `clp-s` only through the wrappers above, or not at all):

- `bin/clp-detect-logs` — reads the first 128 KiB of each log file (read-only). JSON (two or more objects parsed) is reported with its structure, its timestamp field and its first records, every string cut to 128 characters; text is reported with its first lines, each cut to 256 characters, and whether they match a bundled `--structurize` format (vLLM). It suggests a `clp-s-compress-folder` command per group of files that need the same settings. `--parser FILE` dry-runs an agent-written parser on the lines read. See [Log Files](#log-files).
- `bin/structurize.py` — converts text logs to structured JSONL: the built-in vLLM formats, or any format through `--parser FILE`, a Python file defining `parse_line(line)`. Used by `clp-s-compress-folder --structurize`; not called directly.
- `bin/clp-s-schema-tree` — lists every field of an archive from its merged schema tree (`stats.schema_tree`), with no sampling: `FIELD path=… type=… records=N` per field, the KQL path first (an array element's fields are queried through the array, so `message.content[].name` is `message.content.name`; `shown=` gives the `[]` form). An object with many children that all look alike, such as data used as keys, is collapsed into one `*` row. With `--log-shapes-file` (a `stats.log_shapes` dump carrying `node_counts`), each text field also gets `values=` and `templates=`, and `TEXT_FIELDS=` lists the text fields, most values first.
- `bin/log-shape-cache` — persistent cache of the `log-insights` classification, with incremental update when an archive grows. See [Log Shape Cache](#log-shape-cache). Its `fields` subcommand writes, per template hash, the fields its values came from, from a dump with `node_counts` and the archive's schema tree.
- `bin/log-shape-insights-bootstrap` — one-command bootstrap for the `log-insights` skill: the schema tree's fields (`FIELD`, `TEXT_FIELDS=`), value distributions for the most common scalar fields from a sample, the fields each template came from (`TEMPLATE_FIELDS_FILE=`), log shape dictionary dump, per-template frequencies from the counts stored in the archive, and the classification-cache probe, summarized as grep-able `KEY=VALUE` lines.
- `bin/log-shape-cluster` (+ `log-shape-cluster.py`) — groups semantically similar log shapes using embeddings from the semantic server so the LLM classifies one representative per cluster. See [Log Shape Cluster](#log-shape-cluster).
- `bin/log-shape-insight-extract` — builds the `log-insights` insight-pass inputs from a classification, joining its template hashes to the texts it streams from the archive's frequencies file: templates grouped by category (top N by frequency, truncated), the core plan (the `core` entries, one per line, high priority first, reporting entries without a valid `match` filter or ranking as `QUERY_PLAN_INVALID=`), the `drill` entries apart (`/tmp/log-shape-drill-plan.txt`), and, with frequencies, the exact records per category with the classifier's priority and why (`/tmp/log-shape-category-totals.json`) and the top templates overall and per category with their counts and categories (`/tmp/log-shape-top-templates.json`). Bounded, so classifications holding huge near-duplicate templates stay fast. It also empties the focus inbox, so a new analysis starts with no focus.
- `bin/log-shape-baseline-plan` — writes the app-agnostic baseline as its own plan (`/tmp/log-shape-baseline-plan.txt`), run by its own pool while the templates are classified: it samples the schema's severity and logger values from the head of the archive, then adds a `count` per common value, a `count` for the residual (everything else), a `then` rule that fetches the records behind a small severity residual, and one scoped semantic entry. It never uses `--unique`, which scans every record.
- `bin/log-shape-query-plan-run` — the query pool. It executes the plan through `clp-s-search-kql`, rendering each entry's `match` filter to KQL with `kql-build`, printing each entry's result as it completes and recording the rendered KQL, count, share of records, status (`ok`, `zero`, `error`, `timeout`, non-selective), elapsed time, peak memory and samples in `/tmp/log-shape-query-results.ndjson`. Searches run concurrently under memory control: the first runs alone, its peak resident memory is measured, and another starts only while free memory can take one more (sampled twice a second; a search is paused and requeued if memory runs short). An entry's `then` rule can add a follow-up query once its result is in, `--retry-failed` retries a failed entry once, and `--jobs N` pins the concurrency. With `--inbox FILE` the pool also takes entries from FILE while it runs, ahead of every plan entry not yet started, and stays open until FILE says `{"close": true}` (or `--inbox-timeout`, default 900 s, passes): the core plan starts while the user is still choosing a focus, and the focus runs next without a second pool counting the same free memory. `--print-table` renders the recorded results as Markdown, focus entries marked.
- `bin/log-shape-focus` — turns the user's answer to "what should this analysis focus on?" into queries: it queues the chosen categories' `drill` entries, plus any entries the agent wrote from the user's own question or context (validated like plan entries), into the pool's inbox, closes it, and records the focus and the user's context verbatim in `/tmp/log-shape-focus.json`. Run once per analysis, even for "everything", since it is what closes the inbox.
- `bin/log-shape-insight-facts` — computes every number of the insight report in code, from both the baseline's and the plan's results (the user's focus and context with the focus queries' results first, then totals, severity and logger breakdowns with check lines, the category table with priorities, its sum and gap, top templates per category, the fetched warnings and errors grouped by message shape, and the archive's time span from its metadata's `timeRange`) into `/tmp/log-shape-insight-facts.md`, so the report writer only puts them into words.
- `bin/log-shape-report-check` — checks a `log-insights` report against its inputs and prints `FLAG` lines for a figure absent from the facts and results table (with the two listed figures it sums to), a share never printed as a share, a count found only beside different wording, a timestamp the inputs lack, and a KQL filter on a field the archive does not have. The skill hands its `FLAG` lines back to the report writer, which fixes the real ones and leaves the ones the facts support.
- `bin/log-shape-report-save` — saves the finished `log-insights` report where the user chose, in one or more formats, each headed with the date it was generated and, when the facts give one, the logs' time span: Markdown, HTML (a self-contained page with light and dark themes), PDF (printed from that page by a headless Chrome, Chromium or Edge found on the machine: `$CLP_PDF_BROWSER`, `PATH`, the macOS app bundles, Playwright's Chromium, or on WSL the Windows browser), and a page fragment the Claude Code skill publishes to claude.ai with the Artifact tool. A folder gets `log-insights-<name>-<YYYYmmdd-HHMM>.<ext>`; an existing file is never overwritten. `--list-formats` says which formats this machine can write, so the skill offers PDF only when it can print one.
- `bin/clp-compress-status` — reports the state (`running`, `done`, `failed`, `died`) and progress of a `clp-s-compress-folder` run from the status file it keeps beside the archive directory; `clp-s-compress-folder` also prints a `[compress]` heartbeat with progress and an estimated time left (`--heartbeat SECONDS`).
- `bin/kql-build` (+ `lib/kql_build.py`) — renders a structured JSON filter (`all`/`any`/`not`, `eq`/`contains`/`prefix`/`exists`/`gt`…, scoped `semantic`) to KQL with every value quoted and escaped and every group parenthesized, so query plans never carry model-written KQL. `kql-build render '<filter>'` prints the KQL; `kql-build check-plan FILE` validates every query_plan entry, and its ranking (`lib/classification.py`): each entry's `category`, `priority` and `stage`, and each taxonomy category's `priority` and `why`. `log-shape-cache merge`/`put` refuse classifications that fail the same check.
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
- `Time range` (with a timestamp key: the earliest and latest timestamp across every record)
- `Archives dir`
- `Selected session`
- `Archive metadata`

Sessions are always compressed with `--structurize-arrays`. A session record keeps its prompts, replies, tool calls and tool output in the `message.content` array; structured, each element's fields become schema-tree columns, so their text is templated into log shapes and each field can be queried and counted. Unstructured, the array is one opaque string. On one 97 MB session this also made the archive smaller (10.6× against 9.1×) and raised the log shape count from 5,292 to 25,524.

Use the printed top-level `Archives dir` for search and decompression. The wrappers resolve the inner `clp-s` archive directory automatically. Metadata in `.yscope-clp-archive.json` maps archive to session file, agent, roots, timestamp key, time range (`timeRange`, from `clp-s --print-archive-stats`, the same as `clp-s-compress-folder` records), SHA-256, compression stats, command, and resolved inner archive.

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

The resulting archive is compatible with `clp-s-search-kql` and `clp-s-decompress`. Use the printed top-level `Archives dir` for search and decompression. Metadata in `.yscope-clp-archive.json` records the input paths and their common directory, extensions, file count, parser, timestamp key, time range (`timeRange`: the earliest and latest timestamp across every record, from `clp-s --print-archive-stats`, when compressed with `--timestamp-key`), compression stats, command, and resolved inner archive.

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

Allowed controls: `--tge`, `--tle`, `--ignore-case`, `--archive-id`, `--with-archive`, `--projection`, `--semantic-endpoint`, `--semantic-top-k`, `--semantic-threshold`, `--embedding-batch-size`.

The path may hold several archives (for example a bundle of logs compressed into one `--output-dir`): each is searched in turn and the results are concatenated. `--count` and `--unique` rows carry `archive_id`, `--limit` caps the total, `--archive-id` picks one archive, and `--with-archive` wraps each record row as `{"archive_id":…,"record":{…}}` so a hit says which archive it came from (the record itself is untouched).

Use single quotes around KQL in shell commands. Numeric comparisons use infix syntax, for example `durationMs >= 30000`.

## Session Bundles

A bundle is one session's logs kept as CLP archives plus a SQLite catalog: `catalog.sqlite` (what exists and how it connects: agents, workflow runs and their resumed instances, retried attempts, failure causes, timing), `archives/` (one clp-s archives dir, one archive per kind of log) and `files/` (what is not a JSON log). The catalog names records by the IDs they carry (`agentId`, `runId`) and stores no offsets, so it is derived and can be rebuilt from the archives. `clp-bundle build` makes one from a Claude Code session: the main log, subagent transcripts, workflow summaries and journals become one archive per kind of log; the catalog records the graph (agents, workflow runs and their resumed instances, retried attempts with a failure cause, timing), the events, and where every source file went; tool results, file snapshots, tasks and workflow scripts are copied into `files/`. Every file of the session is classified by its path, and one that matches no rule stops the build. The build checks that the events plus the records it left out equal the archive's own count of records with a `uuid`, and removes what it made if that or anything else fails. It refuses an existing directory (`--force` replaces an existing bundle only). A session with no subagents or workflows gets a catalog with only its main thread. `clp-bundle` moves between the catalog and the archives:

```bash
./plugins/clp/bin/clp-bundle BUNDLE build --session-id ed54042e-…      # make BUNDLE from a session under ~/.claude (or --session-file PATH.jsonl)
./plugins/clp/bin/clp-bundle BUNDLE show a1b2c3d4         # the catalog's row: status, cause, time, parents, unit, archive and query
./plugins/clp/bin/clp-bundle BUNDLE evidence a1b2c3d4     # its records from the archive, sorted by time (--tail N, --all, --raw)
./plugins/clp/bin/clp-bundle BUNDLE who --at 2026-08-25T17:15          # what was running then (UTC)
./plugins/clp/bin/clp-bundle BUNDLE who --tool-use-id toolu_…          # the node a launch created
./plugins/clp/bin/clp-bundle BUNDLE who --uuid 92b5ed72-…              # a record's event, and the nodes it points at (the agent it is in, what it launched or reports on)
./plugins/clp/bin/clp-bundle BUNDLE events --agent a1b2c3d4 --tool Bash   # thin rows for records: time, tools called, error/interrupt flags, turn; filter by agent, turn, tool, errors, interrupts, type, time
./plugins/clp/bin/clp-bundle BUNDLE record 92b5ed72-…                  # one event's full record, read from its archive by uuid
./plugins/clp/bin/clp-bundle BUNDLE sql "select cause, count(*) from nodes group by 1"   # read-only
```

The catalog's `events` table has one row per user or assistant record and per record that refers to an agent or task (the completion notifications), with the tools called inside it in `event_tools`, and no text; a record is found by its `uuid`. Hook and reminder attachments, system rows, records without a `uuid` and journal rows are counted in the `bundle` table (`events_unlisted_*`, `events_skipped_*`), not listed. That makes main-thread records (which carry no agent) and single tool calls visible to SQL: `select … from nodes n join events e on e.agent_id = n.agent_id …`.

`show` and `evidence` take a node id, an agent id (or a unique prefix of six or more characters), a run id or a task id; `--json` gives `show`, `who` and `sql` as JSON.

## Semantic Search

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive 'semantic("slow database queries")'
```

Semantic search finds log events whose log shape is semantically similar to a natural language query, even when exact keywords differ. Use `semantic("query")` in KQL and combine with regular KQL using `AND`, e.g. `'semantic("errors") AND level:error'`.

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

URLs must be HTTPS, a `localhost`/loopback address, or a `*.yscope.ai` host. The same resolution drives `log-shape-cluster` (see below), so one setting covers both semantic search and log shape clustering.

Other semantic flags: `--semantic-top-k K` (default 5), `--semantic-threshold T` (default 0.3, range 0.0-1.0), and `--embedding-batch-size N` (default auto).

## Decompress

```bash
./plugins/clp/bin/clp-s-decompress \
  /tmp/session-archive \
  /tmp/session-archive-decompressed
```

## Log Insights

`log-insights` analyzes an archive by first dumping its **log shape dictionary** — the complete vocabulary of distinct message templates, with variables replaced by `<*>`:

```bash
# stats.log_shapes (shapes API, clp-core >= 0.13) dumps the dictionary as raw
# {"archive_id","count","id","shape"} lines; the wrapper adds the required
# --experimental automatically. Shape strings encode variables in an
# archive-dependent form (raw placeholder bytes on regular archives,
# %rule.name% TextShape placeholders on clpp archives) — `log-shape-cache
# normalize` detects the encoding per line and renders both to the canonical
# {"log_shape":"...<*>..."} NDJSON.
# The legacy `stats.logtypes` spelling is rejected: shapes-API binaries silently return nothing for it.
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | ./plugins/clp/bin/log-shape-cache normalize > /tmp/log-shapes.ndjson
jq -s 'length' /tmp/log-shapes.ndjson
```

This reads the dictionary rather than every record, so it is cheap regardless of archive size: a run with millions of records typically has tens to a few hundred templates. Every subsequent query is derived from a template that is known to exist, instead of guessing keywords that may not appear at all.

The skill is app-agnostic — it reads the fields (timestamp/severity/logger/message candidates and every text field) from the archive's merged schema tree, so it works on structurized text archives and native-JSON archives alike.

The skill's mechanical preamble is packaged as one command:

```bash
./plugins/clp/bin/log-shape-insights-bootstrap /tmp/archive
```

It samples records for schema discovery, prints per-field value distributions, dumps + normalizes the dictionary, sums the per-template counts that clp-s stored at compression time, probes the classification cache, and prints a grep-able `KEY=VALUE` summary (`LOG_SHAPE_COUNT=`, `FREQS=`, `CACHE_MODE=`, `TO_CLASSIFY=`, `MAX_CHARS=`, output-file paths). The first time it sees an archive it renders the dictionary once (`log-shape-cache ingest`) and stores each template's hash, count, length and first `MAX_CHARS` characters in the cache database; a later run on the same archive (`SHAPES_SOURCE=stored`) reads them back and skips the dump. It first prints an estimate from the archive's size (`ESTIMATE_SECONDS=`: about 1 minute per 300 MiB of archive on a first run, about the sample's time after), then a `[bootstrap]` line as each of its three stages starts and ends, a heartbeat every 30 s while one runs (`--heartbeat`), and the measured `BOOTSTRAP_TIMINGS` at the end. `FREQS=UNAVAILABLE` means an archive was compressed before clp-s stored those counts; recompress it to get frequencies.

Note that `message:term` is an exact match against the whole field value, same as `field:term` on any field, so it correctly returns 0 unless a message equals exactly `term` — the message field being stored as a CLP-string doesn't change that. Exact match is faster, so prefer it whenever you know a field's full value; **wildcard** only for a substring match — `message:"*term*"` works and returns real hits, and message content almost always needs it, since it's free text. `semantic("…")` also searches the log shapes directly and is a good complement to wildcard search for concept-shaped questions.

### Log Shape Cache

Classifying templates into categories and deriving a query plan is the expensive step, and it is a property of the *application*, not the individual capture — the same build emits the same templates every run. `bin/log-shape-cache` persists that classification, keyed by `sha256` of the sorted distinct log shape strings **capped at a character limit** (default 500, `--max-chars` / `$CLP_LOG_SHAPE_MAX_CHARS`) and de-duplicated — the same treatment the templates get before they are embedded, so the key fingerprints the *embedded* vocabulary. The placeholder-rendered form is hashed, so fingerprints are stable across binary generations. (Consequence of the limit: a template whose tail changes beyond it does not change the fingerprint, so it does not register as growth.)

The cache is one SQLite database, `cache.sqlite` in the cache directory. Each entry holds the schema, taxonomy and query plan, and one row per template: its `hash` (sha256 of the full template), its `prefix_hash` (sha256 of the first `--max-chars` characters) and its category. Templates are stored by hash, never by text: some apps log templates of hundreds of KB (CockroachDB's Pebble stats tables average ~184 KB), and an entry that held their text grew to 2.28 GB, which every lookup parsed in full. That entry is 2.2 MB as rows, and looking it up, storing it or merging into it each takes well under a second. The text stays in the archive's own dictionary dump; `log-shape-insight-extract` joins it on the hash. The shared hashing lives in `bin/lib/log_shapes.py`.

The same database stores what each analyzed archive's dictionary holds, in the `archives` and `archive_shapes` tables: per template its hash, its count, its full length and its first `--max-chars` characters, which is all the fingerprint, the prefix hashes and a report's truncated templates read. `ingest` renders a `stats.log_shapes` dump once, in one streaming pass, and stores it; the bootstrap does this the first time it sees an archive. An archive never changes, so a later run on the same archive reads the rows back (`freqs --archive-ids`, `diff --archive-ids`) instead of dumping the dictionary again. On the 357 MiB CockroachDB archive, 11,558 templates totalling 2.4 GB take 14 MB of rows; the first bootstrap takes 63 s and a later one 15 s (the dump and two rendering passes took 190 s before). `diff --archive-ids` answers UPTODATE only: when templates need classifying, their full text is not stored, so it exits 3 and the bootstrap dumps the dictionary. An archive nobody has analyzed for 30 days is dropped on the next `ingest`.

```bash
LC=./plugins/clp/bin/log-shape-cache
# Dump the dictionary and render it to canonical log shape NDJSON in one pipe:
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | "$LC" normalize > /tmp/log-shapes.ndjson
"$LC" count --log-shapes-file /tmp/log-shapes.ndjson   # distinct templates
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | "$LC" freqs                         # {"count":N,"log_shape":...}, most frequent first
"$LC" diff  --log-shapes-file /tmp/log-shapes.ndjson   # UPTODATE | GROWTH | NEW
# Render a dump once and store its archives; then read them back with no dump:
./plugins/clp/bin/clp-s-search-kql /tmp/archive 'stats.log_shapes' 2>/dev/null \
  | "$LC" ingest --log-shapes-out /tmp/log-shapes.ndjson   # ARCHIVE_IDS=, LOG_SHAPE_COUNT=, COUNTS=
"$LC" stored --archive-ids <ID>                      # exit 0 when stored with counts
"$LC" freqs  --archive-ids <ID>                      # {"count","hash","length","log_shape":<prefix>}
"$LC" diff   --archive-ids <ID>                      # UPTODATE, or exit 3 when the full text is needed
"$LC" forget <APP_KEY> --archive-ids <ID>            # delete a classification and/or stored archives
"$LC" list                                          # cached entries + lineage
"$LC" show <APP_KEY>
```

(`key`, `count`, and `diff` also accept a raw `stats.log_shapes` dump directly — lines carrying a `shape` field are rendered on the fly — but store and pass around the normalized form so every tool sees identical strings.)

`diff` prints one tab-separated header line, followed by NDJSON `{"log_shape":"…"}` lines for the templates that still need classifying:

| Header | Meaning |
| --- | --- |
| `UPTODATE\t<app_key>\t<count>` | Fingerprint unchanged — reuse the cached classification, nothing to classify. A template that differs from a cached one only past the character limit takes its category through the shared prefix hash. |
| `GROWTH\t<app_key>\t<base_key>\t<count>\t<new_count>` | Archive grew from `<base_key>` — only the `<new_count>` new templates follow and need classifying. |
| `NEW\t<app_key>\t<count>` | No compatible base — all `<count>` templates follow. |

`<count>` is always the TRUE full template count, not the de-duplicated one. The GROWTH subset test is one indexed query over the prefix hashes, so a change that only affects bytes past the character limit reports UPTODATE rather than GROWTH.

On GROWTH the new classification is merged into the base entry with `merge --base-key BK` (templates by hash, taxonomy and query plan unioned; `grown_from` records the lineage), so a growing archive only ever costs the classification of its newly-added templates. `merge` prints the result without writing anything, so the insight pass reads it at once; `put --key NK` stores it, in one transaction, and can run in the background because readers see either the old entry or the new one.

Every stored classification is ranked: each taxonomy category carries a `priority` (`high`, `medium`, `low`) and a one-line `why`, and each query plan entry a structured `match` filter rather than a KQL string (see `bin/kql-build`), its `category`, a `priority`, and a `stage` — `core` runs on every analysis, `drill` only when the user focuses on its category. `merge` and `put` refuse a classification that fails this check and write nothing. An entry stored before classifications were ranked is never reused: `diff` warns and treats it as absent, the app is classified again, and `put` replaces it.

Files of the old format (one `<APP_KEY>.json` file per entry, with template text) are ignored, and `diff` and `list` say so; they hold no ranking, so delete them.

Cache location: `~/.config/yscope-clp-plugin/log-shape-cache/`, overridable with `$CLP_LOG_SHAPE_CACHE_DIR`, or per-command with `--cache-dir` on the subcommands that read or write the cache (`freqs`, `ingest`, `stored`, `diff`, `get`, `merge`, `put`, `forget`, `list`, `show`). `normalize`, `count`, and `key` only transform/hash the input and do not accept it. `--max-chars` (default 500, or `$CLP_LOG_SHAPE_MAX_CHARS`) is accepted by the subcommands that compute or stamp the fingerprint or cut the stored prefixes (`key`, `ingest`, `stored`, `diff`, `put`); it must match the limit given to `log-shape-cluster`, or embedding and cache fingerprints diverge.

### Log Shape Cluster

Classification cost scales with the number of templates the LLM must label. `bin/log-shape-cluster` shrinks that two ways: it truncates each template to a character limit and de-duplicates the results, so identical prefixes are only embedded once, then embeds the distinct texts through the semantic server's `/v1/embeddings` endpoint and greedily groups them at a cosine-similarity threshold, so the LLM classifies one representative per cluster (by cluster id) and `expand` propagates the category to every member mechanically, writing each member as its hashes — exact, because the LLM never echoes log shape strings. Representatives and members are always the FULL templates; truncation applies only to what is embedded.

Embeddings come from the same already-running server that powers semantic search. Nothing is installed, downloaded, or started locally — the clustering is pure Python standard library, with no third-party dependency.

```bash
LTC=./plugins/clp/bin/log-shape-cluster
"$LTC" cluster --max-chars 500 --input /tmp/log-shapes-to-classify.ndjson
"$LTC" expand --clusters /tmp/log-shape-clusters.json \
  --classification /tmp/log-shape-class.json     # id-based assignments from the LLM
```

`cluster` prints `CLUSTERS=`, `TEMPLATES=` (full count), `EMBEDDED=` (distinct truncated texts actually sent), and `MAX_CHARS=`.

Field rules cut the work further when the archive counts log shapes per field (a `node_counts` dump; see `log-shape-cache fields`). `fields` summarizes each text field, and a rules file gives whole fields one category, such as source code, diffs or agent reasoning:

```bash
"$LTC" fields --template-fields /tmp/log-shape-template-fields.ndjson \
  --freqs-file /tmp/log-shape-freqs.ndjson        # one line per text field
"$LTC" cluster --max-chars 500 --input /tmp/log-shapes-to-classify.ndjson \
  --template-fields /tmp/log-shape-template-fields.ndjson \
  --field-rules /tmp/log-shape-field-rules.json   # {"field_rules": [{"field", "category"}]}
```

A template whose values sit in ruled fields (at least 90% of them; `--rule-share`) takes the category of the ruled field holding most of them and is neither embedded nor clustered (`FIELD_RULED=`); `expand` adds it back by hash and refuses a rule whose category is missing from the classifier's taxonomy (on GROWTH, `--categories-from` names the base classification, whose categories count too, since the classifier lists only the ones it adds). On one 44,818-record Claude Code session, rules on 24 fields assigned 17,205 of 25,483 templates, and the remaining 8,278 formed 1,543 clusters instead of 4,182.

- Endpoint: `--semantic-endpoint`, then `$CLP_SEMANTIC_ENDPOINT`, then the `semantic-endpoint` config file, then the built-in remote endpoint — the same chain as [Semantic search](#semantic-search). The launcher resolves and health-checks it, then passes it down.
- Model contract: `BAAI/bge-base-en-v1.5`, int8[768] — matching what `clp-s` advertises, so both hit the same server-side cache. Threshold: cosine 0.80 (override with `--threshold` or `$CLP_LOG_CLUSTER_THRESHOLD`; raise to 0.85–0.90 to split more, lower to merge more). `--batch-size` sets texts per request (default 256); `--max-request-bytes` (`$CLP_LOG_SHAPE_MAX_REQUEST_BYTES`, default 100 000 000) caps the encoded size of any single request body.
- Truncation: `--max-chars` (`$CLP_LOG_SHAPE_MAX_CHARS`, default 500) caps each template at that many UTF-8 characters before embedding; it must match the value used by `log-shape-cache`, which fingerprints the same truncated set (both use `lib/log_shapes.py`).
- `expand` is stdlib-only and fully offline — it needs no endpoint — and validates that every cluster id is assigned exactly once before writing anything (exit 2 otherwise), which protects the log shape cache from partial classifications.
- Exit codes for `cluster`: 0 ok, 1 input problem, 2 the embedding server is unreachable/rejected, 3 usage error. `--help` and `expand` never touch the network.
- `setup` has been removed; it now exits 2 with a pointer to the endpoint settings. Existing venvs under `~/.config/yscope-clp-plugin/venvs/log-shape-cluster` are no longer used and can be deleted.

## Query Starters

For session-log analysis (which tools fired, what failed, how long a turn took, what context was used), see the per-use-case trajectory skills:

- Claude Code: `claude-code-trajectory` skill (in the installed plugin)
- Codex: `codex-trajectory` skill (in the installed plugin)

For harness/test/patch failures and Docker/resource issues, see the `Trajectory` sections in those skills — both have a "Query Starters" table covering SWE-bench runs, test failures, patch failures, and Docker issues.

For semantic search suggestions, see the `Semantic Search` section in the `search` skill.

For broad trajectory debugging, suggest a subagent when available. Ask it to run the query sequence and return only the archive path, queries, top findings, and next useful queries.
