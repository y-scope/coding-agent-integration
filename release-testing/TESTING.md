# Release Testing: CLP Plugin Walkthrough (vLLM logs)

A step-by-step, copy-paste walkthrough of the CLP coding-agent plugin for
someone who has **never used CLP** and doesn't know what it is. Every command
shows its expected output so you can tell immediately whether something is
wrong. Total time: ~10 minutes.

**What is CLP?** CLP (Compressed Log Processor) compresses log files into a
small archive that you can *search without decompressing*. This plugin wraps
CLP for coding agents (Claude Code / Codex): compress logs, search them with
KQL queries, ask natural-language ("semantic") questions, and analyze an
archive by its "logtypes" — the distinct message templates the application
emits.

**The test data** is three real vLLM server logs in `sample-logs/vllm/`
(a failed smoke run, a successful one, and a bug-fix run — ~38 KB of plain
text). vLLM is an LLM inference server; you don't need to know anything about
it. The logs look like this:

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

  If `MISSING`: install CLP (the plugin's hosted installer ships the binary,
  or download a clp-core release), or point the wrappers at an existing
  binary once per shell with `export CLP_S_BIN=/path/to/clp-s`. Note the
  check only confirms the binary exists — it cannot read the version. If
  your build is older than 0.13, Steps 1–4, 7 and 8 still work; Step 5 will
  fail with `no shape/logtype entries found` (see Troubleshooting — your
  archive stays valid, only the binary needs updating).

All commands are run **from the repository root**. Outputs go to
`release-testing/workdir/` (gitignored — safe to delete at any time).

```bash
rm -rf release-testing/workdir   # start clean so every expected output matches
mkdir -p release-testing/workdir
B=./plugins/clp/bin      # the plugin's command wrappers
```

> **Keep one shell open for the whole walkthrough.** Later steps reuse
> variables defined earlier (`B`, `ARCHIVE`, `LC`, `KEY`, `A2`, `K2`,
> `CLP_LOGTYPE_CACHE_DIR`). If you lose your shell, re-run the Setup block
> above and the `ARCHIVE=` line in Step 1, then continue where you left off.

One output convention used throughout: the wrappers print human-readable
header lines (archive path, metadata, the underlying command) to **stdout**
before the JSON results. `grep '^{'` keeps only the JSON records — that is
why it appears in every pipeline below. (The `2>/dev/null` merely silences
occasional wrapper warnings; it is *not* what removes the headers.)

## Step 1 — Compress the logs into an archive

These are plain-text logs, so pass `--structurize`: it parses each line into
`timestamp / logger / level / message` fields first, which is what makes the
archive searchable by field.

```bash
"$B/clp-s-compress-folder" \
  --folder release-testing/sample-logs/vllm \
  --structurize \
  --archives-root release-testing/workdir/archives
```

Expected output — the run prints ~20 lines (source folder, flags, the
underlying `clp-s` command, metadata paths); the ones to check are these
(sizes may vary by a few bytes, and paths are printed absolute):

```
Structurize: converted 3 file(s) to structured JSONL
...
Raw input bytes: 54989
Archive bytes: 7392
Compression ratio: 7.44x
File size reduction: 47597 bytes (86.56%)
Input files: 3
Archives dir: /.../release-testing/workdir/archives/folder-vllm-<TIMESTAMP>
```

> Note: `Raw input bytes` measures the structurized JSONL handed to `clp-s`
> (larger than the original text, because parsing adds field structure). The
> original three files are ~38 KB, so the effective ratio vs. your raw text is
> even better than the printed number.

Save the archive path — every later step uses it:

```bash
ARCHIVE="$(ls -dt release-testing/workdir/archives/folder-* | head -1)"
echo "$ARCHIVE"
```

## Step 2 — See what a record looks like

A search with the query `*` returns every record, one JSON object per line
(after the `grep '^{'` header filter explained in Setup):

```bash
"$B/clp-s-search-kql" "$ARCHIVE" '*' 2>/dev/null | grep '^{' | head -2
```

Expected: two records with exactly these four fields. The first is special —
`--structurize` preserves the log's pre-timestamp preamble (here, the `vllm
serve` launch command, newlines escaped as `\n`) as a record with
`"logger":"preamble"`; the second is a normal parsed line:

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
  | grep '^{' | jq -r '.level' | sort | uniq -c
```

Expected:

```
    215 INFO
     35 WARNING
```

## Step 4 — The one rule you must know: message content is NOT KQL-searchable

The `message` field is stored as a *CLP-string*: instead of the raw text, CLP
stores a template with the variable parts factored out — this is where the
compression comes from. The trade-off: a KQL query on message content
**always returns 0**, even when the text is present:

```bash
"$B/clp-s-search-kql" "$ARCHIVE" 'message:Triton' 2>/dev/null | grep -c '^{'
```

Expected: `0` — **this is correct behavior, not a bug.**

