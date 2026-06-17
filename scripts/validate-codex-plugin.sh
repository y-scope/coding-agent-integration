#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="${1:-./plugins/clp}"

python3 - "$PLUGIN_DIR" <<'PY'
import json
import sys
from pathlib import Path

plugin_dir = Path(sys.argv[1]).resolve()
plugin_json_path = plugin_dir / ".codex-plugin" / "plugin.json"
marketplace_root = plugin_dir.parent.parent
marketplace_json_path = marketplace_root / ".agents" / "plugins" / "marketplace.json"

def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)

def read_json(path: Path) -> dict:
    if not path.is_file():
        fail(f"missing {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            value = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"{path} is invalid JSON: {e}")
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return value

plugin = read_json(plugin_json_path)
marketplace = read_json(marketplace_json_path)

name = plugin.get("name")
if not isinstance(name, str) or not name:
    fail("plugin.json must include a non-empty name")
if not isinstance(plugin.get("version"), str) or not plugin["version"]:
    fail("plugin.json must include a non-empty version")
if not isinstance(plugin.get("description"), str) or not plugin["description"]:
    fail("plugin.json must include a non-empty description")

skills = plugin.get("skills")
if skills:
    skills_dir = (plugin_dir / skills).resolve()
    if not skills_dir.is_dir():
        fail(f"skills directory does not exist: {skills_dir}")
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    if not skill_files:
        fail(f"skills directory contains no */SKILL.md files: {skills_dir}")
    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            fail(f"{skill_file} is missing YAML frontmatter")
        header = text.split("\n---\n", 1)[0]
        if "\nname:" not in f"\n{header}" or "\ndescription:" not in f"\n{header}":
            fail(f"{skill_file} frontmatter must include name and description")

if marketplace.get("name") != "yscope":
    fail("marketplace.json name must be yscope")

plugins = marketplace.get("plugins")
if not isinstance(plugins, list):
    fail("marketplace.json must include a plugins array")

entry = next((item for item in plugins if isinstance(item, dict) and item.get("name") == name), None)
if entry is None:
    fail(f"marketplace.json does not list plugin {name!r}")

source = entry.get("source")
if not isinstance(source, dict) or source.get("source") != "local":
    fail(f"marketplace entry for {name!r} must use local source")
source_path = source.get("path")
if not isinstance(source_path, str) or not source_path:
    fail(f"marketplace entry for {name!r} must include source.path")
if not (marketplace_root / source_path).resolve().is_dir():
    fail(f"marketplace source.path does not resolve to a directory: {source_path}")

claude_plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
if claude_plugin_json_path.is_file():
    claude_plugin = read_json(claude_plugin_json_path)
    claude_version = claude_plugin.get("version")
    if not isinstance(claude_version, str) or not claude_version:
        fail(f"{claude_plugin_json_path} must include a non-empty version")
    if plugin.get("version") != claude_version:
        fail(
            f"Claude and Codex plugin versions must match: "
            f"Claude={claude_version!r}, Codex={plugin['version']!r}"
        )

print(f"Codex plugin validation passed: {plugin_dir}")
PY
