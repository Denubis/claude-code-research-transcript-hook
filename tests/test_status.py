"""Tests for the status command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from claude_transcript_archive.cli import app


@pytest.fixture
def runner():
    return CliRunner()


class TestStatusCommand:
    def test_status_no_sessions(self, monkeypatch, runner):
        """Status in repo with no sessions shows zeros."""
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path.cwd()],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [],
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Total:" in result.output
        assert "0 sessions" in result.output

    def test_status_json_output(self, monkeypatch, runner):
        """Status with --json returns valid JSON."""
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path.cwd()],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [],
        )

        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total" in data
        assert "archived" in data
        assert "unarchived" in data

    def test_status_with_sessions(self, temp_dir, monkeypatch, runner):
        """AC4.1: Status reports sessions from worktrees."""
        # Create a fake transcript
        transcript = temp_dir / "session-abc.jsonl"
        transcript.write_text(
            '{"type":"assistant","message":{"role":"assistant","content":"hi"}}\n'
        )

        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [(transcript, "session-abc")],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_project_dir_from_transcript",
            lambda _p: temp_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {},
        )
        # Mock subprocess.run for git rev-parse in the status command
        monkeypatch.setattr(
            "subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir) + "\n", "returncode": 0})(),
        )

        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total"] == 1
        assert len(data["unarchived"]) == 1
        assert data["unarchived"][0]["classification"] == "trivial"

    def test_status_lists_unarchived_sessions(self, temp_dir, monkeypatch, runner):
        """Plain status output lists each unarchived session id and classification."""
        transcript = temp_dir / "session-xyz.jsonl"
        transcript.write_text(
            '{"type":"assistant","message":{"role":"assistant","content":"hi"}}\n'
        )

        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [(transcript, "session-xyz")],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_project_dir_from_transcript",
            lambda _p: temp_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {},
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir) + "\n", "returncode": 0})(),
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Unarchived sessions:" in result.output
        assert "session-xyz" in result.output
        assert "trivial" in result.output

    def test_status_lists_needs_review_sessions(self, temp_dir, monkeypatch, runner):
        """Plain status output lists each archived session whose needs_review is true."""
        transcript = temp_dir / "session-rev.jsonl"
        transcript.write_text(
            '{"type":"assistant","message":{"role":"assistant","content":"hi"}}\n'
        )

        # Stage an archive dir with manifest + catalog marking needs_review=True
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        session_dir = archive_dir / "2026-04-24-needs-review"
        session_dir.mkdir()
        (archive_dir / ".session_manifest.json").write_text(
            json.dumps({"session-rev": str(session_dir)})
        )
        (archive_dir / "CATALOG.json").write_text(
            json.dumps(
                {
                    "sessions": [
                        {
                            "session_id": "session-rev",
                            "needs_review": True,
                            "title": "needs review",
                        }
                    ]
                }
            )
        )

        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [(transcript, "session-rev")],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_project_dir_from_transcript",
            lambda _p: temp_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {},
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir) + "\n", "returncode": 0})(),
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Needs review:" in result.output
        assert "session-rev" in result.output

    def test_status_omits_lists_when_empty(self, monkeypatch, runner):
        """No section headers when there are no unarchived or needs_review sessions."""
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path.cwd()],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [],
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Unarchived sessions:" not in result.output
        assert "Needs review:" not in result.output
