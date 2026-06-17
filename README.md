# YScope CLP Coding-Agent Plugins

Open-source Claude Code and Codex plugins for compressing, searching, and
decompressing coding-agent session log archives with
[CLP](https://github.com/y-scope/clp) (Compressed Log Processor).

This repo contains the **plugin payload only**. The compiled installer that
ships the `clp-s` binary and the deploy tooling live in a separate private
repository.

## Plugins

| Plugin | Surface |
| --- | --- |
| `clp@yscope` | List recent Claude Code and Codex session JSONL files, compress one selected session with `clp-s c --timestamp-key timestamp`, search local CLP archives with KQL (including `semantic("...")` similarity search), and decompress a local CLP archive directory. |

The plugin exposes a curated subset of `clp-s` capabilities. It does not
expose full-project compression, reducers, network/file output handlers,
results-cache writes, indexing, conversion, remote decompression, metadata
sinks, or arbitrary `clp-s` option passthrough. See
[`plugins/clp/README.md`](plugins/clp/README.md) for the full API surface.

## Repository layout

```text
.claude-plugin/marketplace.json
    Claude Code marketplace manifest.

.agents/plugins/marketplace.json
    Codex marketplace manifest.

plugins/clp/
    Shared plugin root for clp@yscope.
plugins/clp/.claude-plugin/plugin.json
    Claude Code plugin manifest.
plugins/clp/.codex-plugin/plugin.json
    Codex plugin manifest.
plugins/clp/bin/
    Restricted-passthrough bash wrappers for clp-s.
plugins/clp/skills-claude/
    Claude Code skills: compress, search, decompress, claude-code-trajectory.
plugins/clp/skills-codex/
    Codex skills: compress, search, decompress, codex-trajectory.

scripts/validate-codex-plugin.sh
    Validates the Codex plugin manifest and SKILL.md frontmatter.

.github/workflows/release.yml
    Builds marketplace.tar.gz and plugin-release.json on tag push.

LICENSE
    Apache-2.0.
```

The repository root is the marketplace root for both products. Claude Code
reads `.claude-plugin/marketplace.json`; Codex reads
`.agents/plugins/marketplace.json`. Both marketplace manifests point to the
same plugin directory, `./plugins/clp`, while each product reads its own
plugin manifest and skill directory.

## Install

End users install via the compiled installer at
`https://installer.yscope.ai/coding-agent-plugin.sh`. The installer and
deploy tooling live in the private repository
[`y-scope/coding-agent-integration-installer`](https://github.com/y-scope/coding-agent-integration-installer);
see its `DEPLOY.md` for build and deploy instructions.

### Local install for development

From the repository root:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp

claude plugin marketplace add "$PWD" --scope user
claude plugin install clp@yscope --scope user

codex plugin marketplace add "$PWD"
codex plugin add clp@yscope
```

Or launch a single Claude session against the local plugin:

```bash
claude --plugin-dir ./plugins/clp
```

See [LOCAL_TESTING.md](LOCAL_TESTING.md) for more on local testing.

## Wrappers

- `bin/clp-s-list-sessions`
- `bin/clp-s-compress-session`
- `bin/clp-s-search-kql`
- `bin/clp-s-decompress`

The wrappers prefer `CLP_S_BIN`, then plugin-local `bin/clp-s`, then
plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`. See
[`plugins/clp/README.md`](plugins/clp/README.md) for the full wrapper contract,
flag allowlist, and example commands.

## Semantic search

`clp-s-search-kql` supports `semantic("query")` in KQL for natural-language
similarity search via [embedding.yscope.ai](https://embedding.yscope.ai).
The wrapper health-checks the embedding service before running a semantic
search; if the service is unavailable, the search fails with a clear error.

Default endpoint: `https://embedding.yscope.ai/v1/similarity`. Override with
`--semantic-endpoint URL` or set `CLP_SEMANTIC_ENDPOINT`. Other semantic flags:
`--semantic-top-k K`, `--semantic-threshold T`, `--embedding-batch-size N`.

```bash
./plugins/clp/bin/clp-s-search-kql /tmp/session-archive \
  'semantic("slow database queries") AND level:error'
```

## Contributing

This repo is the source of truth for the plugin payload. Wrappers, skills,
plugin manifests, and marketplace manifests are all open for contribution.

- **Adding a wrapper** — place a script in `plugins/clp/bin/`, follow the
  existing restricted-passthrough pattern (explicit flag allowlist, no `--`
  passthrough, validate paths, unset dangerous env vars, source
  `bin/lib/clp-common.sh` for shared utilities). Add the wrapper to
  `plugins/clp/README.md` and the relevant `SKILL.md`.
- **Adding a skill** — create a new directory under
  `plugins/clp/skills-claude/` or `plugins/clp/skills-codex/` with a
  `SKILL.md` that has YAML frontmatter (`name`, `description`). For new
  use-cases (e.g. vLLM debugging), create a top-level directory like
  `plugins/clp/skills-claude/vllm-debugging/` rather than adding to an
  existing skill. Reference only the existing wrappers; never call `clp-s`
  directly.
- **Updating plugin metadata** — bump the `version` field in both
  `plugins/clp/.claude-plugin/plugin.json` and
  `plugins/clp/.codex-plugin/plugin.json` (they must match). Update
  `plugins/clp/README.md` and the skill files to reflect new behavior.

Before opening a PR, run:

```bash
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp
```

## Releases

This repo follows [Semantic Versioning](https://semver.org/) for the
`clp@yscope` plugin. To cut a release:

1. Bump the `version` field in both plugin manifests (must match). Bump the
   version in `plugins/clp/.claude-plugin/plugin.json` and
   `plugins/clp/.codex-plugin/plugin.json` — they must stay in sync.
2. Update the relevant `SKILL.md` for any behavior change:
   - KQL/semantic syntax or wrapper flag changes → common `search/SKILL.md`
   - Session-log workflow or agent schema changes → `claude-code-trajectory/`
     or `codex-trajectory/`
   - Compress/decompress flag changes → corresponding `compress/` or
     `decompress/` skill
3. Update `plugins/clp/README.md` if the API surface changed.
4. Tag the commit (`git tag vX.Y.Z`) and push the tag.
5. The release workflow (`.github/workflows/release.yml`) builds
   `marketplace.tar.gz`, computes its SHA-256, and attaches both to the
   GitHub release.
6. The private installer repo consumes the release via
   `scripts/deploy-installer.sh --plugin-tag vX.Y.Z`.

## License

Apache-2.0. See [LICENSE](LICENSE).
