"""Tests for the regenerate command."""
import json

from typer.testing import CliRunner

from claude_transcript_archive.cli import app


def _create_archive_with_raw(archive_dir, session_id):
    """Create archive dir with raw transcript and sidecar."""
    session_dir = archive_dir / f"2024-01-01-{session_id}"
    session_dir.mkdir(parents=True)

    # Write raw transcript
    raw = session_dir / "raw-transcript.jsonl"
    raw.write_text(
        '{"type":"user","message":{"role":"user","content":"Hello"}}\n'
        '{"type":"assistant","message":{"role":"assistant","content":"Hi there!"}}\n'
    )

    # Write sidecar
    sidecar = {
        "session": {"id": session_id, "started_at": "2024-01-01T10:00:00"},
        "auto_generated": {"title": "Test Session", "purpose": "", "tags": []},
        "three_ps": {"prompt_summary": "", "process_summary": "", "provenance_summary": ""},
        "archive": {
            "directory_name": session_dir.name,
            "needs_review": True,
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


class TestRegenerateCommand:
    def test_regenerate_single_session(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        session_dir = _create_archive_with_raw(archive_dir, "test-session")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, ["regenerate", "--session-id", "test-session"])
        assert result.exit_code == 0
        assert "Regenerated 1" in result.output

        # Verify conversation.md was generated
        assert (session_dir / "conversation.md").exists()

    def test_regenerate_missing_raw(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()

        # Create archive WITHOUT raw-transcript.jsonl
        session_dir = archive_dir / "2024-01-01-no-raw"
        session_dir.mkdir()
        (session_dir / "session.meta.json").write_text(
            json.dumps({
                "session": {"id": "no-raw"},
                "auto_generated": {"title": "No Raw"},
                "archive": {
                    "directory_name": session_dir.name,
                    "needs_review": True,
                    "trivial": False,
                },
            })
        )
        manifest = archive_dir / ".session_manifest.json"
        manifest.write_text(json.dumps({"no-raw": str(session_dir)}))

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, ["regenerate", "--session-id", "no-raw"])
        assert result.exit_code == 0
        assert "skipping" in result.output.lower() or "Warning" in result.output

    def test_regenerate_no_args_fails(self):
        runner = CliRunner()
        result = runner.invoke(app, ["regenerate"])
        assert result.exit_code == 1

    def test_regenerate_all(self, temp_dir, monkeypatch):
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        _create_archive_with_raw(archive_dir, "session-1")
        _create_archive_with_raw(archive_dir, "session-2")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, ["regenerate", "--all"])
        assert result.exit_code == 0
        assert "Regenerated 2" in result.output
