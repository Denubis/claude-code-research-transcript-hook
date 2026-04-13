"""Tests for the bulk command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from claude_transcript_archive.cli import app


class TestBulkCommand:
    def test_bulk_no_sessions(self, monkeypatch):
        """No sessions -> reports none found."""
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path.cwd()],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [],
        )
        runner = CliRunner()
        result = runner.invoke(app, ["bulk"])
        assert result.exit_code == 0
        assert "No sessions found" in result.output

    def test_bulk_all_already_archived(self, temp_dir, monkeypatch):
        """All sessions archived -> reports all already archived."""
        transcript = temp_dir / "session-abc.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"hi"}}\n')

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
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_archive_dir",
            lambda **_kw: temp_dir / "archive",
        )
        # Create manifest with this session
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        manifest_path = archive_dir / ".session_manifest.json"
        manifest_path.write_text(json.dumps({"session-abc": str(archive_dir / "some-dir")}))

        runner = CliRunner()
        result = runner.invoke(app, ["bulk", "--local"])
        assert result.exit_code == 0
        assert "already archived" in result.output

    def test_bulk_archives_unarchived(self, temp_dir, monkeypatch):
        """AC5.1: Bulk archives sessions with needs_review=true."""
        transcript = temp_dir / "session-abc.jsonl"
        # Write a trivial session (< 5 assistant messages)
        transcript.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')

        archive_dir = temp_dir / "ai_transcripts"

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
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_archive_dir",
            lambda **_kw: archive_dir,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["bulk", "--local"])
        assert result.exit_code == 0
        assert "Bulk archive complete" in result.output
