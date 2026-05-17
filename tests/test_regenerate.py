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

    def test_regenerate_output_is_whitespace_clean(self, temp_dir, monkeypatch):
        """End-to-end guarantee: after regenerate, every text file in the
        session dir has no trailing-whitespace and exactly one trailing newline.
        Regression for the pre-commit ping-pong observed during stitched-archive
        appends — the user's tooling repeatedly bounced commits because
        regenerate left dirty HTML/MD on disk."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        session_dir = _create_archive_with_raw(archive_dir, "clean-session")

        # Plant deliberately dirty files that claude-code-transcripts might
        # have produced, so normalise has actual work to do.
        (session_dir / "index.html").write_text("<html>   \n<body>x</body>\n</html>")
        (session_dir / "page-001.html").write_text("dirty   \n   \n")
        (session_dir / ".title").write_text("Test Session   ")

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        result = runner.invoke(app, ["regenerate", "--session-id", "clean-session"])
        assert result.exit_code == 0

        # Walk every text-ish file in the archive dir and prove cleanliness.
        suffixes_to_check = {".html", ".md", ".json", ".jsonl", ".txt"}
        names_to_check = {".title", ".last_size"}
        for path in session_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in suffixes_to_check and path.name not in names_to_check:
                continue
            content = path.read_text(encoding="utf-8")
            if not content:
                continue
            assert content.endswith("\n"), f"{path.name} missing EOF newline"
            assert not content.endswith("\n\n"), f"{path.name} has multiple EOF newlines"
            for n, line in enumerate(content.splitlines(), 1):
                assert line == line.rstrip(), (
                    f"{path.name}:{n} has trailing whitespace: {line!r}"
                )

    def test_regenerate_cluster_via_constituent_uuid(self, temp_dir, monkeypatch):
        """auto-stitch.AC6.3: regenerate --session-id <constituent> rebuilds the
        cluster's outputs from the concatenated raw — manifest fan-in resolves
        every constituent UUID to the same cluster dir, so any constituent's
        UUID is a valid handle on the cluster's render."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        cluster_dir = archive_dir / "2026-04-24-stitched-arc-stitched"
        cluster_dir.mkdir()

        raw = cluster_dir / "raw-transcript.jsonl"
        raw.write_text(
            '{"type":"user","message":{"role":"user","content":"Question 1"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"Answer 1"}}\n'
            '{"type":"user","message":{"role":"user","content":"Question 2"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"Answer 2"}}\n'
        )

        # Stitched-shape meta.
        (cluster_dir / "session.meta.json").write_text(json.dumps({
            "session": {"id": "uuid-primary", "started_at": "2026-04-24T10:00:00Z"},
            "auto_generated": {"title": "stitched arc"},
            "three_ps": {
                "prompt_summary": "p", "process_summary": "q", "provenance_summary": "r",
            },
            "archive": {
                "directory_name": cluster_dir.name,
                "needs_review": False, "trivial": False, "stitched": True,
            },
            "_constituent_sessions": [
                {"id": "uuid-primary", "rank": 1},
                {"id": "uuid-second", "rank": 2},
            ],
        }))
        # Manifest fan-in: both constituent UUIDs → same cluster dir.
        (archive_dir / ".session_manifest.json").write_text(json.dumps({
            "uuid-primary": str(cluster_dir),
            "uuid-second": str(cluster_dir),
        }))

        monkeypatch.setattr(
            "claude_transcript_archive.cli.subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir), "returncode": 0})(),
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults",
            lambda _p: {"target": "branch"},
        )

        runner = CliRunner()
        # Invoke with the SECOND constituent's UUID — not the primary — to prove
        # the resolution works for any constituent.
        result = runner.invoke(app, ["regenerate", "--session-id", "uuid-second"])
        assert result.exit_code == 0
        assert "Regenerated 1" in result.output
        # Cluster outputs rebuilt.
        assert (cluster_dir / "conversation.md").exists()
        # The rebuild covered the concatenated stream (both turn pairs).
        md_text = (cluster_dir / "conversation.md").read_text(encoding="utf-8")
        assert "Question 1" in md_text
        assert "Question 2" in md_text

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
