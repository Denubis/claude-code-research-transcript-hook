"""Full lifecycle integration tests."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_transcript_archive.cli import app


@pytest.fixture()
def git_repo(temp_dir):
    """Create a real git repo in temp_dir."""
    subprocess.run(
        ["git", "init"],
        cwd=temp_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        cwd=temp_dir,
        check=True,
        capture_output=True,
    )
    return temp_dir


class TestAC2AllVerbsCallable:
    """AC2.1: Each of the seven verbs is callable and produces --help output."""

    @pytest.mark.parametrize(
        "verb",
        ["init", "archive", "bulk", "status", "update", "regenerate", "clean"],
    )
    def test_verb_help(self, verb):
        runner = CliRunner()
        result = runner.invoke(app, [verb, "--help"])
        assert result.exit_code == 0
        assert verb in result.output.lower() or "usage" in result.output.lower()


class TestAC2InvalidFlags:
    """AC2.4: Invalid flags produce Typer error, not traceback."""

    def test_invalid_flag(self):
        runner = CliRunner()
        result = runner.invoke(app, ["archive", "--nonexistent-flag"])
        assert result.exit_code != 0
        # Should not have a Python traceback
        assert "Traceback" not in result.output


class TestStatusJson:
    """AC2.3: status --json outputs valid JSON."""

    def test_status_json_valid(self, monkeypatch):
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path.cwd()],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [],
        )
        runner = CliRunner()
        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total" in data
        assert "archived" in data
        assert "unarchived" in data


class TestFullLifecycle:
    """End-to-end lifecycle: init -> archive -> status -> update -> regenerate -> clean."""

    def test_lifecycle(self, git_repo):
        """Full lifecycle in a real git repo."""
        repo = git_repo

        # Step 1: Init
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_transcript_archive.cli",
                "init",
                "--non-interactive",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"init failed: {result.stderr}"

        # Verify init artifacts
        assert (repo / ".ai-transcripts").is_dir()
        assert ".ai-transcripts/" in (repo / ".gitignore").read_text()
        assert (repo / ".claude" / "settings.local.json").exists()
        assert (repo / ".claude" / "transcript-defaults.json").exists()

        # Step 2: Archive a session
        transcript = repo / "test-session.jsonl"
        transcript.write_text(
            '{"type":"user","message":{"role":"user","content":"Hello"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"Hi!"}}\n'
        )

        archive_dir = repo / ".ai-transcripts"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_transcript_archive.cli",
                "archive",
                "--transcript",
                str(transcript),
                "--session-id",
                "integration-test-001",
                "--output",
                str(archive_dir),
                "--tags",
                "test,integration",
                "--purpose",
                "Lifecycle test",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"archive failed: {result.stderr}"

        # Verify archive output exists in archive dir
        all_dirs = [d for d in archive_dir.iterdir() if d.is_dir()]
        assert len(all_dirs) >= 1, (
            f"No archive dirs found. Contents: {list(archive_dir.iterdir())}"
        )

        session_dir = all_dirs[0]
        sidecar_path = session_dir / "session.meta.json"
        assert sidecar_path.exists(), f"No sidecar in {session_dir}"

        sidecar = json.loads(sidecar_path.read_text())
        assert sidecar["auto_generated"]["tags"] == ["test", "integration"]
        assert sidecar["auto_generated"]["purpose"] == "Lifecycle test"

        # Step 3: Update with Three Ps (run from repo root so _resolve_archive_dir works)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_transcript_archive.cli",
                "update",
                "--session-id",
                "integration-test-001",
                "--prompt",
                "Testing the lifecycle",
                "--process",
                "Automated integration test",
                "--provenance",
                "CI verification",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"update failed: {result.stderr}"

        # Verify Three Ps and needs_review update
        sidecar = json.loads(sidecar_path.read_text())
        assert sidecar["three_ps"]["prompt_summary"] == "Testing the lifecycle"
        assert sidecar["archive"]["needs_review"] is False

        # Step 4: Delete CATALOG.json, run clean --execute to rebuild
        catalog_path = archive_dir / "CATALOG.json"
        if catalog_path.exists():
            catalog_path.unlink()

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "claude_transcript_archive.cli",
                "clean",
                "--execute",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"clean failed: {result.stderr}"
        assert catalog_path.exists(), "CATALOG.json not rebuilt by clean"

        # Step 5: Verify all verbs respond to --help (AC2.1)
        for verb in [
            "init",
            "archive",
            "bulk",
            "status",
            "update",
            "regenerate",
            "clean",
        ]:
            help_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "claude_transcript_archive.cli",
                    verb,
                    "--help",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert help_result.returncode == 0, f"{verb} --help failed"
