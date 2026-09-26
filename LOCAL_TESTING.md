# Local Testing

This guide covers command-level testing of the plugin payload from a clone of this repository. For testing the compiled installer (TUI, bootstrap, deploy), see the private installer's `LOCAL_TESTING.md`.

Everything below runs `./plugins/clp/bin/clp` straight out of the checkout. That is the only command the plugin ships: its first argument is either a subcommand or the target to analyse, and `./plugins/clp/bin/clp` with no arguments prints the whole subcommand table.

## Modes

Command smoke tests: run `bin/clp` directly from the checkout against a real `clp-s` binary.

This file does not cover installer testing. The installer and deploy tooling live in the private repository [`y-scope/coding-agent-integration-installer`](https://github.com/y-scope/coding-agent-integration-installer); see its `LOCAL_TESTING.md` for `bun run dev`, `bun run build:binary`, bootstrap invocation, and deploy dry-runs.

## Prerequisites

Install or have available:

- `bash`
- `jq`
- `shellcheck`
- `python3` — required by `bin/clp` itself, by `clp detect`, and by `clp compress folder --structurize`, which runs each text file through `bin/lib/structurize.py`.
- `clp-s` on `PATH` for compression and search. If `clp-s` is not on `PATH`, set `CLP_S_BIN=/path/to/clp-s` to point the subcommands at a specific binary. The Folder + Log shape smoke test needs **clp-core 0.13+** (shapes API) for `stats.log_shapes`; the session smoke tests work on older builds too.

The plugin also reads marketplace manifests from this repository's `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`, both of which point to `./plugins/clp`.

## Preflight

Validate the marketplace manifests and plugin metadata:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp
```

Check syntax and style. Every implementation lives in `plugins/clp/bin/lib/`, so the globs below cover all of them plus the entry point — no list to keep in sync:

```bash
for f in plugins/clp/bin/lib/*.sh; do
  bash -n "$f"
done

python3 -m py_compile plugins/clp/bin/clp plugins/clp/bin/lib/*.py

shellcheck plugins/clp/bin/lib/*.sh
```

Check that every subcommand in the table is reachable — the table in `bin/lib/commands.py` is the command's whole surface, and a name whose implementation is missing fails here:

```bash
./plugins/clp/bin/clp --help >/dev/null && echo "router OK"

for sub in list-sessions detect search decompress schema kql bundle bundle-review \
           bootstrap baseline-plan extract run focus facts shape-cache shape-cluster \
           "compress session" "compress folder" \
           "session turns" "session facts" "session score" \
           "report check" "report save"; do
  # shellcheck disable=SC2086
  ./plugins/clp/bin/clp $sub --help >/dev/null || echo "FAILED: clp $sub"
done

# `clp compress status` reserves exit 0/1/3 for the run's state, so its usage
# text exits 2. Check that it printed, not what it returned:
./plugins/clp/bin/clp compress status --help 2>&1 | head -1

# A name that is not in the table is a usage error, not a target:
./plugins/clp/bin/clp nonesuch 2>&1 | head -1   # -> error: unknown subcommand: nonesuch
```

## Command Smoke Test

List recent sessions:

```bash
./plugins/clp/bin/clp list-sessions \
  --agent claude \
  --limit 3 \
  --manifest /tmp/clp-s-local-selection.tsv
```

The table should include `IDX`, `AGENT`, modified timestamp, raw bytes, human size, session name, project/cwd, and session ID.

Dry-run compression:

```bash
./plugins/clp/bin/clp compress session \
  --selection-file /tmp/clp-s-local-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --dry-run
```

Real compression into `/tmp`:

```bash
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yscope-clp-local-smoke.XXXXXX")"

CLP_S_BIN="$(command -v clp-s)" \
./plugins/clp/bin/clp compress session \
  --selection-file /tmp/clp-s-local-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --output-dir "$SMOKE_DIR"
```

Expected output includes:

- `Raw input bytes`
- `Archive bytes`
- `Compression ratio`
- `File size reduction`
- `Resolved clp-s archive dir`
- `Archive metadata`

Search the top-level archive directory printed by compression:

```bash
CLP_S_BIN="$(command -v clp-s)" \
./plugins/clp/bin/clp search \
  "$SMOKE_DIR" \
  'type:assistant'
```

Dry-run decompression:

```bash
CLP_S_BIN="$(command -v clp-s)" \
./plugins/clp/bin/clp decompress \
  --dry-run \
  "$SMOKE_DIR" \
  "${SMOKE_DIR}-out"
```

Claude and Codex share one `bin/clp`. Agent-specific tuning lives in `plugins/clp/skills-claude/` and `plugins/clp/skills-codex/`.

## Folder + Log Shape Smoke Test

Covers `clp detect`, `clp compress folder --structurize` and the `analyze-logs` general-route flow. Point `LOG_DIR` at a folder of vLLM text logs (`release-testing/sample-logs/vllm` works).

```bash
LOG_DIR=/path/to/logs
FOLDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yscope-clp-folder-smoke.XXXXXX")"

./plugins/clp/bin/clp detect "$LOG_DIR"
```

Expected: `Input: ... is a folder, N matching file(s)`, a `format: text: ... match the bundled vllm-sflow format` (or `vllm-raw`) block per file, and `SUGGEST clp compress folder --path ... --structurize`. It writes nothing. Then compress:

```bash
./plugins/clp/bin/clp compress folder \
  --path "$LOG_DIR" \
  --structurize \
  --archives-root "$FOLDER_DIR"
```

Expected output includes one `[structurize] <file>: N records` line per file, `Structurize: converted N file(s)`, the compression stats, `Archives dir`, and `Archive metadata`.

Discover the schema and dump the log shape dictionary from the printed `Archives dir`:

```bash
# Newest archive dir, so re-running the smoke test does not break the glob:
ARCHIVE="$(ls -dt "$FOLDER_DIR"/folder-* | head -1)"

# One full record reveals the field names (structurize yields
# timestamp/logger/level/message):
./plugins/clp/bin/clp search "$ARCHIVE" '*' 2>/dev/null | head -1

# stats.log_shapes emits raw shape lines (placeholder bytes, not <*>);
# clp shape-cache normalize renders them to canonical {"log_shape":...} NDJSON.
# The wrapper adds the required --experimental flag automatically.
./plugins/clp/bin/clp search "$ARCHIVE" 'stats.log_shapes' 2>/dev/null \
  | ./plugins/clp/bin/clp shape-cache normalize > /tmp/smoke-log-shapes.ndjson
jq -s 'length' /tmp/smoke-log-shapes.ndjson
```

Exercise the classification cache. On a fresh cache dir `diff` reports `NEW` and lists every template. Storing a classification under that key flips the same input to `UPTODATE`; an archive that has since grown reports `GROWTH` and lists only the newly-added templates:

```bash
export CLP_LOG_SHAPE_CACHE_DIR=/tmp/smoke-lt-cache
LC=(./plugins/clp/bin/clp shape-cache)   # a subcommand is two words, so an array

"${LC[@]}" count --log-shapes-file /tmp/smoke-log-shapes.ndjson
"${LC[@]}" diff  --log-shapes-file /tmp/smoke-log-shapes.ndjson | head -1   # -> NEW

# Store a minimal classification for this template set, then re-probe. The
# stand-in is one cluster holding every template, labeled "other"; the real
# `expand` turns it into a classification that names templates by hash.
stand_in() {   # usage: stand_in LOG_SHAPES_NDJSON > classification.json
  jq -s '{max_chars:500, clusters:[{id:"c1", representative:.[0].log_shape,
          members:[.[].log_shape], count:length}]}' "$1" > /tmp/smoke-clusters.json
  echo '{"schema":{"message":"message"},"taxonomy":[{"category":"other","description":"smoke","priority":"low","why":"smoke"}],
         "assignments":[{"id":"c1","category":"other"}],
         "query_plan":[{"label":"All","match":{"field":"message","exists":true},"method":"count",
                       "category":"other","priority":"low","stage":"core"}]}' \
    > /tmp/smoke-class.json
  ./plugins/clp/bin/clp shape-cluster expand --clusters /tmp/smoke-clusters.json \
    --classification /tmp/smoke-class.json --output /tmp/smoke-expanded.json >/dev/null
  cat /tmp/smoke-expanded.json
}
KEY="$("${LC[@]}" key --log-shapes-file /tmp/smoke-log-shapes.ndjson)"
stand_in /tmp/smoke-log-shapes.ndjson | "${LC[@]}" put --key "$KEY"

"${LC[@]}" diff --log-shapes-file /tmp/smoke-log-shapes.ndjson | head -1   # -> UPTODATE
"${LC[@]}" list
```

Exercise the general route's stages, the ones `clp <TARGET>` hands off to. `clp bootstrap` wraps the schema sample, the dictionary dump with per-template frequencies, and the cache probe in one command, and stores the archive's counts in the cache database so a later run on the same archive skips the dump. It needs clp-core 0.13+; older builds make it exit 1 with `error: stats.log_shapes emitted no log shapes`. `--dump` makes this first run dump the dictionary even when you repeat the block, so the clusterer below always has the full template text:

```bash
./plugins/clp/bin/clp bootstrap --dump \
  --cache-dir /tmp/smoke-lt-cache --out-dir /tmp/smoke-bootstrap "$ARCHIVE"
# Expect an estimate line, [bootstrap] start/end lines for stages 1/3-3/3, DIST
# lines, LOG_SHAPE_COUNT>0, SHAPES_SOURCE=dump, FREQS=OK, a LOG_SHAPES_FILE= line,
# CACHE_MODE=UPTODATE (cache primed above), and BOOTSTRAP_TIMINGS.

# Again without --dump: the archive is now stored, so nothing is dumped.
./plugins/clp/bin/clp bootstrap \
  --cache-dir /tmp/smoke-lt-cache --out-dir /tmp/smoke-bootstrap-stored "$ARCHIVE" \
  | grep 'analyzed before\|SHAPES_SOURCE\|CACHE_MODE\|LOG_SHAPES_FILE'
# Expect "analyzed before" in the estimate line, SHAPES_SOURCE=stored,
# CACHE_MODE=UPTODATE, and no LOG_SHAPES_FILE line.

# Clusterer: embeds via the semantic server (no setup, no local model).
# Needs a reachable endpoint — the built-in remote default is used unless
# CLP_SEMANTIC_ENDPOINT or the semantic-endpoint config file says otherwise:
./plugins/clp/bin/clp shape-cluster cluster --max-chars 500 \
  --input /tmp/smoke-bootstrap/log-shapes.ndjson --output /tmp/smoke-lt-clusters.json
# Expect CLUSTERS<=EMBEDDED<=TEMPLATES and one {"id","count","representative"}
# line per cluster; /tmp/smoke-lt-clusters.json holds the memberships for
# `expand`. Representatives/members are FULL templates. (Without --output they
# go to /tmp/log-shape-clusters.json, which a running analysis may be using.)
```

Truncation and fingerprinting (no embedding server needed). The cache key is computed over templates capped at `MAX_CHARS` characters and de-duplicated, so a change that only affects a template's tail past the limit must NOT register as growth, while a change within the limit must:

```bash
LC=(./plugins/clp/bin/clp shape-cache)
D=/tmp/smoke-trunc; mkdir -p "$D"; export CLP_LOG_SHAPE_CACHE_DIR="$D/cache"
P="$(python3 -c 'print("P"*500)')"
printf '{"log_shape":"%sAAA"}\n' "$P" > "$D/base.ndjson"
printf '{"log_shape":"%sBBB"}\n' "$P" > "$D/other.ndjson"   # differs only past 500
printf '{"log_shape":"%sAAA"}\n{"log_shape":"new within limit"}\n' "$P" > "$D/grown.ndjson"

# Same key despite the post-limit tail change; count stays the FULL count:
[ "$("${LC[@]}" key --log-shapes-file "$D/base.ndjson")" \
  = "$("${LC[@]}" key --log-shapes-file "$D/other.ndjson")" ] && echo "key OK"
"${LC[@]}" count --log-shapes-file "$D/base.ndjson"        # -> 1

stand_in "$D/base.ndjson" \
  | "${LC[@]}" put --max-chars 500 \
      --key "$("${LC[@]}" key --log-shapes-file "$D/base.ndjson")"

"${LC[@]}" diff --log-shapes-file "$D/other.ndjson" | head -1   # -> UPTODATE
# The post-limit variant takes the stored category through the shared prefix hash:
"${LC[@]}" get "$("${LC[@]}" key --log-shapes-file "$D/other.ndjson")" > "$D/class.json"
./plugins/clp/bin/clp extract --classification-file "$D/class.json" \
  --no-freqs --log-shapes-file "$D/other.ndjson" --out-templates "$D/t.txt" \
  --out-query-plan "$D/q.txt" --out-drill-plan "$D/d.txt" \
  --focus-inbox "$D/inbox.ndjson" --focus-file "$D/focus.json" \
  | grep CATEGORY   # -> CATEGORY other 1 (no UNCLASSIFIED=)
"${LC[@]}" diff --log-shapes-file "$D/grown.ndjson" | head -1   # -> GROWTH ... 1
```

Note that the message field is a CLP-string: `message:term` returns 0 by design. Match message content by projecting the field and grepping it, and use the scalar fields for KQL:

```bash
./plugins/clp/bin/clp search "$ARCHIVE" 'level:WARNING' 2>/dev/null | grep -c '^{'
./plugins/clp/bin/clp search --projection message "$ARCHIVE" '*' 2>/dev/null \
  | jq -r '.message' | grep -c 'SomeStaticText'
```

### Cleanup

The smoke tests keep their caches and outputs under `/tmp/smoke-*` and the two `mktemp` directories, so your real classification cache under `~/.config/yscope-clp-plugin/` and the `/tmp/log-shape-*` files of a running analysis are untouched. In the shell you ran them from:

```bash
rm -rf "$SMOKE_DIR" "$FOLDER_DIR" /tmp/clp-s-local-selection.tsv \
  /tmp/smoke-log-shapes.ndjson /tmp/smoke-lt-cache /tmp/smoke-clusters.json \
  /tmp/smoke-class.json /tmp/smoke-expanded.json /tmp/smoke-bootstrap \
  /tmp/smoke-bootstrap-stored /tmp/smoke-lt-clusters.json /tmp/smoke-trunc
unset CLP_LOG_SHAPE_CACHE_DIR
```

## Manual Local Marketplace Install

These commands modify local Claude/Codex plugin configuration but do not deploy or upload anything. Useful for testing a skill change without re-cutting a release.

Claude:

```bash
claude plugin marketplace add "$PWD" --scope user
claude plugin install clp@yscope --scope user
```

Codex:

```bash
codex plugin marketplace add "$PWD"
codex plugin add clp@yscope
```

After installing, start a new Claude/Codex session before testing plugin skills.

## Avoid Production During Local Testing

Do not pass these unless intentionally testing remote install behavior:

```text
--manifest-url
YSCOPE_CLP_INSTALL_MANIFEST_URL
YSCOPE_CLP_INSTALLER_MANIFEST_URL
YSCOPE_CLP_INSTALLER_URL
```

The command smoke tests do not call the installer bootstrap and never reach R2; these environment variables are only relevant for installer testing in the private repo.
