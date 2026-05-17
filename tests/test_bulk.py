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
    """auto-stitch: bulk groups unarchived sessions by (project, customTitle)
    and dispatches singletons to archive() while multi-session clusters dispatch
    to stitch_cluster() — one cluster directory, every constituent UUID in
    manifest, archive.stitched=true in meta."""

    @staticmethod
    def _write_session_jsonl(
        path: Path,
        custom_title: str | None,
        *,
        started_at: str = "2026-05-01T10:00:00Z",
        ended_at: str = "2026-05-01T11:00:00Z",
    ) -> None:
        entries: list[dict] = [
            {
                "type": "user",
                "timestamp": started_at,
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "timestamp": ended_at,
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-7",
                    "content": [{"type": "text", "text": "ok"}],
                },
            },
        ]
        if custom_title is not None:
            for entry in entries:
                entry["customTitle"] = custom_title
        path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )

    def test_cluster_of_two_stitches_into_one_directory(self, temp_dir, monkeypatch):
        """Phase 3: a cluster of ≥2 same-customTitle sessions produces ONE
        stitched archive directory (not two singletons), with both UUIDs fanned
        into the manifest pointing at it and archive.stitched=true."""
        a = temp_dir / "uuid-aaa.jsonl"
        b = temp_dir / "uuid-bbb.jsonl"
        self._write_session_jsonl(
            a, "shared-feat",
            started_at="2026-04-24T10:00:00Z", ended_at="2026-04-24T11:00:00Z",
        )
        self._write_session_jsonl(
            b, "shared-feat",
            started_at="2026-05-16T10:00:00Z", ended_at="2026-05-16T11:00:00Z",
        )

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
        assert "stitched" in result.output

        # One cluster directory exists, named with -stitched suffix
        stitched_dirs = [
            d for d in archive_dir.iterdir() if d.is_dir() and d.name.endswith("-stitched")
        ]
        assert len(stitched_dirs) == 1
        cluster_dir = stitched_dirs[0]

        # Manifest fans both UUIDs into the cluster
        manifest = json.loads(
            (archive_dir / ".session_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["uuid-aaa"] == str(cluster_dir)
        assert manifest["uuid-bbb"] == str(cluster_dir)

        # Stitched schema marker is present
        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert meta["archive"]["stitched"] is True
        assert len(meta["_constituent_sessions"]) == 2

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
