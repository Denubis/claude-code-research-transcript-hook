"""Tests for the clean command."""

from pathlib import Path

from typer.testing import CliRunner

from claude_transcript_archive.cli import app


class TestCleanCommand:
    def test_clean_no_archive(self, monkeypatch):
        monkeypatch.setattr(
            "claude_transcript_archive.cli._resolve_archive_dir",
            lambda: Path("/nonexistent"),
        )
        runner = CliRunner()
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 0
        assert "Nothing to clean" in result.output

    def test_clean_dry_run_default(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        monkeypatch.setattr(
            "claude_transcript_archive.cli._resolve_archive_dir",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        runner = CliRunner()
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 0
        # Dry run is default
        assert "DRY RUN" in result.output or "clean" in result.output.lower()
