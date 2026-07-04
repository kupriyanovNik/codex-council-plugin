#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
MARKETPLACE_ROOT = ROOT.parents[1]
MARKETPLACE_PATH = MARKETPLACE_ROOT / ".agents" / "plugins" / "marketplace.json"


REQUIRED_SKILLS = {
    "council-run",
    "council-review",
    "council-design",
    "council-implement",
}


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("ok:", message)


def load_json(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_manifest() -> None:
    manifest = load_json(ROOT / ".codex-plugin" / "plugin.json")
    check(manifest["name"] == "codex-council", "plugin name is codex-council")
    check(manifest["skills"] == "./skills/", "plugin points at bundled skills")
    check(manifest["mcpServers"] == "./.mcp.json", "plugin points at bundled MCP config")
    default_prompt = manifest["interface"]["defaultPrompt"]
    check(isinstance(default_prompt, list) and 1 <= len(default_prompt) <= 3, "defaultPrompt is a short list")


def validate_mcp_config() -> None:
    mcp = load_json(ROOT / ".mcp.json")
    server = mcp["mcpServers"]["codex-council"]
    check(server["command"] == "python3", "MCP server uses python3")
    check(server["args"] == ["./mcp/council_server.py", "--stdio"], "MCP server starts council_server.py over stdio")
    check((ROOT / "mcp" / "council_server.py").exists(), "MCP server file exists")


def validate_marketplace() -> None:
    check(MARKETPLACE_PATH.exists(), "marketplace manifest exists in dedicated plugin root")
    marketplace = load_json(MARKETPLACE_PATH)
    entries = [entry for entry in marketplace["plugins"] if entry["name"] == "codex-council"]
    check(len(entries) == 1, "marketplace has one codex-council entry")
    source_path = entries[0]["source"]["path"]
    check(source_path == "./plugins/codex-council", "marketplace source path is relative to dedicated root")
    check((MARKETPLACE_ROOT / "plugins" / "codex-council").resolve() == ROOT.resolve(), "marketplace source resolves to this plugin")


def validate_skills() -> None:
    skills_dir = ROOT / "skills"
    found = {path.parent.name for path in skills_dir.glob("*/SKILL.md")}
    check(REQUIRED_SKILLS.issubset(found), "all required skills exist")
    for skill in REQUIRED_SKILLS:
        text = (skills_dir / skill / "SKILL.md").read_text(encoding="utf-8")
        check(text.startswith("---\n"), f"{skill} has YAML frontmatter")
        check(f"name: {skill}" in text, f"{skill} frontmatter name matches folder")
        check("description:" in text, f"{skill} has description")


def validate_clean_tree() -> None:
    banned = []
    for path in MARKETPLACE_ROOT.rglob("*"):
        if path.name in {".DS_Store", "__pycache__"}:
            banned.append(path)
    check(not banned, "plugin root has no .DS_Store or __pycache__ files")


def cleanup_python_cache() -> None:
    for path in ROOT.rglob("__pycache__"):
        shutil.rmtree(path)
    for path in ROOT.rglob("*.pyc"):
        path.unlink()


def main() -> int:
    cleanup_python_cache()
    validate_manifest()
    validate_mcp_config()
    validate_marketplace()
    validate_skills()
    validate_clean_tree()
    run([sys.executable, "mcp/council_server.py", "--self-test"])
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    cleanup_python_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
