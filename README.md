# YScope CLP Coding-Agent Plugins

Open-source Claude Code and Codex plugins for compressing, searching, and decompressing coding-agent session log archives with [CLP](https://github.com/y-scope/clp) (Compressed Log Processor).

This repo contains the **plugin payload only**. The compiled installer that ships the `clp-s` binary and the deploy tooling live in a separate private repository.

## Plugins

| Plugin | Surface |
| --- | --- |
| `clp@yscope` | List recent Claude Code and Codex session JSONL files, compress one selected session with `clp-s c --timestamp-key timestamp`, search local CLP archives with KQL (including `semantic("...")` similarity search), and decompress a local CLP archive directory. |

The plugin exposes a curated subset of `clp-s` capabilities. It does not expose full-project compression, reducers, network/file output handlers, results-cache writes, indexing, conversion, remote decompression, metadata sinks, or arbitrary `clp-s` option passthrough. See [`plugins/clp/README.md`](plugins/clp/README.md) for the full API surface.

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
plugins/clp/bin/clp
    The one command the plugin ships: the analysis router, plus every
    subcommand (compress, search, decompress, schema, bundle, report,
    shape-cache, ...).
plugins/clp/bin/lib/
    Every subcommand's implementation, the subcommand table that names
    them (commands.py), and the shared clp-common.sh.
plugins/clp/bin/clp-s
    The clp-s engine binary, placed here by the installer and gitignored.
    Not a command anyone types; the subcommands resolve it.
plugins/clp/skills-claude/
    Claude Code skills: compress, compress-folder, search, analyze-logs,
    decompress.
plugins/clp/skills-codex/
    Codex skills: compress, compress-folder, search, log-insights,
    decompress, codex-trajectory.

scripts/validate-codex-plugin.sh
    Validates the Codex plugin manifest and SKILL.md frontmatter.

.github/workflows/release.yml
    Builds marketplace.tar.gz and plugin-release.json on tag push.

LICENSE
    Apache-2.0.
```

The repository root is the marketplace root for both products. Claude Code reads `.claude-plugin/marketplace.json`; Codex reads `.agents/plugins/marketplace.json`. Both marketplace manifests point to the same plugin directory, `./plugins/clp`, while each product reads its own plugin manifest and skill directory.

## Install

End users install via the compiled installer at `https://installer.yscope.ai/coding-agent-plugin.sh`. The installer and deploy tooling live in the private repository [`y-scope/coding-agent-integration-installer`](https://github.com/y-scope/coding-agent-integration-installer); see its `DEPLOY.md` for build and deploy instructions.

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

## The `clp` command

`bin/clp` is the only command in the plugin. Its first argument is either a subcommand or a target to analyse:

```bash
./plugins/clp/bin/clp /var/log/vllm        # analyse this log file, folder, archive, bundle or session id
./plugins/clp/bin/clp compress session     # one step of that flow, on its own
./plugins/clp/bin/clp                      # usage plus the full subcommand table
```

The steps of the compress/search/decompress workflow are:

- `bin/clp list-sessions`
- `bin/clp compress session`
- `bin/clp compress folder`
- `bin/clp search`
- `bin/clp decompress`

These resolve the engine binary in order: `CLP_S_BIN`, then plugin-local `bin/clp-s`, then plugin-local `.clp-core/bin/clp-s`, then `clp-s` on `PATH`. See [`plugins/clp/README.md`](plugins/clp/README.md) for the full contract, flag allowlist, and example commands.

## Semantic search

`clp search` supports `semantic("query")` in KQL for natural-language similarity search. It requires an embedding server that is **already running** — the plugin never starts one (no Docker, no local model download). `clp search` health-checks the endpoint before running a semantic search; if it is unavailable, the search fails with a clear error.

Endpoint resolution, highest precedence first:

1. `--semantic-endpoint URL` (inline)
2. `CLP_SEMANTIC_ENDPOINT`
3. the `semantic-endpoint` config file — `~/.config/yscope-clp-plugin/semantic-endpoint`, one URL per line, blank lines and `#comments` ignored (override the path with `CLP_SEMANTIC_ENDPOINT_FILE`)
4. the built-in remote endpoint — `https://ca-central-semantic-cache.yscope.ai`, used if it passes the health check

An endpoint named by 1–3 that fails its health check is a hard error — `clp search` will not silently fall back to a different host. A server you host yourself (including one on `localhost`) must be named explicitly; it is not auto-detected.

```bash
echo 'https://embeddings.internal.example.com' \
  > ~/.config/yscope-clp-plugin/semantic-endpoint
```

The same endpoint drives `clp shape-cluster`, which embeds log shapes through the server's `/v1/embeddings` endpoint.

Other semantic flags: `--semantic-top-k K`, `--semantic-threshold T`, `--embedding-batch-size N`.

```bash
./plugins/clp/bin/clp search /tmp/session-archive \
  'semantic("slow database queries") AND level:error'
```

## Contributing

This repo is the source of truth for the plugin payload. The `clp` command and its subcommands, the skills, the plugin manifests, and the marketplace manifests are all open for contribution.

For the full contributor guide — local dev loop (install → edit → ask the agent → reload), Claude Code vs Codex reload asymmetry, where to put a change (subcommand vs skill), and the pre-merge sanity checklist — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Releases

This repo follows [Semantic Versioning](https://semver.org/) for the `clp@yscope` plugin. To cut a release:

1. Bump the `version` field in both plugin manifests (must match). Bump the version in `plugins/clp/.claude-plugin/plugin.json` and `plugins/clp/.codex-plugin/plugin.json` — they must stay in sync.
2. Update the relevant `SKILL.md` for any behavior change:
   - KQL/semantic syntax or `clp search` flag changes → common `search/SKILL.md`
   - Session-log workflow or agent schema changes → `analyze-logs/` or `codex-trajectory/`
   - Compress/decompress flag changes → corresponding `compress/` or `decompress/` skill
3. Update `plugins/clp/README.md` if the API surface changed.
4. Tag the commit (`git tag vX.Y.Z`) and push the tag.
5. The release workflow (`.github/workflows/release.yml`) builds `marketplace.tar.gz`, computes its SHA-256, and attaches both to the GitHub release.
6. The private installer repo consumes the release via `scripts/deploy-installer.sh --plugin-tag vX.Y.Z`.

## License

Apache-2.0. See [LICENSE](LICENSE).
