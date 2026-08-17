"""Executable contracts for the provider-neutral CLI and plugin adapters."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "transcript-archive"


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_python_module_runs_the_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "claude_transcript_archive", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Generate repository-local AI session transcripts" in result.stdout


def test_claude_codex_and_antigravity_expose_one_skill_tree() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    claude = _json(REPO_ROOT / ".claude-plugin" / "plugin.json")
    codex = _json(REPO_ROOT / ".codex-plugin" / "plugin.json")
    antigravity = _json(REPO_ROOT / "plugin.json")
    marketplace = _json(REPO_ROOT / ".agents" / "plugins" / "marketplace.json")

    assert project["version"] == claude["version"] == codex["version"] == "1.0.0"
    assert claude["name"] == codex["name"] == antigravity["name"] == PLUGIN_NAME
    assert antigravity == {"name": PLUGIN_NAME}
    assert codex["skills"] == "./skills/"

    plugins = marketplace.get("plugins")
    assert isinstance(plugins, list) and len(plugins) == 1
    entry = cast("dict[str, object]", plugins[0])
    assert entry["name"] == PLUGIN_NAME
    assert entry["source"] == {"source": "local", "path": "./"}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }

    skill_files = sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))
    assert [path.parent.name for path in skill_files] == ["transcript"]
    assert (skill_files[0].parent / "agents" / "openai.yaml").is_file()
