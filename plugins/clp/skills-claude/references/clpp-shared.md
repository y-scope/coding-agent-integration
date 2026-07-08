# clpp (clp+ / `--experimental`) shared reference

clpp is CLP's **experimental** layer — decomposed queries, log-shape
dictionaries, parent-rule shapes, and the `shape()`/`decompose()` KQL functions.
It is gated behind the `--experimental` flag on the same wrappers used by the
stable `compress`/`search` skills. The clpp KQL layer is only active when that
flag is passed.

This file holds the clpp-specific additions shared by `clpp-compress` and
`clpp-search`. The common compress workflow and KQL syntax live in
`shared-compress.md` and `shared-search.md` — read those too.

Use only the plugin wrappers. Do not call bare `clp-s`.

## `--experimental` and `--parsing-specification` must be passed together

For **compression**, the clp-s binary requires **both** flags — it rejects either
one alone with:

> `--experimental and --parsing-specification must both be non-empty to use log-surgeon for compression`

The `clp-s-compress-session` wrapper mirrors that contract: pass both, or pass
neither (the stable path). It is an error to pass one without the other.

For **search**, only `--experimental` is needed — no parsing spec exists at
search time.

## Compress (clpp)

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-compress-session" \
  --session-file ~/.claude/projects/<…>/session.jsonl \
  --experimental --parsing-specification /path/to/parsing-spec.txt \
  --timestamp-key timestamp
```

Notes:
- The parsing spec **must match the structure of the input**. The benchmark
  specs under `./benchmark/parsing-specs/` in a CLP source checkout are tuned
  for the hive dataset, not for Claude/Codex session JSON — supply a
  session-appropriate spec when compressing sessions.
- All stable compress options (see `shared-compress.md`) still apply. The
  wrapper records the full experimental command in the archive metadata JSON.
- On the **installed** core binary, `shape()` as a **filter** works, but
  `shape()`/`decompose()` **projections** are silently dropped — the output has
  no `message.shape` / `message.decompose` field (just the other projected
  columns, or `{}`). To get decomposed/shape *projection* output, point at a
  local clpp build with `--clp-s-bin PATH` (or `CLP_S_BIN`) until the bundled
  binary is republished from the merged clpp tree. Do this only when projection
  output is actually required — it is not the default workflow.

After compression, report the same stats as the stable `compress` skill (raw
input bytes, archive bytes, compression ratio, file size reduction, archives dir,
archive metadata).

## Search (clpp)

Pass `--experimental` to activate the clpp KQL layer. Without it, `shape()` and
`decompose()` are treated as plain text rather than functions. Stable KQL runs
unchanged when the flag is added, so it is safe to leave `--experimental` on for a
clpp archive.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  /tmp/archive 'shape(message): "*error*"'
```

### KQL functions

| Function | Where | Effect |
| --- | --- | --- |
| `shape(column)` | filter | Wildcard-match against the column's log shape text. |
| `shape(column)` | projection | Output the column's shape string. |
| `decompose(column)` | projection | Output the column's decomposed view (shape + leaf values). |

Project the shape string of a column:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --projection 'shape(message)' /tmp/archive '*'
```

Project the decomposed view of a column (shape + extracted leaf values):

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  --projection 'decompose(message)' /tmp/archive '*'
```

Count matches without fetching full records:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/clp-s-search-kql" --experimental \
  /tmp/archive 'shape(message): "*error*"' | grep -c '^{'
```

### Output keys

Projected `shape()` and `decompose()` results nest under the column name:
`{"message":{"shape":"…","<leaf>":[…]}}` (rendered as the dotted keys
`message.shape` / `message.decompose` in flat views). The shape string is the
logtype template (static text + `%var%` placeholders); the leaf arrays are the
variable values bound to each placeholder for that row.

## Tips

- Combine experimental filters with stable KQL and time-range flags:
  `shape(message): "*error*" AND level:error`, plus `--tge`/`--tle` for time
  windows. Time ranges are flags, never KQL (same gotcha as `shared-search.md`).
- Use `--clp-s-bin PATH` (or the `CLP_S_BIN` env var) to run against a local
  clpp-branch build that may be ahead of the installed binary.
- clpp archives require `--experimental` to open. The `clp-s-decompress` wrapper
  does not currently pass it, so decompressing a clpp archive is not yet
  supported via the wrapper — decompress stable archives with the `decompress`
  skill, and handle clpp decompression via a local build until the upstream
  path is fixed.