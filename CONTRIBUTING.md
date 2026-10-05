# Contributing

This repo is the source of truth for the `clp@yscope` plugin payload (the `clp` command, the skills, the manifests). The compiled installer and `clp-s` binary live in a separate private repository.

A useful mental model: **the command is the security boundary, skills are the UX**. The subcommands (and `bin/lib/clp-common.sh`) enforce the flag allowlist, validate paths, and unset dangerous env vars. Skills are prompt instructions that tell the agent which subcommand to call with which arguments. Keep these two layers cleanly separated and changes stay reviewable.

## Repository layout

```text
.claude-plugin/marketplace.json
    Claude Code marketplace manifest.
.agents/plugins/marketplace.json
    Codex marketplace manifest.

plugins/clp/.claude-plugin/plugin.json
plugins/clp/.codex-plugin/plugin.json
    Product-specific plugin manifests. version must match — the
    release workflow runs `scripts/validate-codex-plugin.sh` which
    enforces this.
plugins/clp/bin/clp
    The only command. Reads its first argument as a subcommand, or as the
    TARGET of the analysis router. A tiny entry point; no logic of its own.
plugins/clp/bin/lib/
    commands.py — the subcommand table: every name, its blurb, and the file
    that implements it. The source of truth for the command's surface.
    One implementation per subcommand (search.sh, compress-session.sh,
    schema_tree_cli.py, ...), the shared clp-common.sh, and the internal
    modules the implementations import (kql_build.py, log_shapes.py,
    structurize.py, ...), which are never invoked directly.
plugins/clp/bin/clp-s
    The clp-s engine binary, placed here by the installer and gitignored.
    Not a subcommand; see resolve_clp_s below.
plugins/clp/skills-claude/
    Claude Code skills: compress, compress-folder, search, analyze-logs,
    decompress, clpp-compress, clpp-search, dev,
    plus references/ (shared snippets included by the skills).
plugins/clp/skills-codex/
    Codex skills: compress, compress-folder, search, log-insights,
    decompress, codex-trajectory.

scripts/validate-codex-plugin.sh
    Validates the Codex manifest and SKILL.md frontmatter.
.github/workflows/release.yml
    Builds marketplace.tar.gz on tag push.
```

The repo root is the marketplace root for both products. Claude Code reads `.claude-plugin/marketplace.json`; Codex reads `.agents/plugins/marketplace.json`. Both point at the same `plugins/clp/` directory, but each product reads its own plugin manifest and its own `skills-*` directory.

## Local development loop

The loop is the same shape for both products — install the local marketplace once, then edit the source and reload — but the reload commands differ.

### 1. Install the local marketplace (one-time per clone)

From the repo root:

```bash
claude plugin validate .
claude plugin validate ./plugins/clp
scripts/validate-codex-plugin.sh ./plugins/clp

claude plugin marketplace add "$PWD" --scope user
claude plugin install clp@yscope --scope user

codex plugin marketplace add "$PWD"
codex plugin add clp@yscope
```

The local marketplace points directly at `./plugins/clp`, so file edits in this checkout are the source the agent will read — there is no copy step.

For a single one-off session against the local plugin without registering a marketplace, use `claude --plugin-dir ./plugins/clp`. This is also the fastest loop for tweaking `SKILL.md` files — the plugin root is the checkout itself, so every reload is immediate.

See [LOCAL_TESTING.md](LOCAL_TESTING.md) for the smoke tests (list sessions → compress → search → decompress, dry-run then real) and the `clp-s` resolution rules.

### 2. Edit, ask the agent, reload