To search message content, *project* the field and grep it:

```bash
"$B/clp-s-search-kql" --projection message "$ARCHIVE" '*' 2>/dev/null \
  | grep '^{' | jq -r '.message' | grep -c 'Triton'
```

Expected: `18` — the text was there all along; you just have to reach it this
way. Tip: narrow with a searchable field first (`level:WARNING`) and then grep
the projected messages — cheaper than scanning everything.

## Step 5 — Dump the logtype dictionary

A *logtype* is a message template with variables replaced by `<*>`. The
dictionary is the complete vocabulary of distinct messages in the archive —
the fastest way to learn what an unfamiliar log actually contains, without
reading every record.

`stats.log_shapes` is **not KQL** — it is a special directive the search
wrapper recognizes in the query slot. It dumps the archive's internal
logtype dictionary; each raw line encodes the variable positions as special
placeholder bytes, so the pipeline below pipes it through
`logtype-cache normalize`, which renders every placeholder as `<*>` and
emits one clean `{"logtype":"..."}` JSON line per template. Works on any
archive (the wrapper adds the required `--experimental` flag itself):

```bash
LC="$B/logtype-cache"
"$B/clp-s-search-kql" "$ARCHIVE" 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | "$LC" normalize > release-testing/workdir/logtypes.ndjson
jq -s 'length' release-testing/workdir/logtypes.ndjson
```

Expected: `100` — 250 records collapse to 100 distinct templates.

Which templates repeat most? Project the messages, templatize, count:

```bash
"$B/clp-s-search-kql" --projection message "$ARCHIVE" '*' 2>/dev/null \
  | grep '^{' | jq -r '.message' \
  | sed -E 's/\{[^}]+\}/<*>/g; s/0x[0-9a-fA-F]+/<*>/g; s/\b[0-9]+\b/<*>/g' \
  | sort | uniq -c | sort -rn | head -3
```

Expected (top entry):

```
      9 Triton not installed or not compatible; certain GPU-related functions will not be available.
```

## Step 6 — The classification cache (NEW → UPTODATE → GROWTH)

Analyzing an archive means classifying its templates — expensive the first
time, but the same application emits the same templates every run, so the
plugin caches the classification, keyed by a fingerprint (SHA-256 of the
sorted template set). `diff` compares your archive's templates against the
cache and prints a tab-separated status header:

- `NEW <key> <count>` — never seen this app; all `<count>` templates need classifying.
- `UPTODATE <key> <count>` — exact cache hit; nothing to do.
- `GROWTH <key> <base_key> <count> <new_count>` — a superset of cached entry
  `<base_key>`; only the `<new_count>` new templates (listed as NDJSON after
  the header) need classifying.

(`logtype-cache --help` documents all subcommands.)

```bash
# Note: exported relative path — valid only while you stay at the repo root.
export CLP_LOGTYPE_CACHE_DIR=release-testing/workdir/lt-cache
LC="$B/logtype-cache"

"$LC" diff --logtypes-file release-testing/workdir/logtypes.ndjson | head -1
```

Expected: a line starting with `NEW` — first time seeing this app; all 100
templates would need classifying.

Normally the *agent* classifies the templates (Step 9); here we store a
minimal stand-in by hand just to exercise the cache. A stored classification
is a JSON object with four keys: `schema` (which record field holds the
message), `taxonomy` (the category list), `templates` (each logtype →
category), and `query_plan` (suggested follow-up queries). The `jq` below
builds the simplest valid one — every template categorized as `other`:

```bash
KEY="$("$LC" key --logtypes-file release-testing/workdir/logtypes.ndjson)"
jq -s '{schema:{message:"message"},
        taxonomy:[{category:"other",description:"walkthrough"}],
        templates:[.[]|{logtype:.logtype,category:"other"}],
        query_plan:[{label:"All",kql:"*",method:"count"}]}' \
  release-testing/workdir/logtypes.ndjson | "$LC" put-merged --key "$KEY"

"$LC" diff --logtypes-file release-testing/workdir/logtypes.ndjson | head -1
```

Expected: `put-merged` confirms with `Stored classification for app_key
<key> -> ...` (on stderr — not an error), and the second `diff` now prints a
line starting with `UPTODATE` — cache hit; nothing to classify.

Now the incremental part. Compress only **two** of the three logs — as if this
were an earlier, smaller capture of the same app — and probe with its
dictionary. First store its classification, then probe with the full 3-file
dictionary:

```bash
mkdir -p release-testing/workdir/two-files
cp release-testing/sample-logs/vllm/macos-m1-smoke-failure-2026-06-15.log \
   release-testing/sample-logs/vllm/macos-m1-smoke-success-2026-06-30.log \
   release-testing/workdir/two-files/

"$B/clp-s-compress-folder" --folder release-testing/workdir/two-files \
  --structurize --archives-root release-testing/workdir/archives2 >/dev/null

A2="$(ls -dt release-testing/workdir/archives2/folder-* | head -1)"
"$B/clp-s-search-kql" "$A2" 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | "$LC" normalize > release-testing/workdir/logtypes-2.ndjson
jq -s 'length' release-testing/workdir/logtypes-2.ndjson
```

