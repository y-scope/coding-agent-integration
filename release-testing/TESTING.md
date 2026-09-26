# Release Testing: CLP Plugin Walkthrough (vLLM logs)

A step-by-step, copy-paste walkthrough of the CLP coding-agent plugin for someone who has **never used CLP** and doesn't know what it is. Every command shows its expected output so you can tell immediately whether something is wrong. Total time: ~10 minutes.

**What is CLP?** CLP (Compressed Log Processor) compresses log files into a small archive that you can *search without decompressing*. This plugin wraps CLP for coding agents (Claude Code / Codex): compress logs, search them with KQL queries, ask natural-language ("semantic") questions, and analyze an archive by its "log_shapes" — the distinct message templates the application emits.

**The test data** is three real vLLM server logs in `sample-logs/vllm/` (a failed smoke run, a successful one, and a bug-fix run — ~38 KB of plain text). vLLM is an LLM inference server; you don't need to know anything about it. The logs look like this:

```
INFO 06-15 03:52:34 [importing.py:81] Triton not installed or not compatible; ...
(APIServer pid=14116) INFO 06-15 03:52:36 [api_utils.py:339] ...
```

## Prerequisites

- `bash`, `jq`, `python3`
- `clp-s` — the CLP binary, **clp-core 0.13+ (shapes API)**. Check with:

  ```bash
  ./plugins/clp/bin/clp-s --help >/dev/null 2>&1 && echo OK || echo MISSING
  ```