Make your change in the relevant file (a subcommand's implementation in `bin/lib/`, a `SKILL.md`, a manifest, or `bin/lib/clp-common.sh`). Then drive the change with the agent itself:

> "Update `plugins/clp/skills-claude/search/SKILL.md` to also pass `--tge 2025-01-01`."

The agent edits the file. You observe the diff. Run the subcommand or skill manually to confirm the behavior matches what you asked for.

#### Reload in Claude Code

The local marketplace source points at the checkout on disk, so any edit under `bin/`, a skill, or a manifest is picked up on reload — there is no copy step. There are three reload paths, in order of cost:

1. **Just restart the session** — fastest, what you'll do 90% of the time.
2. **Update in place** while keeping the session:

   ```bash
   claude plugin update clp@yscope
   ```

This re-reads the marketplace source on disk. Useful when you don't want to lose in-flight context.
3. **Interactive UI**: `/plugin` → pick **Update** for `clp@yscope`. Same effect as the CLI form.

For edits under `bin/` only, you can also run the subcommand directly from the checkout without reloading the skill at all:

```bash
./plugins/clp/bin/clp search /path/to/archive 'level:error'
```

#### Reload in Codex

Codex does not have an in-session reload for installed plugins. To see your changes:

1. Start a new Codex session (new thread). The local marketplace source is re-read on launch, so `bin/`, manifest, and skill edits are all picked up.
2. If the change does not appear, bump the `version` field in `plugins/clp/.codex-plugin/plugin.json` by hand and re-run `codex plugin add clp@yscope` to force a refresh.

#### Asymmetry to be aware of

- Claude skills reach the command as `${CLAUDE_PLUGIN_ROOT}/bin/clp <subcommand>` (resolved at agent startup). Codex skills hard-code the marketplace install path (`~/.codex/marketplaces/yscope/plugins/clp/bin/clp`). When you add a new subcommand, both skill trees need the matching reference.
- Claude has in-session reload (`claude plugin update` or `/plugin` UI); Codex currently needs a new session and (for manifest changes) a `version` bump in the Codex manifest.
- The `version` field must match between `plugins/clp/.claude-plugin/plugin.json` and `plugins/clp/.codex-plugin/plugin.json`. `scripts/validate-codex-plugin.sh` enforces this locally and the release workflow runs it as part of CI, so a tag with version drift will fail the release build.

### 3. Verify

After your edit, run the preflight commands from step 1 again. They are fast and they catch the most common regressions (broken manifest JSON, missing `name`/`description` frontmatter, version drift between the two manifests).

For changes under `bin/`, also run the smoke tests in [LOCAL_TESTING.md](LOCAL_TESTING.md). For semantic-search changes, hit the health-check path by running `clp search` with a query that does not use `semantic("...")` first, then one that does.

### 4. Port and open a PR

If your change was only made in one product's skills tree, port the equivalent change to the other. A change under `bin/` is product-agnostic but the corresponding skill docs in both `skills-claude/` and `skills-codex/` should still be reviewed for wording drift.

Push your branch and open a PR against `main`. The release flow is described in the [Releases](README.md#releases) section of the README — this repo follows [Semantic Versioning](https://semver.org/) for the `clp@yscope` plugin.

## How the subcommands are layered

The table in `bin/lib/commands.py` gives each subcommand a name and a one-line blurb; it says nothing about its role. These five tiers do, and a subcommand belongs in exactly one of them — if it seems to belong in two, it is doing two jobs. Above all five sits the router, `clp <TARGET>`: it identifies the application that wrote the logs and names the next step, and every step it names is one of the subcommands below.

| Tier | What it does | Subcommands |
|---|---|---|
| **Passthrough** | restricted calls into the `clp-s` binary, adding validation and nothing else | `clp search`, `clp compress session`, `clp compress folder`, `clp decompress`, `clp list-sessions`, `clp detect`, `clp compress status` |
| **Derived reading** | computes something the records do not state, from one archive | `clp schema` (fields, type drift, per-field counts), `clp session turns` (turn boundaries and the time split) |
| **Session model** | reconstructs a whole session from its many files, as archives plus a catalog of how they connect | `clp bundle`, `clp bundle-review` |
| **Measurement** | computes every figure a report may quote, so no model does arithmetic | `clp session measure`, `clp facts` |
| **Policy and orchestration** | applies thresholds, or drives a pipeline | `clp session score` + `scoring-scale.json`, `clp bootstrap`, `clp baseline-plan`, `clp focus`, `clp run`, `clp extract`, `clp report`, `clp kql`, `clp shape-cache`, `clp shape-cluster` |

Three things that follow from the tiers, and are easy to get wrong:

**A passthrough may not compute.** If a change would have `clp search` derive, summarise or interpret anything, it belongs in a derived-reading subcommand that calls it. Keeping that boundary is why `clp search` can be trusted as the single query path.

**Measurement may not judge.** `clp session measure` publishes values and never scores them; the thresholds live in `scoring-scale.json` because what counts as acceptable is the customer's policy, not a property of the data. A penalty, a weighting or a rung in Python is a bug, not a shortcut.

**`clp facts` and `clp session measure` are deliberately not merged.** Both write a facts file, and they are not alike: `clp session measure` runs its own checks, while `clp facts` runs none; one reads a query-plan's results, the other a bundle's catalog. What they share is a *contract* — the report writer may quote no figure absent from the facts file — and that contract is worth stating in both places rather than abstracting into a base neither fits.

### Naming

A subcommand name says what it does and nothing about where its code came from: the table in `bin/lib/commands.py` is the whole of the public surface, so the name in it is the only name a user ever sees. Keep them verbs (`compress`, `search`, `extract`) or the thing produced (`schema`, `facts`, `shape-cache`), and keep them free of `/`, `.` and `~` — those three characters are how the router tells a mistyped subcommand from a target.

"shape" survives in `clp shape-cache` and `clp shape-cluster` because the subject genuinely is log shapes — the dictionary, the clustering, the classification cache, and the baseline *method* documented in `references/log-shape-baseline.md`.

Files on disk are not renamed along with the subcommands. `clp extract` still writes `/tmp/clp-insights-query-plan.txt` and `clp session measure` still writes `/tmp/clp-session-facts.md`, because a path is a contract with whatever reads it next — including a run that started before your change. Rename one only if you are changing every reader in the same commit.

## What to change for each kind of edit

- **New flag on an existing subcommand** — add the flag to that implementation's explicit allowlist in `bin/lib/` (and to the skill's `allowed-tools` for Claude skills), then describe it in `plugins/clp/README.md` and the matching `SKILL.md`. No manifest bump unless behavior changed.
- **New subcommand** — add the implementation under `plugins/clp/bin/lib/`, then add its name, blurb and filename to `SUBCOMMANDS` in `bin/lib/commands.py`; nothing else registers it, and a name that is not in that table is an `unknown subcommand` error. A shell implementation sources `bin/lib/clp-common.sh` and follows the restricted-passthrough pattern (no `--` passthrough, explicit allowlist, validated paths, dangerous env vars unset). Group related steps under one name (`Group(...)`, as `compress` and `session` do) rather than adding another top-level name. Update `plugins/clp/README.md`, reference it from the relevant `SKILL.md` in both product skill trees, and bump the `version` field in both `plugin.json` files.
- **New internal module** — a file in `bin/lib/` that no subcommand table entry points at is an internal module, imported by an implementation and never invoked directly (`structurize.py`, `log_shapes.py`, `kql_build.py`). It needs no table entry and no doc beyond its own docstring.
- **New skill** — create a new directory under `plugins/clp/skills-claude/` and/or `plugins/clp/skills-codex/` with a `SKILL.md` that has YAML frontmatter (`name`, `description`). For product-specific use-cases (e.g. a vLLM debugging skill), one product's tree is fine; for the common compress/search/decompress workflow, add to both. Call only `bin/clp` and its documented subcommands; never call bare `clp-s`, and never call a file in `bin/lib/` directly.
- **Skill wording / behavior** — edit the `SKILL.md` directly. Skills are prompt instructions; the subcommands do the actual work. Keep the `allowed-tools` list (Claude) and the command reference (Codex) in sync with the subcommand's actual allowlist.
- **Manifest or marketplace change** — bump the `version` in both `plugin.json` files; update `plugins/clp/README.md` to reflect the new surface. Marketplace manifests (`.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`) rarely need edits — the plugin entries are stable. Add a new plugin entry there only when shipping a genuinely new plugin, not for a new skill in an existing plugin.

## What the commands actually validate

`bin/lib/clp-common.sh` is shared; each subcommand enforces a different slice of the policy. Knowing which check lives where makes error messages actionable instead of mysterious.

| Check | Implemented in | Notes |
| --- | --- | --- |
| `clp-s` resolution (`CLP_S_BIN` → `bin/clp-s` → `.clp-core/bin/clp-s` → `PATH`) | `lib/clp-common.sh::resolve_clp_s` | Every subcommand that touches the engine calls this. Set `CLP_S_BIN` to override. |
| Flag allowlist (every flag explicit; `--` rejected) | Each implementation's argument loop in `bin/lib/` | Adding a flag means editing that file, not the skill. |
| Selected session is under the agent's source root | `clp compress session` | `source_real` containment check; rejects `--session-file` outside `~/.claude/projects` or `~/.codex/sessions`. |
| Output directory is not broad (not `/`, `$HOME`, `~/.claude`, `~/.codex`) | `lib/clp-common.sh::is_broad_output_dir` (called by `clp compress session` and `clp decompress`) | The most common "refused" error — choose an output under `/tmp` or a project-local dir. |
| `archives-root` config (saved default + env override) | `clp compress session` | `--show-archives-root`, `--set-archives-root`, `--save-archives-root`. |
| HTTPS-only semantic endpoint + health check | `lib/clp-common.sh::require_secure_url`, `check_semantic_endpoint` (called by `clp search` only when `semantic("...")` is in the KQL) | The endpoint URL is **not** passed in plaintext; `clp search` derives a `…/health` URL and `curl`s it before running the query. |
| Output parent directory exists | `clp decompress` | Unlike compress, decompress writes to the chosen path rather than creating a timestamped subdir under a saved root. |
| Metadata file (`<archive>/.yscope-clp-archive.json`) | `clp compress session` | Written on success; consumed by `print_archive_metadata_summary` so other subcommands can print `agent`, `session.file`, `sourceRoot`. |

If a subcommand refuses a path, the error message names the check (e.g. "refusing broad output directory", "selected session file must be under source root"). Match the error against the row above to find which knob to adjust or which bug to file.

## Where a change belongs, and keeping the two skill trees in sync

Two judgement calls come up often enough to call out:

**When a change belongs in the subcommand vs the skill.** If the constraint is a security or correctness property (no arbitrary paths, fixed flag set, env var scrubbing), it belongs in the implementation under `bin/lib/`, enforced by code. If the constraint is about how the agent should phrase a query, what defaults to use, or which skill to call from another skill, it belongs in `SKILL.md`. Resist putting "use this flag" instructions in a subcommand's usage string — it should fail closed, not lecture.

**When a new skill is a new directory vs an addition to an existing skill.** A new use-case (e.g. "vLLM debugging", "Sentry error triage") deserves its own top-level directory under `skills-claude/<name>/SKILL.md` and `skills-codex/<name>/SKILL.md`. A new option or a refinement of an existing flow belongs inside the relevant `compress/`, `search/`, `decompress/`, or `*-trajectory/` skill. Don't grow the existing skills indefinitely — if the description is no longer accurate, split.

**Edit-both-products rule.** Almost every skill change should land in both `skills-claude/<name>/SKILL.md` and `skills-codex/<name>/SKILL.md`. There are only three reasons to edit just one:

- The change is genuinely product-specific (e.g. adding a Codex-only KQL starter to `codex-trajectory`).
- The change is to a command reference that already diverges between products (e.g. `${CLAUDE_PLUGIN_ROOT}` vs the Codex hard-coded path).
- The other product's tree doesn't have the skill yet (you're adding the Claude-side skill first because Codex can't see it).

If none of these apply, your PR is incomplete until both trees match.

## Pre-merge sanity

A few things to check before opening a PR, in addition to the preflight commands above:

- `shellcheck` is clean on every shell file in `bin/lib/` you touched, `clp-common.sh` included.
- The two `plugin.json` `version` fields match.
- New `SKILL.md` files have YAML frontmatter with both `name` and `description`.
- New flags appear in the implementation's allowlist in `bin/lib/`, the corresponding `SKILL.md` in **both** product trees, and `plugins/clp/README.md`.
- A new subcommand is in `SUBCOMMANDS` in `bin/lib/commands.py`, and `plugins/clp/bin/clp` with no arguments prints it in the table with a blurb that fits on one line.
- A change that exposes new behavior is reflected in that subcommand's `--help` (or equivalent) text.
- A change to `plugins/clp/scoring-scale.json` passes `plugins/clp/bin/clp session measure --check-scale` (the release workflow runs it too). Every axis needs a `basis` and a `rationale`: a threshold nobody can justify is not a threshold. Moving a rung is a behavior change — bump `scale_version`, since scores from before and after are not comparable.

## Release process

See the [Releases](README.md#releases) section of the README for the version-bump → tag → release workflow and how the private installer consumes the release.

The release CI runs `claude plugin validate` and `scripts/validate-codex-plugin.sh`, which catches manifest drift and broken `SKILL.md` frontmatter. The smoke tests in [LOCAL_TESTING.md](LOCAL_TESTING.md) are not run by CI — run them locally before tagging, especially if the change touched a subcommand's allowlist or added a new flag.

## Getting unstuck

- **"`clp` refused my path"** — check the error against the [What the commands actually validate](#what-the-commands-actually-validate) table above. The most common fix is to choose an output under `/tmp` or a project-local dir.
- **"error: unknown subcommand"** — the name is not in `SUBCOMMANDS` in `bin/lib/commands.py`. Either it is a typo (`clp` prints the whole table when run with no arguments), or you added an implementation to `bin/lib/` without registering it. A first argument that holds a `/`, a `.` or a `~`, names something that exists, or is a session-id UUID is read as the router's TARGET instead, so `clp ./searhc` is a missing file rather than a bad subcommand.
- **"skill changes don't show up in the agent"** — for Claude, run `claude plugin update clp@yscope`; for Codex, start a new session. If the manifest changed, bump the Codex `version` and re-add the plugin.
- **"manifest validates locally but `claude plugin validate` complains about my new skill"** — confirm the skill directory has a `SKILL.md` with YAML frontmatter (`name`, `description`) and that the path matches `skills:` in `plugin.json`.
- **"shellcheck fails on my edit"** — most failures are unquoted variable expansions or missing `[[ -n "$x" ]]` guards. The existing shell implementations in `bin/lib/` are the reference style.
- **`--help`** — every subcommand has one (`clp search --help`), `clp --help` is the router's, and bare `clp` prints the table of all of them. Read them before guessing flag names; the allowlist is the truth, the `SKILL.md` may lag.

## License

By contributing, you agree that your contributions will be licensed under the project's [Apache-2.0](LICENSE) license.