Expected: `96` — the 2-file archive has 96 templates.

```bash
# Fresh cache so the demo is deterministic; store the 96-template classification:
export CLP_LOGTYPE_CACHE_DIR=release-testing/workdir/lt-cache-growth
K2="$("$LC" key --logtypes-file release-testing/workdir/logtypes-2.ndjson)"
jq -s '{schema:{message:"message"},
        taxonomy:[{category:"other",description:"walkthrough"}],
        templates:[.[]|{logtype:.logtype,category:"other"}],
        query_plan:[{label:"All",kql:"*",method:"count"}]}' \
  release-testing/workdir/logtypes-2.ndjson | "$LC" put-merged --key "$K2"

# Probe with the FULL 3-file dictionary — the archive "grew":
"$LC" diff --logtypes-file release-testing/workdir/logtypes.ndjson | head -1
```

Expected: a line starting with `GROWTH`, with `100` and `4` as the last two
fields (total templates, new templates) — the cache recognized the 96 known
templates and asks you to classify **only the 4 new ones**, not all 100. The
NDJSON lines after the header are exactly those 4 templates:

```bash
"$LC" diff --logtypes-file release-testing/workdir/logtypes.ndjson | grep -c '^{'
```

Expected: `4`

This is the feature's core value: re-analyzing a growing log costs only the
classification of what's new.

## Step 7 — Semantic search (natural language)

`semantic("...")` finds records whose message *means* something similar to
your query, even with no keyword overlap. It needs a working embedding
endpoint (auto-detected; requires network unless you run a local embedding
server):

```bash
"$B/clp-s-search-kql" "$ARCHIVE" 'semantic("GPU features unavailable")' 2>/dev/null \
  | grep '^{' | jq -r '.message' | sort -u | grep 'Triton'
```

Expected:

```
Triton not installed or not compatible; certain GPU-related functions will not be available.
```

— found even though your query shares no keywords with the message ("Triton"
appears nowhere in it). The result set also includes other GPU-related lines
(KV cache size, custom fusions); semantic matching is similarity-ranked, not
exact. If this step fails with an endpoint error, the embedding service is
unreachable from your machine; everything else in this walkthrough still
works.

## Step 8 — Decompress (round-trip check)

```bash
"$B/clp-s-decompress" "$ARCHIVE" release-testing/workdir/decompressed
grep -c 'Triton' release-testing/workdir/decompressed/original
```

Decompression writes one JSONL file named `original` into the output
directory.

Expected: `18` — the decompressed JSONL contains the same 18 Triton records
you found in Step 4 via projection. Round-trip confirmed.

## Step 9 (optional) — Drive it through the agent

Everything above is what the plugin's *skills* automate. In a Claude Code
session started from this repo:

```
claude --plugin-dir ./plugins/clp
```

then ask:

> Compress the logs in release-testing/sample-logs/vllm and give me logtype
> insights.

The agent should: compress with `--structurize`, report the compression stats,
dump the 100-template dictionary, classify the templates (caching the result),
and return a report with severity counts, top templates, warnings, and
follow-up queries — the same steps you just did by hand.

## Cleanup

```bash
rm -rf release-testing/workdir
```

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `error: clp-s binary not found` | Install CLP or set `CLP_S_BIN=/path/to/clp-s`. |
| `jq: parse error: Invalid numeric literal` | You piped wrapper output straight into `jq`. The wrapper prints header lines first — always filter with `grep '^{'`. |
| `message:<word>` returns 0 | Expected (Step 4). Message content is not KQL-searchable; project + grep instead. |
| Step 5 prints `error: no shape/logtype entries found in input` and the count is 0 | Your `clp-s` predates the shapes API (e.g. clp-core 0.12.x) — the underlying error (`--experimental flag set but archive was not created with --experimental`) is hidden by the `2>/dev/null` in the pipeline. Your archive is fine and Steps 1–4/7–8 remain valid; only the binary is too old. Point `CLP_S_BIN` at a 0.13+ build and re-run Step 5 — no recompression needed. |
| `error: stats.logtypes was renamed to stats.log_shapes` | You ran the legacy query spelling; use `stats.log_shapes` as shown in Step 5. |
| Semantic search: endpoint error | The embedding service is unreachable. Keyword/logtype steps are unaffected. |
| `warning: clp-s does not support --semantic-cache-dir` | Your `clp-s` build lacks the local semantic cache; the search falls back to remote-only scoring and still works. |
| Numbers differ slightly from this doc | Byte counts vary with clp-s version; record/template counts (250 / 100 / 96 / 4 / 35 / 18) should match exactly. |
