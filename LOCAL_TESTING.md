# Local Testing

This guide covers wrapper-level testing of the plugin payload from a clone
of this repository. For testing the compiled installer (TUI, bootstrap,
deploy), see the private installer's `LOCAL_TESTING.md`.

## Modes

Wrapper smoke tests: run plugin scripts directly from the checkout against
a real `clp-s` binary.

This file does not cover installer testing. The installer and deploy
tooling live in the private repository
[`y-scope/coding-agent-integration-installer`](https://github.com/y-scope/coding-agent-integration-installer);
see its `LOCAL_TESTING.md` for `bun run dev`, `bun run build:binary`,
bootstrap invocation, and deploy dry-runs.

## Prerequisites

Install or have available:

- `bash`
- `jq`
- `shellcheck`
- `python3` — required by `clp-s-compress-folder --structurize`, which runs
  each input file through `bin/structurize.py`.
- `clp-s` on `PATH` for wrapper compression/search. If `clp-s` is not on
  `PATH`, set `CLP_S_BIN=/path/to/clp-s` to point the wrappers at a
  specific binary. The Folder + Logtype smoke test needs **clp-core 0.13+**
  (shapes API) for `stats.log_shapes`; the session wrapper smoke tests work
  on older builds too.

The plugin also reads marketplace manifests from this repository's
`.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`,
both of which point to `./plugins/clp`.

## Preflight

Validate the marketplace manifests and plugin metadata:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp
```

Check shell wrapper syntax and style:

```bash
for f in plugins/clp/bin/clp-s-*; do
  bash -n "$f"
done

shellcheck \
  plugins/clp/bin/clp-s-list-sessions \
  plugins/clp/bin/clp-s-compress-session \
  plugins/clp/bin/clp-s-compress-folder \
  plugins/clp/bin/clp-s-search-kql \
  plugins/clp/bin/clp-s-decompress \
  plugins/clp/bin/lib/clp-common.sh
```

## Wrapper Smoke Test

List recent sessions:

```bash
./plugins/clp/bin/clp-s-list-sessions \
  --agent claude \
  --limit 3 \
  --manifest /tmp/clp-s-local-selection.tsv
```

The table should include `IDX`, `AGENT`, modified timestamp, raw bytes, human
size, session name, project/cwd, and session ID.

Dry-run compression:

```bash
./plugins/clp/bin/clp-s-compress-session \
  --selection-file /tmp/clp-s-local-selection.tsv \
  --session-index 1 \
  --timestamp-key timestamp \
  --dry-run
```

Real compression into `/tmp`:

```bash
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yscope-clp-local-smoke.XXXXXX")"

CLP_S_BIN="$(command -v clp-s)" \
./plugins/clp/bin/clp-s-compress-session \
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
./plugins/clp/bin/clp-s-search-kql \
  "$SMOKE_DIR" \
  'type:assistant'
```

Dry-run decompression:

```bash
CLP_S_BIN="$(command -v clp-s)" \
./plugins/clp/bin/clp-s-decompress \
  --dry-run \
  "$SMOKE_DIR" \
  "${SMOKE_DIR}-out"
```

Claude and Codex share this wrapper directory. Agent-specific tuning lives in
`plugins/clp/skills-claude/` and `plugins/clp/skills-codex/`.

## Folder + Logtype Smoke Test

Covers `clp-s-compress-folder --structurize` and the `logtype-insights` flow.
Point `LOG_DIR` at any folder of plain-text logs.

```bash
LOG_DIR=/path/to/logs
FOLDER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yscope-clp-folder-smoke.XXXXXX")"

./plugins/clp/bin/clp-s-compress-folder \
  --folder "$LOG_DIR" \
  --structurize \
  --archives-root "$FOLDER_DIR"
```

Expected output includes `Structurize: converted N file(s)`, the compression
stats, `Archives dir`, and `Archive metadata`.

Discover the schema and dump the logtype dictionary from the printed
`Archives dir`:

```bash
# Newest archive dir, so re-running the smoke test does not break the glob:
ARCHIVE="$(ls -dt "$FOLDER_DIR"/folder-* | head -1)"

# One full record reveals the field names (structurize yields
# timestamp/logger/level/message):
./plugins/clp/bin/clp-s-search-kql "$ARCHIVE" '*' 2>/dev/null | grep '^{' | head -1

# The wrapper prints metadata header lines to stdout, so filter with grep '^{'.
# stats.log_shapes emits raw shape lines (placeholder bytes, not <*>);
# logtype-cache normalize renders them to canonical {"logtype":...} NDJSON.
# The wrapper adds the required --experimental flag automatically.
./plugins/clp/bin/clp-s-search-kql "$ARCHIVE" 'stats.log_shapes' 2>/dev/null \
  | grep '^{' | ./plugins/clp/bin/logtype-cache normalize > /tmp/smoke-logtypes.ndjson
jq -s 'length' /tmp/smoke-logtypes.ndjson
```

Exercise the classification cache. On a fresh cache dir `diff` reports `NEW`
and lists every template. Storing a classification under that key flips the
same input to `UPTODATE`; an archive that has since grown reports `GROWTH` and
lists only the newly-added templates:

```bash
export CLP_LOGTYPE_CACHE_DIR=/tmp/smoke-lt-cache
LC=./plugins/clp/bin/logtype-cache

"$LC" count --logtypes-file /tmp/smoke-logtypes.ndjson
"$LC" diff  --logtypes-file /tmp/smoke-logtypes.ndjson | head -1   # -> NEW

# Store a minimal classification for this template set, then re-probe.
KEY="$("$LC" key --logtypes-file /tmp/smoke-logtypes.ndjson)"
jq -s '{schema:{message:"message"},
        taxonomy:[{category:"other",description:"smoke"}],
        templates:[.[]|{logtype:.logtype,category:"other"}],
        query_plan:[{label:"All",kql:"*",method:"count"}]}' \
  /tmp/smoke-logtypes.ndjson | "$LC" put-merged --key "$KEY"

"$LC" diff --logtypes-file /tmp/smoke-logtypes.ndjson | head -1   # -> UPTODATE
"$LC" list
```

Note that the message field is a CLP-string: `message:term` returns 0 by
design. Match message content by projecting the field and grepping it, and use
the scalar fields for KQL:

```bash
./plugins/clp/bin/clp-s-search-kql "$ARCHIVE" 'level:WARNING' 2>/dev/null | grep -c '^{'
./plugins/clp/bin/clp-s-search-kql --projection message "$ARCHIVE" '*' 2>/dev/null \
  | grep '^{' | jq -r '.message' | grep -c 'SomeStaticText'
```

## Manual Local Marketplace Install

These commands modify local Claude/Codex plugin configuration but do not
deploy or upload anything. Useful for testing a skill change without
re-cutting a release.

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

After installing, start a new Claude/Codex session before testing plugin
skills.

## Avoid Production During Local Testing

Do not pass these unless intentionally testing remote install behavior:

```text
--manifest-url
YSCOPE_CLP_INSTALL_MANIFEST_URL
YSCOPE_CLP_INSTALLER_MANIFEST_URL
YSCOPE_CLP_INSTALLER_URL
```

Wrapper smoke tests do not call the bootstrap or installer and never reach
R2; these environment variables are only relevant for installer testing
in the private repo.
