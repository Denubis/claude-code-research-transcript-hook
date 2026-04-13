"""Tests for the update command."""
import json

from typer.testing import CliRunner

from claude_transcript_archive.cli import app


def _create_archive(archive_dir, session_id, needs_review=True):
    """Helper to create a minimal archive with sidecar."""
    session_dir = archive_dir / f"2024-01-01-{session_id}"
    session_dir.mkdir(parents=True)
    sidecar = {
        "session": {"id": session_id, "started_at": "2024-01-01T10:00:00"},
        "auto_generated": {"title": "Original Title", "purpose": "", "tags": []},
        "three_ps": {"prompt_summary": "", "process_summary": "", "provenance_summary": ""},
        "archive": {
            "directory_name": session_dir.name,
            "needs_review": needs_review,
            "trivial": False,
        },
    }
    (session_dir / "session.meta.json").write_text(json.dumps(sidecar))
    # Write manifest
    manifest_path = archive_dir / ".session_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest[session_id] = str(session_dir)
    manifest_path.write_text(json.dumps(manifest))
    return session_dir


class TestUpdateCommand:
    def test_update_tags_and_purpose(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_archive(archive_dir, "test-session")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, [
            "update", "--session-id", "test-session",
            "--tags", "research,analysis",
            "--purpose", "Testing updates",
        ])
        assert result.exit_code == 0

        sidecar = json.loads(
            (archive_dir / "2024-01-01-test-session" / "session.meta.json").read_text()
        )
        assert sidecar["auto_generated"]["tags"] == ["research", "analysis"]
        assert sidecar["auto_generated"]["purpose"] == "Testing updates"

    def test_update_three_ps_marks_reviewed(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_archive(archive_dir, "test-session")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, [
            "update", "--session-id", "test-session",
            "--prompt", "Test prompt",
            "--process", "Test process",
            "--provenance", "Test provenance",
        ])
        assert result.exit_code == 0

        sidecar = json.loads(
            (archive_dir / "2024-01-01-test-session" / "session.meta.json").read_text()
        )
        assert sidecar["archive"]["needs_review"] is False

    def test_update_nonexistent_session(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        # Empty manifest
        (archive_dir / ".session_manifest.json").write_text("{}")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, ["update", "--session-id", "nonexistent"])
        assert result.exit_code == 1

    def test_update_all_needs_review(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_archive(archive_dir, "review-1", needs_review=True)
        _create_archive(archive_dir, "review-2", needs_review=True)
        _create_archive(archive_dir, "done-1", needs_review=False)

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, [
            "update", "--all-needs-review",
            "--tags", "batch-tag",
        ])
        assert result.exit_code == 0
        assert "Updated 2 session" in result.output

    def test_update_no_args_fails(self):
        """Must provide --session-id or --all-needs-review."""
        runner = CliRunner()
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
