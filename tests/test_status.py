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

    def test_status_dedupes_cluster_constituents(self, temp_dir, monkeypatch, runner):
        """auto-stitch.AC6.2: a stitched cluster appears as ONE archived row in
        status, not N rows (one per constituent UUID). Manifest fan-in lists
        every constituent UUID pointing at the same dir; status must collapse."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        cluster_dir = archive_dir / "2026-04-24-feat-x-stitched"
        cluster_dir.mkdir()
        # Stitched meta: archive.stitched=true, _constituent_sessions of 3.
        cluster_meta = {
            "session": {"id": "uuid-a", "started_at": "2026-04-24T10:00:00Z"},
            "auto_generated": {"title": "feat-x"},
            "three_ps": {
                "prompt_summary": "p", "process_summary": "q", "provenance_summary": "r",
            },
            "archive": {
                "directory_name": cluster_dir.name,
                "needs_review": False,
                "trivial": False,
                "stitched": True,
            },
            "_constituent_sessions": [
                {"id": "uuid-a", "rank": 1},
                {"id": "uuid-b", "rank": 2},
                {"id": "uuid-c", "rank": 3},
            ],
        }
        (cluster_dir / "session.meta.json").write_text(json.dumps(cluster_meta))
        manifest = {
            "uuid-a": str(cluster_dir),
            "uuid-b": str(cluster_dir),
            "uuid-c": str(cluster_dir),
        }
        (archive_dir / ".session_manifest.json").write_text(json.dumps(manifest))
        (archive_dir / "CATALOG.json").write_text(
            json.dumps({"sessions": [
                {"session_id": "uuid-a", "needs_review": False, "title": "feat-x"},
            ]})
        )

        transcripts = []
        for sid in ["uuid-a", "uuid-b", "uuid-c"]:
            tp = temp_dir / f"{sid}.jsonl"
            tp.write_text('{"type":"user","message":{"role":"user","content":"x"}}\n')
            transcripts.append((tp, sid))

        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions",
            lambda: transcripts,
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

        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Three transcripts found, one stitched cluster — total counts collapse.
        assert len(data["archived"]) == 1, (
            f"expected one cluster row, got {len(data['archived'])}: {data['archived']}"
        )
        # The single row identifies as a stitched cluster.
        assert data["archived"][0].get("stitched") is True
        assert data["archived"][0].get("constituent_count") == 3

    def test_status_plain_output_shows_cluster_constituent_count(
        self, temp_dir, monkeypatch, runner,
    ):
        """Plain text status names the cluster and its constituent count."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        cluster_dir = archive_dir / "2026-04-24-feat-y-stitched"
        cluster_dir.mkdir()
        (cluster_dir / "session.meta.json").write_text(json.dumps({
            "session": {"id": "uuid-1", "started_at": "2026-04-24T10:00:00Z"},
            "auto_generated": {"title": "feat-y"},
            "three_ps": {
                "prompt_summary": "p", "process_summary": "q", "provenance_summary": "r",
            },
            "archive": {
                "directory_name": cluster_dir.name,
                "needs_review": False, "trivial": False, "stitched": True,
            },
            "_constituent_sessions": [
                {"id": "uuid-1", "rank": 1},
                {"id": "uuid-2", "rank": 2},
            ],
        }))
        (archive_dir / ".session_manifest.json").write_text(json.dumps({
            "uuid-1": str(cluster_dir), "uuid-2": str(cluster_dir),
        }))
        (archive_dir / "CATALOG.json").write_text(json.dumps({"sessions": []}))

        transcripts = [
            (temp_dir / "uuid-1.jsonl", "uuid-1"),
            (temp_dir / "uuid-2.jsonl", "uuid-2"),
        ]
        for tp, _ in transcripts:
            tp.write_text('{"type":"user","message":{"role":"user","content":"x"}}\n')

        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees", lambda: [temp_dir],
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.discover_sessions", lambda: transcripts,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.get_project_dir_from_transcript",
            lambda _p: temp_dir,
        )
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.load_project_defaults", lambda _p: {},
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda _cmd, **_kw: type("R", (), {"stdout": str(temp_dir) + "\n", "returncode": 0})(),
        )

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        # Single archived count, mentions cluster + constituents.
        assert "Archived:    1 sessions" in result.output
        assert "2 sessions stitched" in result.output or "2 constituents" in result.output

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
