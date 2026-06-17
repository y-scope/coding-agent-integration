# Contributing

This repo is the source of truth for the `clp@yscope` plugin payload (wrappers,
skills, manifests). The compiled installer and `clp-s` binary live in a separate
private repository.

A useful mental model: **wrappers are the security boundary, skills are the UX**.
Wrappers (and `bin/lib/clp-common.sh`) enforce the flag allowlist, validate
paths, and unset dangerous env vars. Skills are prompt instructions that tell
the agent which wrapper to call with which arguments. Keep these two layers
cleanly separated and changes stay reviewable.

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
plugins/clp/bin/
    Restricted-passthrough bash wrappers (clp-s-*) and shared lib/clp-common.sh.
plugins/clp/skills-claude/
    Claude Code skills: compress, search, decompress, claude-code-trajectory.
plugins/clp/skills-codex/
    Codex skills: compress, search, decompress, codex-trajectory.

scripts/validate-codex-plugin.sh
    Validates the Codex manifest and SKILL.md frontmatter.
.github/workflows/release.yml
    Builds marketplace.tar.gz on tag push.
```

The repo root is the marketplace root for both products. Claude Code reads
`.claude-plugin/marketplace.json`; Codex reads `.agents/plugins/marketplace.json`.
Both point at the same `plugins/clp/` directory, but each product reads its own
plugin manifest and its own `skills-*` directory.

## Local development loop

The loop is the same shape for both products — install the local marketplace
once, then edit the source and reload — but the reload commands differ.

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

The local marketplace points directly at `./plugins/clp`, so file edits in
this checkout are the source the agent will read — there is no copy step.

For a single one-off session against the local plugin without registering a
marketplace, use `claude --plugin-dir ./plugins/clp`. This is also the
fastest loop for tweaking `SKILL.md` files — the plugin root is the
checkout itself, so every reload is immediate.

See [LOCAL_TESTING.md](LOCAL_TESTING.md) for wrapper smoke tests (list
sessions → compress → search → decompress, dry-run then real) and the
`clp-s` resolution rules.

### 2. Edit, ask the agent, reload

Make your change in the relevant file (a wrapper, a `SKILL.md`, a manifest,
or `bin/lib/clp-common.sh`). Then drive the change with the agent itself:

> "Update `plugins/clp/skills-claude/search/SKILL.md` to also pass
> `--tge 2025-01-01`."

The agent edits the file. You observe the diff. Run the wrapper or skill
manually to confirm the behavior matches what you asked for.

#### Reload in Claude Code

The local marketplace source points at the checkout on disk, so any
wrapper, skill, or manifest edit is picked up on reload — there is no
copy step. There are three reload paths, in order of cost:

1. **Just restart the session** — fastest, what you'll do 90% of the time.
2. **Update in place** while keeping the session:

   ```bash
   claude plugin update clp@yscope
   ```

   This re-reads the marketplace source on disk. Useful when you don't
   want to lose in-flight context.
3. **Interactive UI**: `/plugin` → pick **Update** for `clp@yscope`. Same
   effect as the CLI form.

For wrapper-only edits, you can also run the wrapper directly from the
checkout without reloading the skill at all:

```bash
./plugins/clp/bin/clp-s-search-kql /path/to/archive 'level:error'
```

#### Reload in Codex

Codex does not have an in-session reload for installed plugins. To see
your changes:

1. Start a new Codex session (new thread). The local marketplace source is
   re-read on launch, so all wrapper, manifest, and skill edits are picked
   up.
2. If the change does not appear, bump the `version` field in
   `plugins/clp/.codex-plugin/plugin.json` by hand and re-run
   `codex plugin add clp@yscope` to force a refresh.

#### Asymmetry to be aware of

- Claude skills reference wrappers via `${CLAUDE_PLUGIN_ROOT}/bin/...`
  (resolved at agent startup). Codex skills hard-code the marketplace
  install path (`~/.codex/marketplaces/yscope/plugins/clp`). When you add
  a new wrapper, both skill trees need the matching reference.
- Claude has in-session reload (`claude plugin update` or `/plugin` UI);
  Codex currently needs a new session and (for manifest changes) a
  `version` bump in the Codex manifest.
- The `version` field must match between
  `plugins/clp/.claude-plugin/plugin.json` and
  `plugins/clp/.codex-plugin/plugin.json`. `scripts/validate-codex-plugin.sh`
  enforces this locally and the release workflow runs it as part of CI,
  so a tag with version drift will fail the release build.

### 3. Verify

After your edit, run the preflight commands from step 1 again. They are
fast and they catch the most common regressions (broken manifest JSON,
missing `name`/`description` frontmatter, version drift between the two
manifests).

For wrapper changes, also run the smoke tests in
[LOCAL_TESTING.md](LOCAL_TESTING.md). For semantic-search changes, hit
the health-check path by running the wrapper with a query that does not
use `semantic("...")` first, then one that does.

### 4. Port and open a PR

If your change was only made in one product's skills tree, port the
equivalent change to the other. A change to `bin/` or
`bin/lib/clp-common.sh` is product-agnostic but the corresponding skill
docs in both `skills-claude/` and `skills-codex/` should still be
reviewed for wording drift.

Push your branch and open a PR against `main`. The release flow is
described in the [Releases](README.md#releases) section of the README —
this repo follows [Semantic Versioning](https://semver.org/) for the
`clp@yscope` plugin.

## What to change for each kind of edit

- **New wrapper flag** — add the flag to the wrapper's explicit allowlist
  (and to the skill's `allowed-tools` for Claude skills), then describe
  the new flag in `plugins/clp/README.md` and the matching
  `SKILL.md`. No manifest bump unless behavior changed.
- **New wrapper** — add a script under `plugins/clp/bin/`, source
  `bin/lib/clp-common.sh`, and follow the restricted-passthrough pattern
  (no `--` passthrough, explicit allowlist, validated paths, dangerous env
  vars unset). Update `plugins/clp/README.md` and reference the wrapper
  from the relevant `SKILL.md` in both product skill trees. Bump the
  `version` field in both `plugin.json` files.
- **New skill** — create a new directory under
  `plugins/clp/skills-claude/` and/or `plugins/clp/skills-codex/` with a
  `SKILL.md` that has YAML frontmatter (`name`, `description`). For
  product-specific use-cases (e.g. a vLLM debugging skill), one
  product's tree is fine; for the common compress/search/decompress
  workflow, add to both. Reference only the existing wrappers; never
  call bare `clp-s`.
- **Skill wording / behavior** — edit the `SKILL.md` directly. Skills
  are prompt instructions; the wrappers do the actual work. Keep the
  `allowed-tools` list (Claude) and the wrapper reference (Codex) in
  sync with the wrapper's actual allowlist.
- **Manifest or marketplace change** — bump the `version` in both
  `plugin.json` files; update `plugins/clp/README.md` to reflect the new
  surface. Marketplace manifests (`.claude-plugin/marketplace.json` and
  `.agents/plugins/marketplace.json`) rarely need edits — the plugin
  entries are stable. Add a new plugin entry there only when shipping a
  genuinely new plugin, not for a new skill in an existing plugin.

## What the wrappers actually validate

`bin/lib/clp-common.sh` is shared; each wrapper enforces a different slice
of the policy. Knowing which check lives where makes error messages
actionable instead of mysterious.

| Check | Implemented in | Notes |
| --- | --- | --- |
| `clp-s` resolution (`CLP_S_BIN` → `bin/clp-s` → `.clp-core/bin/clp-s` → `PATH`) | `lib/clp-common.sh::resolve_clp_s` | All wrappers call this. Set `CLP_S_BIN` to override. |
| Flag allowlist (every flag explicit; `--` rejected) | Each wrapper's argument loop | Adding a flag means editing the wrapper, not the skill. |
| Selected session is under the agent's source root | `clp-s-compress-session` | `source_real` containment check; rejects `--session-file` outside `~/.claude/projects` or `~/.codex/sessions`. |
| Output directory is not broad (not `/`, `$HOME`, `~/.claude`, `~/.codex`) | `lib/clp-common.sh::is_broad_output_dir` (called by `clp-s-compress-session` and `clp-s-decompress`) | The most common "refused" error — choose an output under `/tmp` or a project-local dir. |
| `archives-root` config (saved default + env override) | `clp-s-compress-session` | `--show-archives-root`, `--set-archives-root`, `--save-archives-root`. |
| HTTPS-only semantic endpoint + health check | `lib/clp-common.sh::require_secure_url`, `check_semantic_endpoint` (called by `clp-s-search-kql` only when `semantic("...")` is in the KQL) | The endpoint URL is **not** passed in plaintext; the wrapper derives a `…/health` URL and `curl`s it before running the query. |
| Output parent directory exists | `clp-s-decompress` | Unlike compress, decompress writes to the chosen path rather than creating a timestamped subdir under a saved root. |
| Metadata file (`<archive>/.yscope-clp-archive.json`) | `clp-s-compress-session` | Written on success; consumed by `print_archive_metadata_summary` so other wrappers can print `agent`, `session.file`, `sourceRoot`. |

If a wrapper refuses a path, the error message names the check (e.g.
"refusing broad output directory", "selected session file must be under
source root"). Match the error against the row above to find which knob to
adjust or which bug to file.

## Skill vs wrapper-vs-skill sync

Two judgement calls come up often enough to call out:

**When a change belongs in the wrapper vs the skill.** If the constraint
is a security or correctness property (no arbitrary paths, fixed flag
set, env var scrubbing), it belongs in the wrapper, enforced by code.
If the constraint is about how the agent should phrase a query, what
defaults to use, or which skill to call from another skill, it belongs
in `SKILL.md`. Resist putting "use this flag" instructions in the
wrapper's usage string — wrappers should fail closed, not lecture.

**When a new skill is a new directory vs an addition to an existing
skill.** A new use-case (e.g. "vLLM debugging", "Sentry error
triage") deserves its own top-level directory under
`skills-claude/<name>/SKILL.md` and `skills-codex/<name>/SKILL.md`.
A new option or a refinement of an existing flow belongs inside the
relevant `compress/`, `search/`, `decompress/`, or `*-trajectory/`
skill. Don't grow the existing skills indefinitely — if the
description is no longer accurate, split.

**Edit-both-products rule.** Almost every skill change should land in
both `skills-claude/<name>/SKILL.md` and `skills-codex/<name>/SKILL.md`.
There are only three reasons to edit just one:

- The change is genuinely product-specific (e.g. adding a Codex-only KQL
  starter to `codex-trajectory`).
- The change is to a wrapper reference that already diverges between
  products (e.g. `${CLAUDE_PLUGIN_ROOT}` vs the Codex hard-coded path).
- The other product's tree doesn't have the skill yet (you're adding the
  Claude-side skill first because Codex can't see it).

If none of these apply, your PR is incomplete until both trees match.

## Pre-merge sanity

A few things to check before opening a PR, in addition to the preflight
commands above:

- `shellcheck` is clean on every wrapper you touched and on
  `bin/lib/clp-common.sh`.
- The two `plugin.json` `version` fields match.
- New `SKILL.md` files have YAML frontmatter with both `name` and
  `description`.
- New wrapper flags appear in the wrapper's allowlist, the
  corresponding `SKILL.md` in **both** product trees, and
  `plugins/clp/README.md`.
- A wrapper change that exposes new behavior is reflected in the
  wrapper's `--help` (or equivalent) text.

## Release process

See the [Releases](README.md#releases) section of the README for the
version-bump → tag → release workflow and how the private installer
consumes the release.

The release CI runs `claude plugin validate` and
`scripts/validate-codex-plugin.sh`, which catches manifest drift and
broken `SKILL.md` frontmatter. The smoke tests in
[LOCAL_TESTING.md](LOCAL_TESTING.md) are not run by CI — run them
locally before tagging, especially if the change touched a wrapper
allowlist or added a new flag.

## Getting unstuck

- **"wrapper refused my path"** — check the error against the
  [What the wrappers actually validate](#what-the-wrappers-actually-validate)
  table above. The most common fix is to choose an output under
  `/tmp` or a project-local dir.
- **"skill changes don't show up in the agent"** — for Claude, run
  `claude plugin update clp@yscope`; for Codex, start a new session. If
  the manifest changed, bump the Codex `version` and re-add the plugin.
- **"manifest validates locally but `claude plugin validate` complains
  about my new skill"** — confirm the skill directory has a `SKILL.md`
  with YAML frontmatter (`name`, `description`) and that the path
  matches `skills:` in `plugin.json`.
- **"shellcheck fails on my edit"** — most wrapper failures are
  unquoted variable expansions or missing `[[ -n "$x" ]]` guards. The
  existing wrappers in `bin/` are the reference style.
- **`--help` for a wrapper** — every wrapper has one. Read it before
  guessing flag names; the allowlist is the truth, the `SKILL.md` may
  lag.

## License

By contributing, you agree that your contributions will be licensed
under the project's [Apache-2.0](LICENSE) license.
