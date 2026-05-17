"""Tests for auto-stitch cluster orchestration (Phase 3+).

Covers stitch_cluster() in archive.py — building a fresh stitched archive
directory from N constituent JSONLs. The MELICA hand-rolled cluster at
``/media/brian/storage/people/Adela/melica/ai_transcripts/
2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/`` is the schema
oracle; see docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md
for the AC list and DR rationale.
"""

import json
from pathlib import Path

from claude_transcript_archive import catalog as _catalog
from claude_transcript_archive.archive import stitch_cluster


def _write_session_jsonl(
    path: Path,
    session_id: str,
    custom_title: str,
    started_at: str,
    ended_at: str,
    user_messages: int = 2,
    assistant_messages: int = 3,
) -> None:
    """Build a minimal JSONL exercising the fields stitch_cluster cares about.

    Each session has a fixed customTitle (so auto-stitch could discover it),
    explicit start/end timestamps (so chronological sorting and duration
    aggregation are deterministic), and counted user/assistant entries.
    """
    lines: list[str] = []
    # First entry — start timestamp + customTitle
    lines.append(json.dumps({
        "type": "user",
        "timestamp": started_at,
        "customTitle": custom_title,
        "message": {"role": "user", "content": "start"},
        "sessionId": session_id,
    }))
    # Remaining user messages
    for i in range(user_messages - 1):
        lines.append(json.dumps({
            "type": "user",
            "timestamp": started_at,
            "customTitle": custom_title,
            "message": {"role": "user", "content": f"u{i}"},
        }))
    # Assistant messages
    for i in range(assistant_messages - 1):
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": started_at,
            "customTitle": custom_title,
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": f"a{i}"}],
            },
        }))
    # Final assistant — carries the session's end timestamp
    lines.append(json.dumps({
        "type": "assistant",
        "timestamp": ended_at,
        "customTitle": custom_title,
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-7",
            "content": [{"type": "text", "text": "end"}],
        },
        "version": "2.1.114",
    }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _Cluster:
    """Helper container holding the inputs and outputs of a stitch_cluster run."""

    def __init__(self, temp_dir: Path):
        self.archive_dir = temp_dir / "archive"
        self.members: list[tuple[Path, str]] = []

    def add(
        self,
        session_id: str,
        custom_title: str,
        started_at: str,
        ended_at: str,
        user_messages: int = 2,
        assistant_messages: int = 3,
    ) -> Path:
        slug_dir = self.archive_dir.parent / f"-fake-{session_id[:4]}"
        slug_dir.mkdir(exist_ok=True)
        path = slug_dir / f"{session_id}.jsonl"
        _write_session_jsonl(
            path, session_id, custom_title, started_at, ended_at,
            user_messages=user_messages, assistant_messages=assistant_messages,
        )
        self.members.append((path, session_id))
        return path

    def run(self, **kwargs) -> Path:
        result = stitch_cluster(self.members, self.archive_dir, quiet=True, **kwargs)
        assert result is not None, "stitch_cluster returned None"
        return result


class TestStitchClusterSchema:
    """auto-stitch.AC2 — stitched schema matches MELICA hand-rolled format."""

    def test_ac2_1_archive_stitched_constituents_primary_jsonl(self, temp_dir):
        """AC2.1: meta has archive.stitched=true, _constituent_sessions with
        rank starting at 1, artifacts.primary_jsonl naming first constituent's jsonl."""
        c = _Cluster(temp_dir)
        c.add("aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        c.add("ccc-uuid", "feat-x", "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z")

        out = c.run()
        meta = json.loads((out / "session.meta.json").read_text(encoding="utf-8"))

        assert meta["archive"]["stitched"] is True
        assert meta["_constituent_sessions"] == [
            {"id": "aaa-uuid", "rank": 1},
            {"id": "bbb-uuid", "rank": 2},
            {"id": "ccc-uuid", "rank": 3},
        ]
        assert meta["artifacts"]["primary_jsonl"] == "aaa-uuid.jsonl"
        assert (out / "aaa-uuid.jsonl").exists()

    def test_ac2_2_statistics_aggregated(self, temp_dir):
        """AC2.2: statistics aggregates across constituents — turns, user_messages,
        assistant_messages, jsonl_lines, raw_transcript_bytes."""
        c = _Cluster(temp_dir)
        c.add(
            "aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
            user_messages=3, assistant_messages=2,
        )
        c.add(
            "bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
            user_messages=4, assistant_messages=5,
        )

        out = c.run()
        meta = json.loads((out / "session.meta.json").read_text(encoding="utf-8"))
        stats = meta["statistics"]

        # Sum across both sessions: 3+4 user, 2+5 assistant, turns = users+assistants
        assert stats["user_messages"] == 7
        assert stats["assistant_messages"] == 7
        assert stats["turns"] == 14
        # jsonl_lines = 5 (session A: 3 user + 2 assistant) + 9 (session B: 4 user + 5 assistant)
        assert stats["jsonl_lines"] == 14
        # raw_transcript_bytes from actual file
        assert stats["raw_transcript_bytes"] == (out / "raw-transcript.jsonl").stat().st_size

    def test_ac2_3_session_id_primary_started_earliest_ended_latest(self, temp_dir):
        """AC2.3: session.id equals first constituent's UUID; started_at is earliest,
        ended_at is latest, duration_minutes spans the full range."""
        c = _Cluster(temp_dir)
        # Insert out of chronological order — stitch_cluster must sort
        c.add("middle-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        c.add("earliest-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("latest-uuid", "feat-x", "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z")

        out = c.run()
        meta = json.loads((out / "session.meta.json").read_text(encoding="utf-8"))

        assert meta["session"]["id"] == "earliest-uuid"
        assert meta["session"]["started_at"] == "2026-04-24T10:00:00Z"
        assert meta["session"]["ended_at"] == "2026-05-16T11:00:00Z"
        # ~22 days = ~31700 minutes; allow rough check
        assert 31000 < meta["session"]["duration_minutes"] < 32500

    def test_chronological_rank_after_unsorted_input(self, temp_dir):
        """Members supplied out of chronological order are sorted before rank
        assignment — rank 1 is always the earliest start."""
        c = _Cluster(temp_dir)
        c.add("z-uuid", "feat-x", "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z")
        c.add("a-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("m-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")

        out = c.run()
        meta = json.loads((out / "session.meta.json").read_text(encoding="utf-8"))

        assert meta["_constituent_sessions"] == [
            {"id": "a-uuid", "rank": 1},
            {"id": "m-uuid", "rank": 2},
            {"id": "z-uuid", "rank": 3},
        ]

    def test_directory_name_includes_stitched_suffix_and_earliest_date(self, temp_dir):
        """Cluster dir is YYYY-MM-DD-<sanitised-title>-stitched, date from earliest start."""
        c = _Cluster(temp_dir)
        c.add("aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("bbb-uuid", "feat-x", "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z")

        out = c.run()
        assert out.name.startswith("2026-04-24-")
        assert out.name.endswith("-stitched")

    def test_raw_transcript_is_concatenation_of_constituents(self, temp_dir):
        """raw-transcript.jsonl contains every line from every constituent, in
        chronological order. Total line count equals sum of constituent line counts."""
        c = _Cluster(temp_dir)
        path_a = c.add(
            "aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
            user_messages=2, assistant_messages=2,
        )
        path_b = c.add(
            "bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
            user_messages=2, assistant_messages=2,
        )

        out = c.run()
        raw = (out / "raw-transcript.jsonl").read_text(encoding="utf-8")
        lines_a = path_a.read_text(encoding="utf-8").splitlines()
        lines_b = path_b.read_text(encoding="utf-8").splitlines()
        raw_lines = [line for line in raw.splitlines() if line.strip()]
        assert raw_lines == [line for line in lines_a if line.strip()] + [
            line for line in lines_b if line.strip()
        ]

    def test_primary_jsonl_mirrors_raw(self, temp_dir):
        """<primary-uuid>.jsonl has the same content as raw-transcript.jsonl
        (per MELICA convention — both files hold the concatenated stream)."""
        c = _Cluster(temp_dir)
        c.add("aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")

        out = c.run()
        raw_text = (out / "raw-transcript.jsonl").read_text(encoding="utf-8")
        primary_text = (out / "aaa-uuid.jsonl").read_text(encoding="utf-8")
        assert raw_text == primary_text


class TestStitchClusterManifest:
    """auto-stitch.AC3 — manifest fan-in."""

    def test_ac3_1_every_constituent_uuid_in_manifest(self, temp_dir):
        c = _Cluster(temp_dir)
        c.add("aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        c.add("ccc-uuid", "feat-x", "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z")

        c.run()
        manifest = _catalog.load_manifest(c.archive_dir)
        assert "aaa-uuid" in manifest
        assert "bbb-uuid" in manifest
        assert "ccc-uuid" in manifest

    def test_ac3_2_every_uuid_resolves_to_cluster_dir(self, temp_dir):
        c = _Cluster(temp_dir)
        c.add("aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        c.add("bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")

        out = c.run()
        manifest = _catalog.load_manifest(c.archive_dir)
        assert manifest["aaa-uuid"] == str(out)
        assert manifest["bbb-uuid"] == str(out)


class TestStitchClusterErrors:
    """Defensive contract — stitch_cluster expects len(members) >= 2."""

    def test_raises_on_singleton(self, temp_dir):
        c = _Cluster(temp_dir)
        c.add("solo-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        import pytest  # noqa: PLC0415

        with pytest.raises(ValueError, match=r"at least 2"):
            stitch_cluster(c.members, c.archive_dir, quiet=True)
