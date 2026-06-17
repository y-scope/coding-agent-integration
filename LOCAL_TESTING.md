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
- `clp-s` on `PATH` for wrapper compression/search. If `clp-s` is not on
  `PATH`, set `CLP_S_BIN=/path/to/clp-s` to point the wrappers at a
  specific binary.

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
