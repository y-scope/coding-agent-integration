---
name: experimental
description: Compress and search CLP archives with experimental (clpp) features — decomposed queries, log shapes, and the shape()/decompose() KQL functions.
allowed-tools:
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session:*)"
  - "Bash(${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql:*)"
---

# Experimental (clpp)

Use this skill for CLP's **experimental** surface — the `clpp` layer that adds
decomposed queries, log-shape dictionaries, parent-rule shapes, and the
`shape()`/`decompose()` KQL functions. This is distinct from the stable
`compress`/`search` skills: the experimental features are gated behind the
`--experimental` flag on the same wrappers, and the clpp KQL layer is only active
when that flag is passed.

Use only the plugin wrappers. Do not call bare `clp-s`.

For non-experimental compress/search, use the `compress` and `search` skills
instead. For pointing the wrappers at a local source build (e.g. an in-flight
clpp branch), see the `dev` skill.

## Compress

Experimental compression requires a **parsing specification** (a log-surgeon spec
that drives the clpp decomposed-query/schema layer). Pass it with
`--parsing-specification PATH`; the wrapper implies `--experimental` for you, so
you do not need to pass both.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
  --session-file ~/.claude/projects/<…>/session.jsonl \
  --parsing-specification /path/to/parsing-spec.txt \
  --timestamp-key timestamp
```

Notes:
- The parsing spec **must match the structure of the input**. The benchmark
  specs under `./benchmark/parsing-specs/` in a CLP source checkout are tuned for
  the hive dataset, not for Claude/Codex session JSON — supply a session-appropriate
  spec when compressing sessions.
- `--experimental` alone (without `--parsing-specification`) is rejected by the
  wrapper, since clpp compression needs the spec.
- All stable compress options (`--compression-level`, `--target-encoded-size`,
  `--print-archive-stats`, `--output-dir`, `--dry-run`, archive-root config) still
  apply. The wrapper records the full experimental command in the archive metadata
  JSON.
- Use `--clp-s-bin PATH` to point at a local build (see the `dev` skill).

After compression, report the same stats as the `compress` skill: raw input
bytes, archive bytes, compression ratio, file size reduction, archives dir, and
archive metadata.

## Search

Pass `--experimental` to activate the clpp KQL layer. Without it, `shape()` and
`decompose()` are treated as plain text rather than functions.

### KQL functions

| Function | Where | Effect |
| --- | --- | --- |
| `shape(column)` | filter | Wildcard-match against the column's log shape text. |
| `shape(column)` | projection | Output the column's shape string. |
| `decompose(column)` | projection | Output the column's decomposed view (shape + leaf values). |

Filter — wildcard match against log shape text:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  /tmp/archive 'shape(message): "*error*"'
```

Project the shape string of a column:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --project 'shape(message)' \
  /tmp/archive '*'
```

Project the decomposed view of a column:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --project 'decompose(message)' \
  /tmp/archive '*'
```

Count matches without fetching full records:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  /tmp/archive 'shape(message): "*error*"' | grep -c '^{'
```

### Output keys

Projected `shape()` and `decompose()` results use a `.` separator in the JSON
output keys: `"message.shape"` and `"message.decompose"`.

## Tips

- Combine experimental filters with stable KQL and time-range flags:
  `shape(message): "*error*" AND level:error`, plus `--tge`/`--tle` for time
  windows. Time ranges are flags, never KQL (same gotcha as the `search` skill).
- Use `--clp-s-bin PATH` (or the `CLP_S_BIN` env var) to run the search against a
  local clpp-branch build that may be ahead of the installed binary.
- `--experimental` only changes which KQL features are active; stable queries run
  unchanged when the flag is added, so it is safe to leave it on for a clpp
  archive.