If `MISSING`: install CLP (the plugin's hosted installer ships the binary, or download a clp-core release), or point the wrappers at an existing binary once per shell with `export CLP_S_BIN=/path/to/clp-s`. Note the check only confirms the binary exists — it cannot read the version. If your build is older than 0.13, Steps 1–4, 7 and 8 still work; Step 5 will fail with `no log shape entries found` (see Troubleshooting — your archive stays valid, only the binary needs updating).

All commands are run **from the repository root**. Outputs go to `release-testing/workdir/` (gitignored — safe to delete at any time).

```bash
rm -rf release-testing/workdir   # start clean so every expected output matches
mkdir -p release-testing/workdir
B=./plugins/clp/bin      # the plugin's command wrappers
```

> **Keep one shell open for the whole walkthrough.** Later steps reuse variables defined earlier (`B`, `ARCHIVE`, `LC`, `KEY`, `A2`, `K2`, `CLP_LOG_SHAPE_CACHE_DIR`). If you lose your shell, re-run the Setup block above and the `ARCHIVE=` line in Step 1, then continue where you left off.

One output convention used throughout: the search wrapper prints its human-readable header lines (archive metadata, the underlying command) to **stderr**, so stdout carries only the JSON results and pipes straight into `jq`. The `2>/dev/null` below hides those headers and clp-s's own log lines.

## Step 1 — Compress the logs into an archive

First ask the detector what these files hold. It reads only the first 128 KiB of each and writes nothing:

```bash
"$B/clp-detect-logs" release-testing/sample-logs/vllm
```

Expected — the lines to check (paths are printed absolute):

```
Input: /.../release-testing/sample-logs/vllm is a folder, 3 matching file(s)
...
== macos-m1-smoke-failure-2026-06-15.log (10.0 KiB; read all, 62 line(s))
format:     text: 55 of 62 lines match the bundled vllm-raw format; --structurize converts it to timestamp, logger, level, message
...
SUMMARY files=3 text=3
SUGGEST clp-s-compress-folder --path /.../release-testing/sample-logs/vllm --structurize
```

These are plain-text vLLM logs, so the suggestion is `--structurize`: it parses each line into `timestamp / logger / level / message` fields first, which is what makes the archive searchable by field.

```bash
"$B/clp-s-compress-folder" \
  --path release-testing/sample-logs/vllm \
  --structurize \
  --archives-root release-testing/workdir/archives
```

Expected output — the run prints ~20 lines (source folder, flags, the underlying `clp-s` command, metadata paths); the ones to check are these (sizes may vary by a few bytes, and paths are printed absolute):

```
[structurize] macos-m1-smoke-failure-2026-06-15.log: 56 records in 0s
[structurize] macos-m1-smoke-openmp-hang-fix-2026-06-26.log: 97 records in 0s
[structurize] macos-m1-smoke-success-2026-06-30.log: 97 records in 0s
Structurize: converted 3 file(s) to structured JSONL
...
Raw input bytes: 54989
Archive bytes: 7392
Compression ratio: 7.44x
File size reduction: 47597 bytes (86.56%)
Input files: 3
Archives dir: /.../release-testing/workdir/archives/folder-vllm-<TIMESTAMP>
```

> Note: `Raw input bytes` measures the structurized JSONL handed to `clp-s` (larger than the original text, because parsing adds field structure). The original three files are ~38 KB, so the effective ratio vs. your raw text is even better than the printed number.

Save the archive path — every later step uses it:

```bash
ARCHIVE="$(ls -dt release-testing/workdir/archives/folder-* | head -1)"
echo "$ARCHIVE"
```

## Step 2 — See what a record looks like

A search with the query `*` returns every record, one JSON object per line:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" '*' 2>/dev/null | head -2
```

Expected: two records with exactly these four fields. The first is special — `--structurize` preserves the log's pre-timestamp preamble (here, the `vllm serve` launch command, newlines escaped as `\n`) as a record with `"logger":"preamble"`; the second is a normal parsed line:

```json
{"timestamp":"2026-06-15 03:52:34,000","logger":"preamble","level":"INFO","message":"python -c \"import vllm; ...\"\n...vllm serve Qwen/Qwen3-0.6B \\"}
{"timestamp":"2026-06-15 03:52:34,000","logger":"importing.py:81","level":"INFO","message":"Triton not installed or not compatible; certain GPU-related functions will not be available."}
```

Count all records:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" '*' 2>/dev/null | grep -c '^{'
```

Expected: `250`

## Step 3 — Search by field (KQL)

KQL is `field:value`. The `level` and `logger` fields are directly searchable:

```bash
# How many warnings?
"$B/clp-s-search-kql" "$ARCHIVE" 'level:WARNING' 2>/dev/null | grep -c '^{'
```

Expected: `35`

```bash
# Severity breakdown. --projection returns only the named field from each
# matching record (like SELECT level FROM ...) — cheaper than full records,
# and the workhorse of the next step:
"$B/clp-s-search-kql" --projection level "$ARCHIVE" '*' 2>/dev/null \
  | jq -r '.level' | sort | uniq -c
```

Expected:

```
    215 INFO
     35 WARNING
```

## Step 4 — The one rule you must know: message content is NOT KQL-searchable

The `message` field is stored as a *CLP-string*: instead of the raw text, CLP stores a template with the variable parts factored out — this is where the compression comes from. The trade-off: a KQL query on message content **always returns 0**, even when the text is present:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" 'message:Triton' 2>/dev/null | grep -c '^{'
```

Expected: `0` — **this is correct behavior, not a bug.**

To search message content, *project* the field and grep it:

```bash
"$B/clp-s-search-kql" --projection message "$ARCHIVE" '*' 2>/dev/null \
  | jq -r '.message' | grep -c 'Triton'
```

Expected: `18` — the text was there all along; you just have to reach it this way. Tip: narrow with a searchable field first (`level:WARNING`) and then grep the projected messages — cheaper than scanning everything.

## Step 5 — Dump the log shape dictionary

A *log shape* is a message template with variables replaced by `<*>`. The dictionary is the complete vocabulary of distinct messages in the archive — the fastest way to learn what an unfamiliar log actually contains, without reading every record.

`stats.log_shapes` is **not KQL** — it is a special directive the search wrapper recognizes in the query slot. It dumps the archive's internal log shape dictionary; each raw line encodes the variable positions as special placeholder bytes, so the pipeline below pipes it through `log-shape-cache normalize`, which renders every placeholder as `<*>` and emits one clean `{"log_shape":"..."}` JSON line per template. Works on any archive (the wrapper adds the required `--experimental` flag itself):

```bash
LC="$B/log-shape-cache"
"$B/clp-s-search-kql" "$ARCHIVE" 'stats.log_shapes' 2>/dev/null \
  | "$LC" normalize > release-testing/workdir/log-shapes.ndjson
jq -s 'length' release-testing/workdir/log-shapes.ndjson
```

Expected: `100` — 250 records collapse to 100 distinct templates.

Which templates repeat most? The archive stores a count per template at compression time, so no record scan is needed:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" 'stats.log_shapes' 2>/dev/null \
  | "$B/log-shape-cache" freqs | head -3
```

Expected (top entry):

```
{"count": 9, "log_shape": "Triton not installed or not compatible; certain GPU-related functions will not <*> available."}
```

The counts over all 100 templates sum to 250, the number of records.

## Step 6 — The classification cache (NEW → UPTODATE → GROWTH)

Analyzing an archive means classifying its templates — expensive the first time, but the same application emits the same templates every run, so the plugin caches the classification, keyed by a fingerprint (SHA-256 of the sorted template set, each template capped at a character limit — 500 by default — and de-duplicated, matching what is sent for embedding). The cache is one SQLite database, `cache.sqlite`, and it stores each template as two hashes (of the full text and of its first 500 characters) with its category, never the text itself. `diff` compares your archive's templates against the cache and prints a tab-separated status header:

- `NEW <key> <count>` — never seen this app; all `<count>` templates need classifying.
- `UPTODATE <key> <count>` — fingerprint hit; nothing to do. (A template whose tail changed only past the character limit takes the category of the cached one it shares its first 500 characters with.)
- `GROWTH <key> <base_key> <count> <new_count>` — a superset of cached entry `<base_key>`; only the `<new_count>` new templates (listed as NDJSON after the header) need classifying.

`<count>` is the true full template count. The GROWTH subset test compares the hashes of the first 500 characters, so a tail-only change reports UPTODATE rather than GROWTH.

(`log-shape-cache --help` documents all subcommands.)

```bash
# Note: exported relative path — valid only while you stay at the repo root.
export CLP_LOG_SHAPE_CACHE_DIR=release-testing/workdir/lt-cache
LC="$B/log-shape-cache"

"$LC" diff --log-shapes-file release-testing/workdir/log-shapes.ndjson | head -1
```

Expected: a line starting with `NEW` — first time seeing this app; all 100 templates would need classifying.

Normally the *agent* classifies the templates (Step 9): the templates are clustered, the agent labels each cluster by id, and `log-shape-cluster expand` gives every member template the label of its cluster, written as the template's hashes. Here we build a minimal stand-in by hand just to exercise the cache: one cluster holding every template, labeled `other`. The `stand_in` function below writes that cluster file and the agent's side of the classification (`schema`, a ranked `taxonomy`, the cluster `assignments`, and a one-entry ranked `query_plan`), then runs the real `expand` and prints its output:

```bash
stand_in() {   # usage: stand_in LOG_SHAPES_NDJSON > classification.json
  W=release-testing/workdir
  jq -s '{max_chars:500, clusters:[{id:"c1", representative:.[0].log_shape,
          members:[.[].log_shape], count:length}]}' "$1" > "$W/clusters.json"
  echo '{"schema":{"message":"message"},
         "taxonomy":[{"category":"other","description":"walkthrough","priority":"low","why":"walkthrough"}],
         "assignments":[{"id":"c1","category":"other"}],
         "query_plan":[{"label":"All","match":{"field":"message","exists":true},"method":"count",
                       "category":"other","priority":"low","stage":"core"}]}' \
    > "$W/class.json"
  "$B/log-shape-cluster" expand --clusters "$W/clusters.json" \
    --classification "$W/class.json" --output "$W/expanded.json" >/dev/null
  cat "$W/expanded.json"
}

KEY="$("$LC" key --log-shapes-file release-testing/workdir/log-shapes.ndjson)"
stand_in release-testing/workdir/log-shapes.ndjson | "$LC" put --key "$KEY"

"$LC" diff --log-shapes-file release-testing/workdir/log-shapes.ndjson | head -1
```

Expected: `put` confirms with `Stored classification for app_key <key>: 100 templates in 0.0s -> .../cache.sqlite` (on stderr — not an error), and the second `diff` now prints a line starting with `UPTODATE` — cache hit; nothing to classify.

Now the incremental part. Compress only **two** of the three logs — as if this were an earlier, smaller capture of the same app — and probe with its dictionary. First store its classification, then probe with the full 3-file dictionary:

```bash
mkdir -p release-testing/workdir/two-files
cp release-testing/sample-logs/vllm/macos-m1-smoke-failure-2026-06-15.log \
   release-testing/sample-logs/vllm/macos-m1-smoke-success-2026-06-30.log \
   release-testing/workdir/two-files/

"$B/clp-s-compress-folder" --path release-testing/workdir/two-files \
  --structurize --archives-root release-testing/workdir/archives2 >/dev/null

A2="$(ls -dt release-testing/workdir/archives2/folder-* | head -1)"
"$B/clp-s-search-kql" "$A2" 'stats.log_shapes' 2>/dev/null \
  | "$LC" normalize > release-testing/workdir/log-shapes-2.ndjson
jq -s 'length' release-testing/workdir/log-shapes-2.ndjson
```

Expected: `96` — the 2-file archive has 96 templates.

```bash
# Fresh cache so the demo is deterministic; store the 96-template classification:
export CLP_LOG_SHAPE_CACHE_DIR=release-testing/workdir/lt-cache-growth
K2="$("$LC" key --log-shapes-file release-testing/workdir/log-shapes-2.ndjson)"
stand_in release-testing/workdir/log-shapes-2.ndjson | "$LC" put --key "$K2"

# Probe with the FULL 3-file dictionary — the archive "grew":
"$LC" diff --log-shapes-file release-testing/workdir/log-shapes.ndjson | head -1
```

Expected: a line starting with `GROWTH`, with `100` and `4` as the last two fields (total templates, new templates) — the cache recognized the 96 known templates and asks you to classify **only the 4 new ones**, not all 100. The NDJSON lines after the header are exactly those 4 templates:

```bash
"$LC" diff --log-shapes-file release-testing/workdir/log-shapes.ndjson | grep -c '^{'
```

Expected: `4`

This is the feature's core value: re-analyzing a growing log costs only the classification of what's new.

By the way — Steps 2, 5, and the cache probe are what the `log-insights` skill runs as its first command, via one helper:

```bash
"$B/clp-insights" bootstrap --cache-dir release-testing/workdir/lt-cache \
  --out-dir release-testing/workdir/bootstrap "$ARCHIVE"
```

Expected: an estimate line first (`[bootstrap] archive <size>; expect under a minute`), a `[bootstrap]` line as each of the three stages starts and ends, a `SAMPLE=` record, `DIST field=...` value distributions for the four fields, `LOG_SHAPE_COUNT=100`, `SHAPES_SOURCE=dump`, `FREQS=OK` with a `FREQS_FILE=` path, a `LOG_SHAPES_FILE=` path, `CACHE_MODE=UPTODATE` (the classification you stored above is fetched to `release-testing/workdir/bootstrap/log-shape-classification.json`), and `BOOTSTRAP_TIMINGS` last.

That first run also stored the archive's per-template counts in the cache database. Run it again:

```bash
"$B/clp-insights" bootstrap --cache-dir release-testing/workdir/lt-cache \
  --out-dir release-testing/workdir/bootstrap-2 "$ARCHIVE" \
  | grep 'analyzed before\|SHAPES_SOURCE\|CACHE_MODE\|LOG_SHAPES_FILE'
```

Expected: the estimate line says `analyzed before`, `SHAPES_SOURCE=stored`, `CACHE_MODE=UPTODATE`, and no `LOG_SHAPES_FILE=` line: the counts came from the database and the dictionary was not dumped. `--dump` forces the dump when you need the full template text.

## Step 7 — Semantic search (natural language)

`semantic("...")` finds records whose message *means* something similar to your query, even with no keyword overlap. It needs a reachable embedding server — the plugin never starts one. By default it uses the built-in remote endpoint (needs network); point it elsewhere with `--semantic-endpoint URL`, `CLP_SEMANTIC_ENDPOINT`, or `~/.config/yscope-clp-plugin/semantic-endpoint`:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" 'semantic("GPU features unavailable")' 2>/dev/null \
  | jq -r '.message' | sort -u | grep 'Triton'
```

Expected:

```
Triton not installed or not compatible; certain GPU-related functions will not be available.
```

— found even though your query shares no keywords with the message ("Triton" appears nowhere in it). The result set also includes other GPU-related lines (KV cache size, custom fusions); semantic matching is similarity-ranked, not exact. If this step fails with an endpoint error, the embedding service is unreachable from your machine; everything else in this walkthrough still works.

## Step 8 — Decompress (round-trip check)

```bash
"$B/clp-s-decompress" "$ARCHIVE" release-testing/workdir/decompressed
grep -c 'Triton' release-testing/workdir/decompressed/original
```

Decompression writes one JSONL file named `original` into the output directory.

Expected: `18` — the decompressed JSONL contains the same 18 Triton records you found in Step 4 via projection. Round-trip confirmed.

## Step 9 (optional) — Drive it through the agent

Everything above is what the plugin's *skills* automate. In a Claude Code session started from this repo:

```
claude --plugin-dir ./plugins/clp
```

then ask:

> Compress the logs in release-testing/sample-logs/vllm and give me log insights.

The agent should: compress with `--structurize`, report the compression stats, run `clp-insights bootstrap` (one command covering the schema sample, the 100-template dictionary dump, and the cache probe — Steps 2, 5, and 6 above — which it announces with an expected duration and follows with its `[bootstrap]` progress lines), cluster the templates with `log-shape-cluster` (which embeds them through the semantic server — it should never try to install a model or start a server), classify the cluster representatives with an opus subagent (caching the expanded result) while it asks what you already know about these logs, summarize the ranked categories and ask what to focus on while the core queries run, queue the focus ahead of the rest, post the early numbers, ask where to save the report and in which formats while the writer works, save it there, and return a report that leads with the focus, with severity counts, top templates, warnings, and follow-up queries — the same steps you just did by hand, with the expensive classification shrunk to one prompt over cluster representatives. Answer the first question with a problem (for example "requests seemed to fail") and check that the focus question recommends the categories it points at and that the report says whether the records support it. Pick HTML and PDF (PDF is offered only when a Chrome, Chromium or Edge is installed) and check that both files land where you chose, with a timestamped name.

## Cleanup

Steps 1–8 write only under `release-testing/workdir`, their caches included (`lt-cache`, `lt-cache-growth`):

```bash
rm -rf release-testing/workdir
unset CLP_LOG_SHAPE_CACHE_DIR
```

Step 9 wrote outside it:
- **The agent's archive**, under the archives root (`/tmp/yscope-clp-archives` unless you configured another).
- **A classification and the archive's stored counts**, in the cache that `CLP_LOG_SHAPE_CACHE_DIR` named in the shell you started `claude` from. That is `release-testing/workdir/lt-cache-growth` if you kept the Step 6 shell, which the `rm -rf` above already removed. Otherwise it is your real cache, `~/.config/yscope-clp-plugin/log-shape-cache`, and the commands below remove only this run's rows from it.
- **The working files** `/tmp/clp-insights-*`, `/tmp/clp-report-flags.txt`, `/tmp/log-shape-*`, `/tmp/log-shapes*.ndjson` and `/tmp/lt-diff.out`. They are shared by every analysis on the machine, so remove them only while no other analysis is running.

```bash
LC=./plugins/clp/bin/log-shape-cache
ROOT="$(./plugins/clp/bin/clp-s-compress-folder --show-archives-root | sed -n 's/^Archives root: //p')"
A="$(ls -dt "$ROOT"/folder-vllm-*/ | head -1)"      # the agent's archive: the newest one
"$LC" forget --archive-ids "$(basename "$(find "$A" -mindepth 1 -maxdepth 1 -type d | head -1)")"
"$LC" list                                          # the entry classified just now; the agent's
"$LC" forget <APP_KEY>                              #   bootstrap printed its APP_KEY too
rm -rf "$A"
rm -f /tmp/clp-insights-* /tmp/clp-report-flags.txt /tmp/log-shape-* /tmp/log-shapes*.ndjson /tmp/lt-diff.out
```

`forget` prints what it removed, and deleting nothing is not an error, so the commands are safe to repeat. Without the `forget` lines, the next agent run on these logs finds the classification (`CACHE_MODE=UPTODATE`) instead of classifying from scratch, and stored archives you no longer analyze drop out of the cache after 30 days.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `error: clp-s binary not found` | Install CLP or set `CLP_S_BIN=/path/to/clp-s`. |
| `message:<word>` returns 0 | Expected (Step 4). Message content is not KQL-searchable; project + grep instead. |
| Step 5 prints `error: no log shape entries found in input` and the count is 0 | Your `clp-s` predates the shapes API (e.g. clp-core 0.12.x) — the underlying error (`--experimental flag set but archive was not created with --experimental`) is hidden by the `2>/dev/null` in the pipeline. Your archive is fine and Steps 1–4/7–8 remain valid; only the binary is too old. Point `CLP_S_BIN` at a 0.13+ build and re-run Step 5 — no recompression needed. |
| `clp-insights bootstrap` exits 1 with `error: stats.log_shapes emitted no log shapes` | Same 0.12.x cause as above. Point `CLP_S_BIN` at a 0.13+ build and re-run. |
| The bootstrap prints no `LOG_SHAPES_FILE=` line, and `log-shapes.ndjson` is missing from its `--out-dir` | Expected when it printed `SHAPES_SOURCE=stored`: the archive was analyzed before with the same cache, so its counts came from the database and the dictionary was not dumped. Add `--dump` for the full template text. |
| `log-shape-cache freqs` fails, or the bootstrap prints `FREQS=UNAVAILABLE` | The archive was compressed by a `clp-s` build that did not store per-template counts (`count` is `null` in `stats.log_shapes`). Recompress with the current build. |
| `error: stats.logtypes was renamed to stats.log_shapes` | You ran the legacy query spelling; use `stats.log_shapes` as shown in Step 5. |
| Semantic search: endpoint error | The embedding server is unreachable. Check the endpoint (`--semantic-endpoint`, `CLP_SEMANTIC_ENDPOINT`, or `~/.config/yscope-clp-plugin/semantic-endpoint`); the plugin never starts a server itself. Keyword/log shape steps are unaffected. |
| `log-shape-cluster` exits 2 | The embedding server is unreachable or rejected (same fix as above). Clustering is pure Python standard library, so there is no dependency to install. `setup` no longer exists — clustering uses the server, not a local model. |
| Numbers differ slightly from this doc | Byte counts vary with clp-s version; record/template counts (250 / 100 / 96 / 4 / 35 / 18) should match exactly. |
