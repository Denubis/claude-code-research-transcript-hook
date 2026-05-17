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

    def test_bulk_honours_target_here_default(self, temp_dir, monkeypatch):
        """When defaults.target == 'here' and no CLI flag, bulk archives locally
        (matches _resolve_archive_dir / status behaviour). Regression for the bug
        where bulk silently wrote to the global archive while status reported a
        local one."""
        transcript = temp_dir / "session-xyz.jsonl"
        transcript.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')

        local_archive = temp_dir / "ai_transcripts"
        global_archive = temp_dir / "global_should_not_be_used"

        captured: dict[str, object] = {}

        def fake_get_archive_dir(*, local: bool, output: object, project_dir: object) -> object:
            captured["local"] = local
            captured["project_dir"] = project_dir
            return local_archive if local else global_archive

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
            lambda _p: {"target": "here"},
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_archive_dir",
            fake_get_archive_dir,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["bulk"])
        assert result.exit_code == 0, result.output
        assert captured.get("local") is True, (
            f"bulk should pass local=True when target=='here'; got {captured!r}"
        )


class TestBulkClustersDispatch:
    """Phase 2: bulk groups unarchived sessions by (project, customTitle) and
    dispatches singletons to archive() while multi-session clusters trip a
    Phase-3 NotImplementedError stub."""

    @staticmethod
    def _write_session_jsonl(path: Path, custom_title: str | None) -> None:
        entry: dict = {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
        }
        if custom_title is not None:
            entry["customTitle"] = custom_title
        path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    def test_cluster_of_two_raises_notimplemented(self, temp_dir, monkeypatch):
        """Phase 2: a cluster of ≥2 same-customTitle sessions raises
        NotImplementedError naming stitch_cluster — Phase 3 wires this up."""
        a = temp_dir / "uuid-aaa.jsonl"
        b = temp_dir / "uuid-bbb.jsonl"
        self._write_session_jsonl(a, "shared-feat")
        self._write_session_jsonl(b, "shared-feat")

        archive_dir = temp_dir / "ai_transcripts"
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [(a, "uuid-aaa"), (b, "uuid-bbb")],
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
        assert result.exit_code != 0
        assert isinstance(result.exception, NotImplementedError)
        assert "stitch_cluster" in str(result.exception)
        assert "Phase 3" in str(result.exception)

    def test_singletons_still_archive_normally(self, temp_dir, monkeypatch):
        """Phase 2: sessions with distinct customTitles cluster as singletons
        and still flow through the existing archive() path — no regression."""
        a = temp_dir / "uuid-aaa.jsonl"
        b = temp_dir / "uuid-bbb.jsonl"
        self._write_session_jsonl(a, "feat-x")
        self._write_session_jsonl(b, "feat-y")

        archive_dir = temp_dir / "ai_transcripts"
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: [(a, "uuid-aaa"), (b, "uuid-bbb")],
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
        assert result.exit_code == 0, result.output
        assert "Bulk archive complete" in result.output
