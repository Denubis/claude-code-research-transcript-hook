"""Tests for the clean command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from claude_transcript_archive.cli import app


def _create_test_archive(archive_dir, session_id, needs_review=True):
    """Helper to create an archive with sidecar."""
    session_dir = archive_dir / f"2024-01-01-{session_id}"
    session_dir.mkdir(parents=True)
    sidecar = {
        "session": {"id": session_id, "started_at": "2024-01-01T10:00:00"},
        "auto_generated": {"title": f"Session {session_id}", "purpose": "", "tags": []},
        "three_ps": {"prompt_summary": "", "process_summary": "", "provenance_summary": ""},
        "archive": {
            "directory_name": session_dir.name,
            "needs_review": needs_review,
            "trivial": False,
        },
    }
    (session_dir / "session.meta.json").write_text(json.dumps(sidecar))
    return session_dir


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


class TestCleanIndexRepair:
    def test_ac6_1_rebuilds_catalog(self, temp_dir, monkeypatch):
        """AC6.1: Delete CATALOG.json, run clean --execute -> CATALOG regenerated."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_test_archive(archive_dir, "session-1", needs_review=True)
        _create_test_archive(archive_dir, "session-2", needs_review=False)
        _create_test_archive(archive_dir, "session-3", needs_review=True)

        # Ensure no CATALOG.json exists
        catalog_path = archive_dir / "CATALOG.json"
        assert not catalog_path.exists()

        monkeypatch.setattr(
            "claude_transcript_archive.cli._resolve_archive_dir",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["clean", "--execute"])
        assert result.exit_code == 0

        # Verify CATALOG.json regenerated
        assert catalog_path.exists()
        catalog = json.loads(catalog_path.read_text())
        assert catalog["total_sessions"] == 3
        assert catalog["needs_review_count"] == 2

    def test_ac6_2_rebuilds_manifest(self, temp_dir, monkeypatch):
        """AC6.2: Delete manifest, run clean --execute -> manifest regenerated."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_test_archive(archive_dir, "session-a")
        _create_test_archive(archive_dir, "session-b")

        manifest_path = archive_dir / ".session_manifest.json"
        assert not manifest_path.exists()

        monkeypatch.setattr(
            "claude_transcript_archive.cli._resolve_archive_dir",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["clean", "--execute"])
        assert result.exit_code == 0

        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "session-a" in manifest
        assert "session-b" in manifest

    def test_ac6_3_dry_run_no_modifications(self, temp_dir, monkeypatch):
        """AC6.3: dry-run reports but modifies no files."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_test_archive(archive_dir, "session-x")

        # No index files exist
        manifest_path = archive_dir / ".session_manifest.json"
        catalog_path = archive_dir / "CATALOG.json"
        assert not manifest_path.exists()
        assert not catalog_path.exists()

        monkeypatch.setattr(
            "claude_transcript_archive.cli._resolve_archive_dir",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )

        runner = CliRunner()
        result = runner.invoke(app, ["clean"])  # dry-run is default
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "Would rebuild" in result.output

        # Files still don't exist
        assert not manifest_path.exists()
        assert not catalog_path.exists()
