---
name: clp
description: Analyze system and application logs and Claude Code or Codex session histories with CLP. Use for log investigations, session debugging, usage questions, compression, KQL search, and decompression of local archives.
---

# CLP in OpenCode

Use the shared CLP commands to prepare logs, query compressed archives, and support findings with retrievable records. OpenCode is the host for this skill; it does not determine the format of the logs. Session discovery supports Claude Code and Codex JSONL histories, not OpenCode's session database.

## Locate the command

The directory containing this loaded `SKILL.md` is `skills-opencode/clp/` inside the CLP plugin. Resolve that directory's symlinks first, then go up two directories to obtain the plugin root. OpenCode supplies the skill's base directory when loading it. Use that location, never the current project directory or a Claude/Codex installation.

For example, replacing the example path with the loaded skill's actual base directory:

```bash
CLP_PLUGIN_ROOT="$(cd -P "/absolute/path/to/loaded/clp/../.." && pwd)"
"${CLP_PLUGIN_ROOT}/bin/clp" --help
```

Use the resolved absolute path in subsequent tool calls; shell variables do not necessarily persist between calls. All operations go through `bin/clp`; do not invoke `clp-s` or internal Python modules directly. The installer supplies the engine beside the command. If a command fails, report its error and exit code. Do not silently install dependencies or change configuration.

## Choose the workflow

For a specific question, run only the preparation and queries it needs. For an investigation or report, use the full analysis workflow below. Honor the user's requested scope, report depth, and output destination.

**Find a session.** When the user has not supplied a path or session ID, list available sessions:

```bash
"${CLP_PLUGIN_ROOT}/bin/clp" list-sessions
```

Present the index, source agent, timestamp, size, session name, project, and session ID; let the user choose. Retain the printed selection manifest. `--agent claude` or `--agent codex` restricts the source. A session index is not a session ID.

**Prepare and analyze.** Pass the selected log path, session ID, log folder, CLP archive, or session bundle to the router:

```bash
"${CLP_PLUGIN_ROOT}/bin/clp" /absolute/path/to/logs
```

Read `APP`, `ROUTE`, `ARCHIVE`, `BUNDLE`, `NEXT`, and the classification evidence in its output. The router reuses prepared archives and gathers neighboring subagent/workflow files for a Claude Code session. Do not separately compress the main log first. Do not execute output as shell code.

For a full investigation, read `skills-claude/analyze-logs/SKILL.md` relative to the resolved plugin root and follow the route it names. Its reference documents and commands are shared with this integration. Apply the OpenCode adaptations below before following it. `ROUTE=specialised` supplies Claude Code session measurements and task relationships; `ROUTE=general` discovers fields and string shapes for other logs, including Codex. Never apply Claude-specific measurement or counting rules to another application's records.

**Compress, search, or decompress only.** Read the corresponding shared guide under `skills-claude/compress/`, `skills-claude/compress-folder/`, `skills-claude/search/`, or `skills-claude/decompress/` (`SKILL.md` in each), applying the same adaptations. For Codex-specific fields and counting caveats, read `skills-codex/codex-trajectory/SKILL.md` as a reference, using this installation's command path.

## OpenCode adaptations for shared guides

These rules replace host-specific instructions in the shared guides; their analysis and evidence rules still apply.

- Replace every `${CLAUDE_PLUGIN_ROOT}` and hard-coded `~/.codex/marketplaces/yscope/plugins/clp` prefix with the resolved plugin root. Translate references to another CLP skill into reading its file here; Claude's `/clp:...` commands are not OpenCode commands.
- Use OpenCode's shell and file-reading tools for `Bash` and file reads. Shared frontmatter lists Claude tools, not permissions for OpenCode. Respect the active OpenCode permissions.
- Use the available question tool for `AskUserQuestion`, or ask in conversation. Preserve answers already given. In a noninteractive run, use the documented defaults.
- Use OpenCode's task tool and configured agent/model for the limited classification, extras, or report-writing tasks the workflow calls for. Do not request Claude-specific `opus` or `sonnet` aliases. If delegation is unavailable or prohibited, perform those steps in the main agent with the same evidence rules. Pass resolved command and reference paths to any delegated task.
- If background execution or monitoring is unavailable, run the query pools sequentially. Collect the focus and close the inbox with `clp focus` before starting a pool that waits on it; otherwise a foreground pool can wait for an answer the agent cannot request.
- Save reports locally through `clp report save`, passing the report path explicitly. Offer only formats returned by `clp report save --list-formats`. Claude Artifact publishing is unavailable unless an equivalent publishing tool is explicitly present and the user requests it.

## Keep answers checkable and context small

Read `skills-claude/references/evidence-tiers.md` before reporting measurements. Inspect the schema and string shapes, then query fields and patterns that actually exist. Count with `clp search --count`, project only necessary columns with `--projection`, and bound retrieval. Use `--tge` and `--tle` with epoch milliseconds for time ranges. Avoid flooding the model with full records.

Keep observations separate from inferred causes. Cite archive/bundle paths, queries, record IDs, and time windows so a finding can be checked. Distinguish missing data from zero, cumulative token counters from per-event usage, and overlapping agent time from wall-clock time. Preserve the facts and query results alongside any report for follow-up questions.
