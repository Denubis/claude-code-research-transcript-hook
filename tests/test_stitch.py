"""Tests for auto-stitch cluster orchestration (Phase 3+).

Covers stitch_cluster() in archive.py — building a fresh stitched archive
directory from N constituent JSONLs. The MELICA hand-rolled cluster at
``/media/brian/storage/people/Adela/melica/ai_transcripts/
2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/`` is the schema
oracle; see docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md
for the AC list and DR rationale.

Phase 4 additions: TestExtendCluster, TestPromoteSingletonToCluster, and
TestArchivePromotionPath exercise the hook-path promotion sequence (DR2/DR3).
"""

import json
from pathlib import Path

import pytest

from claude_transcript_archive import catalog as _catalog
from claude_transcript_archive.archive import (
    archive,
    extend_cluster,
    promote_singleton_to_cluster,
    stitch_cluster,
    stitch_sessions,
)


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

        with pytest.raises(ValueError, match=r"at least 2"):
            stitch_cluster(c.members, c.archive_dir, quiet=True)


# =============================================================================
# Phase 4 — hook-path promotion (extend_cluster, promote_singleton_to_cluster,
# archive() dispatch). See docs/design-plans/2026-05-16-auto-stitch-customtitle-
# clustering.md AC4.1–AC4.4.
# =============================================================================


def _new_session_jsonl(
    temp_dir: Path,
    session_id: str,
    custom_title: str | None,
    started_at: str,
    ended_at: str,
    user_messages: int = 2,
    assistant_messages: int = 3,
) -> Path:
    """Materialise a standalone session JSONL outside any cluster scaffolding.

    Returns the path under temp_dir/-fake-new-<sid>/<sid>.jsonl so each call
    gets its own parent (mirroring Claude Code's per-project layout). When
    custom_title is None the customTitle field is omitted entirely — matching
    how pre-/exec-session-naming sessions look on disk.
    """
    slug_dir = temp_dir / f"-fake-new-{session_id[:4]}"
    slug_dir.mkdir(exist_ok=True)
    path = slug_dir / f"{session_id}.jsonl"
    lines: list[str] = []
    base: dict = {
        "type": "user",
        "timestamp": started_at,
        "message": {"role": "user", "content": "start"},
        "sessionId": session_id,
    }
    if custom_title is not None:
        base["customTitle"] = custom_title
    lines.append(json.dumps(base))
    for i in range(user_messages - 1):
        entry: dict = {
            "type": "user",
            "timestamp": started_at,
            "message": {"role": "user", "content": f"u{i}"},
        }
        if custom_title is not None:
            entry["customTitle"] = custom_title
        lines.append(json.dumps(entry))
    for i in range(assistant_messages - 1):
        entry = {
            "type": "assistant",
            "timestamp": started_at,
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-7",
                "content": [{"type": "text", "text": f"a{i}"}],
            },
        }
        if custom_title is not None:
            entry["customTitle"] = custom_title
        lines.append(json.dumps(entry))
    final: dict = {
        "type": "assistant",
        "timestamp": ended_at,
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-7",
            "content": [{"type": "text", "text": "end"}],
        },
        "version": "2.1.114",
    }
    if custom_title is not None:
        final["customTitle"] = custom_title
    lines.append(json.dumps(final))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestExtendCluster:
    """auto-stitch.AC4.2/AC4.3/AC4.4 — adding one constituent to an existing
    cluster mirrors the MELICA stitch_archive_extend.py contract."""

    def _seed_cluster(self, temp_dir: Path) -> tuple[Path, _Cluster]:
        c = _Cluster(temp_dir)
        c.add(
            "aaa-uuid", "feat-x", "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
            user_messages=2, assistant_messages=3,
        )
        c.add(
            "bbb-uuid", "feat-x", "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
            user_messages=2, assistant_messages=3,
        )
        return c.run(), c

    def test_ac4_2_appends_new_constituent_entry(self, temp_dir):
        cluster_dir, _ = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
            user_messages=4, assistant_messages=5,
        )

        result = extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)
        assert result == cluster_dir

        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert meta["_constituent_sessions"][-1] == {"id": "ccc-uuid", "rank": 3}
        assert len(meta["_constituent_sessions"]) == 3

    def test_ac4_2_statistics_aggregated_after_extend(self, temp_dir):
        cluster_dir, _ = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
            user_messages=4, assistant_messages=5,
        )
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)

        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        stats = meta["statistics"]
        # Seed: 2 sessions × (2u + 3a) = 4u, 6a, 10 lines.
        # Plus ccc: 4u + 5a, 9 lines.
        assert stats["user_messages"] == 8
        assert stats["assistant_messages"] == 11
        assert stats["turns"] == 19
        assert stats["jsonl_lines"] == 19
        assert stats["raw_transcript_bytes"] == (
            cluster_dir / "raw-transcript.jsonl"
        ).stat().st_size

    def test_ac4_2_archive_extended_at_set(self, temp_dir):
        """The MELICA contract: every extend stamps archive.extended_at so
        timestamp-sensitive audits can distinguish stitched-at from latest-
        extension. stitch_archive_extend.py sets it unconditionally."""
        cluster_dir, _ = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)

        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert meta["archive"].get("extended_at"), "archive.extended_at must be set"

    def test_ac4_2_manifest_fans_new_uuid(self, temp_dir):
        cluster_dir, c = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)

        manifest = _catalog.load_manifest(c.archive_dir)
        assert manifest["ccc-uuid"] == str(cluster_dir)
        # Existing constituents still point at the cluster.
        assert manifest["aaa-uuid"] == str(cluster_dir)
        assert manifest["bbb-uuid"] == str(cluster_dir)

    def test_ac4_3_raw_transcript_appended_not_replaced(self, temp_dir):
        """AC4.3: extension preserves prior content — the new JSONL is appended
        to the concatenated stream, not substituted for it."""
        cluster_dir, _ = self._seed_cluster(temp_dir)
        before = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        new_content = new_path.read_text(encoding="utf-8")
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)

        after = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")
        assert after.startswith(before), "prior content must be preserved verbatim"
        # Every line of the new transcript appears in the result.
        for line in new_content.splitlines():
            if line.strip():
                assert line in after

    def test_primary_jsonl_mirror_also_extended(self, temp_dir):
        """MELICA writes appended content to both raw-transcript.jsonl AND
        <primary-uuid>.jsonl — both files hold the same concatenated stream."""
        cluster_dir, _ = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)

        raw = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")
        primary = (cluster_dir / "aaa-uuid.jsonl").read_text(encoding="utf-8")
        assert raw == primary

    def test_ac4_4_idempotent_when_uuid_already_constituent(self, temp_dir):
        """AC4.4: same-UUID re-extend is a no-op — does NOT duplicate the
        _constituent_sessions entry and does NOT re-append content. Matches
        stitch_archive_extend.py lines 189-192 exactly."""
        cluster_dir, _ = self._seed_cluster(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "ccc-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)
        meta_after_first = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )
        raw_after_first = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")

        # Second call with the same UUID — must be no-op.
        extend_cluster(cluster_dir, "ccc-uuid", new_path, quiet=True)
        meta_after_second = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )
        raw_after_second = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")

        # Constituent list length unchanged, content byte-identical.
        assert (
            meta_after_second["_constituent_sessions"]
            == meta_after_first["_constituent_sessions"]
        )
        assert raw_after_first == raw_after_second


class TestPromoteSingletonToCluster:
    """auto-stitch.AC4.1/AC4.3 — promoting an archived singleton to a stitched
    cluster when a chain-mate arrives via the hook path."""

    def _seed_singleton(
        self,
        temp_dir: Path,
        session_id: str = "old-singleton-uuid",
        custom_title: str = "feat-x",
        started_at: str = "2026-04-24T10:00:00Z",
        ended_at: str = "2026-04-24T11:00:00Z",
    ) -> tuple[Path, Path, Path]:
        """Create a real singleton archive via stitch_cluster on N=1 by faking
        a single-member call... no, stitch_cluster requires N>=2. Build the
        singleton structure directly: one transcript + one archive dir with a
        singleton-shape meta and a manifest entry.

        Returns (archive_dir, singleton_dir, original_transcript_path).
        """
        archive_dir = temp_dir / "archives"
        archive_dir.mkdir(parents=True, exist_ok=True)
        transcript = _new_session_jsonl(
            temp_dir, session_id, custom_title, started_at, ended_at,
            user_messages=2, assistant_messages=3,
        )
        result = archive(
            session_id=session_id,
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )
        assert result is not None
        return archive_dir, result, transcript

    def test_ac4_1_creates_cluster_dir_with_stitched_suffix(self, temp_dir):
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )

        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )
        assert cluster_dir is not None
        assert cluster_dir.name.endswith("-stitched")
        # Original singleton dir is gone (renamed in place).
        assert not singleton_dir.exists() or singleton_dir == cluster_dir
        assert cluster_dir.exists()

    def test_ac4_1_meta_rewritten_to_stitched_schema(self, temp_dir):
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )

        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert meta["archive"]["stitched"] is True
        # Both UUIDs in _constituent_sessions, chronologically ranked.
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert "old-singleton-uuid" in ids
        assert "new-uuid" in ids
        # Old singleton started 2026-04-24, new started 2026-05-16 → old is rank 1.
        assert meta["_constituent_sessions"][0]["id"] == "old-singleton-uuid"
        assert meta["_constituent_sessions"][0]["rank"] == 1
        assert meta["_constituent_sessions"][1]["id"] == "new-uuid"
        assert meta["_constituent_sessions"][1]["rank"] == 2

    def test_ac4_1_manifest_fans_both_uuids_to_cluster(self, temp_dir):
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )

        manifest = _catalog.load_manifest(archive_dir)
        assert manifest["old-singleton-uuid"] == str(cluster_dir)
        assert manifest["new-uuid"] == str(cluster_dir)

    def test_ac4_3_singleton_raw_content_preserved(self, temp_dir):
        """AC4.3: promotion never deletes constituent data. The old singleton's
        raw-transcript.jsonl content must appear in the cluster's concatenated raw."""
        archive_dir, singleton_dir, original = self._seed_singleton(temp_dir)
        singleton_raw = (singleton_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")

        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )

        concatenated = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")
        # Singleton content appears verbatim somewhere in the concatenated stream.
        for line in singleton_raw.splitlines():
            if line.strip():
                assert line in concatenated, (
                    f"singleton line missing from concatenated raw: {line[:60]!r}"
                )

    def test_ac4_3_new_content_appended(self, temp_dir):
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        new_content = new_path.read_text(encoding="utf-8")
        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )

        concatenated = (cluster_dir / "raw-transcript.jsonl").read_text(encoding="utf-8")
        for line in new_content.splitlines():
            if line.strip():
                assert line in concatenated

    def test_primary_jsonl_named_after_chronological_first(self, temp_dir):
        """artifacts.primary_jsonl points at the earliest constituent's UUID file."""
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        new_path = _new_session_jsonl(
            temp_dir, "new-uuid", "feat-x",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        cluster_dir = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", new_path, quiet=True,
        )
        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert meta["artifacts"]["primary_jsonl"] == "old-singleton-uuid.jsonl"
        assert (cluster_dir / "old-singleton-uuid.jsonl").exists()

    def test_returns_none_if_new_transcript_missing(self, temp_dir):
        archive_dir, singleton_dir, _ = self._seed_singleton(temp_dir)
        missing = temp_dir / "does-not-exist.jsonl"
        result = promote_singleton_to_cluster(
            singleton_dir, "new-uuid", missing, quiet=True,
        )
        assert result is None
        # Singleton untouched on failure.
        assert singleton_dir.exists()
        meta = json.loads((singleton_dir / "session.meta.json").read_text(encoding="utf-8"))
        assert "stitched" not in meta["archive"] or meta["archive"]["stitched"] is False


class TestArchivePromotionPath:
    """auto-stitch.AC4 integration — archive() dispatches to extend/promote based
    on customTitle match against existing archives in the same project."""

    def test_two_sessions_same_custom_title_produce_one_cluster(self, temp_dir):
        archive_dir = temp_dir / "archives"
        transcript_a = _new_session_jsonl(
            temp_dir, "uuid-a", "feat-shared",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )
        transcript_b = _new_session_jsonl(
            temp_dir, "uuid-b", "feat-shared",
            "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
        )

        out_a = archive(
            session_id="uuid-a",
            transcript_path=transcript_a,
            archive_dir=archive_dir,
            quiet=True,
        )
        assert out_a is not None
        assert not out_a.name.endswith("-stitched"), "first archive is a singleton"

        out_b = archive(
            session_id="uuid-b",
            transcript_path=transcript_b,
            archive_dir=archive_dir,
            quiet=True,
        )
        assert out_b is not None
        assert out_b.name.endswith("-stitched"), (
            "second archive with matching customTitle must produce a cluster"
        )
        # Old singleton dir gone — it was renamed during promotion.
        assert not out_a.exists()
        # Manifest fans both UUIDs to the same cluster dir.
        manifest = _catalog.load_manifest(archive_dir)
        assert manifest["uuid-a"] == str(out_b)
        assert manifest["uuid-b"] == str(out_b)
        meta = json.loads((out_b / "session.meta.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert ids == ["uuid-a", "uuid-b"]

    def test_three_sessions_same_custom_title_produce_one_cluster_of_three(self, temp_dir):
        archive_dir = temp_dir / "archives"
        for uid, day in [("a", "2026-04-24"), ("b", "2026-05-01"), ("c", "2026-05-16")]:
            transcript = _new_session_jsonl(
                temp_dir, f"uuid-{uid}", "feat-triple",
                f"{day}T10:00:00Z", f"{day}T11:00:00Z",
            )
            archive(
                session_id=f"uuid-{uid}",
                transcript_path=transcript,
                archive_dir=archive_dir,
                quiet=True,
            )

        manifest = _catalog.load_manifest(archive_dir)
        cluster_dir = Path(manifest["uuid-a"])
        assert manifest["uuid-b"] == str(cluster_dir)
        assert manifest["uuid-c"] == str(cluster_dir)

        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert ids == ["uuid-a", "uuid-b", "uuid-c"]

    def test_different_custom_titles_stay_separate(self, temp_dir):
        archive_dir = temp_dir / "archives"
        ta = _new_session_jsonl(
            temp_dir, "uuid-x", "feat-one",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )
        tb = _new_session_jsonl(
            temp_dir, "uuid-y", "feat-two",
            "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
        )
        out_a = archive(session_id="uuid-x", transcript_path=ta,
                        archive_dir=archive_dir, quiet=True)
        out_b = archive(session_id="uuid-y", transcript_path=tb,
                        archive_dir=archive_dir, quiet=True)
        assert out_a != out_b
        assert not out_a.name.endswith("-stitched")
        assert not out_b.name.endswith("-stitched")

    def test_session_with_no_custom_title_stays_singleton(self, temp_dir):
        archive_dir = temp_dir / "archives"
        # Existing archive WITH customTitle.
        ta = _new_session_jsonl(
            temp_dir, "uuid-named", "feat-named",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )
        archive(session_id="uuid-named", transcript_path=ta,
                archive_dir=archive_dir, quiet=True)
        # Incoming without customTitle must NOT cluster with anything.
        tb = _new_session_jsonl(
            temp_dir, "uuid-anon", None,
            "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
        )
        out_b = archive(session_id="uuid-anon", transcript_path=tb,
                        archive_dir=archive_dir, quiet=True)
        assert out_b is not None
        assert not out_b.name.endswith("-stitched")

    def test_ac4_4_same_uuid_re_archive_does_not_duplicate(self, temp_dir):
        """AC4.4: After promotion, re-archiving the SAME constituent UUID does
        not duplicate _constituent_sessions and does not corrupt the cluster."""
        archive_dir = temp_dir / "archives"
        ta = _new_session_jsonl(
            temp_dir, "uuid-a", "feat-x",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )
        tb = _new_session_jsonl(
            temp_dir, "uuid-b", "feat-x",
            "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
        )
        archive(session_id="uuid-a", transcript_path=ta,
                archive_dir=archive_dir, quiet=True)
        cluster_dir = archive(session_id="uuid-b", transcript_path=tb,
                              archive_dir=archive_dir, quiet=True)
        assert cluster_dir is not None
        assert cluster_dir.name.endswith("-stitched")
        meta_before = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )

        # Re-archive uuid-b — should not change anything material about cluster state.
        archive(session_id="uuid-b", transcript_path=tb,
                archive_dir=archive_dir, quiet=True)
        meta_after = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )
        assert (
            meta_after["_constituent_sessions"]
            == meta_before["_constituent_sessions"]
        )


# =============================================================================
# Phase 5 — manual stitch CLI for edge cases (AC5.1–AC5.4). The CLI verb is
# tested separately in tests/test_cli.py; here we exercise the orchestration
# function stitch_sessions() that the CLI verb delegates to.
# =============================================================================


def _archive_singleton(
    temp_dir: Path,
    archive_dir: Path,
    session_id: str,
    custom_title: str | None,
    started_at: str,
    ended_at: str,
) -> Path:
    """Helper: archive a single session and return its archive dir."""
    transcript = _new_session_jsonl(
        temp_dir, session_id, custom_title, started_at, ended_at,
        user_messages=2, assistant_messages=3,
    )
    result = archive(
        session_id=session_id,
        transcript_path=transcript,
        archive_dir=archive_dir,
        quiet=True,
    )
    assert result is not None
    return result


class TestStitchSessions:
    """auto-stitch.AC5 — manual stitch orchestration."""

    def test_ac5_1_extend_existing_cluster_with_new_source(self, temp_dir):
        """AC5.1: stitch_sessions on an existing cluster adds the source as a
        new constituent and fans the manifest."""
        archive_dir = temp_dir / "archives"
        # Build a 2-member cluster via auto-stitch.
        ta = _new_session_jsonl(temp_dir, "uuid-a", "feat-x",
                                 "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        tb = _new_session_jsonl(temp_dir, "uuid-b", "feat-x",
                                 "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        archive(session_id="uuid-a", transcript_path=ta,
                archive_dir=archive_dir, quiet=True)
        cluster_dir = archive(session_id="uuid-b", transcript_path=tb,
                              archive_dir=archive_dir, quiet=True)

        # A brand-new source that does NOT share customTitle — the manual stitch
        # CLI ignores customTitle (DR4); we attach by force.
        new_path = _new_session_jsonl(
            temp_dir, "uuid-z", "different-title",
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        result = stitch_sessions(
            target_uuid="uuid-a",  # any constituent UUID resolves to the cluster
            source_specs=[("uuid-z", new_path)],
            archive_dir=archive_dir,
            quiet=True,
        )

        assert result == cluster_dir
        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert "uuid-z" in ids
        manifest = _catalog.load_manifest(archive_dir)
        assert manifest["uuid-z"] == str(cluster_dir)

    def test_ac5_2_promote_singleton_target_then_extend(self, temp_dir):
        """AC5.2: stitch_sessions on a singleton target promotes it to a
        cluster as part of the operation."""
        archive_dir = temp_dir / "archives"
        singleton_dir = _archive_singleton(
            temp_dir, archive_dir, "uuid-solo", "feat-solo",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )

        new_path = _new_session_jsonl(
            temp_dir, "uuid-new", None,
            "2026-05-16T10:00:00Z", "2026-05-16T11:00:00Z",
        )
        result = stitch_sessions(
            target_uuid="uuid-solo",
            source_specs=[("uuid-new", new_path)],
            archive_dir=archive_dir,
            quiet=True,
        )

        assert result is not None
        assert result.name.endswith("-stitched"), (
            f"target should have been promoted to cluster; got {result.name}"
        )
        # Old singleton dir no longer exists (renamed).
        assert not singleton_dir.exists() or singleton_dir == result
        manifest = _catalog.load_manifest(archive_dir)
        assert manifest["uuid-solo"] == str(result)
        assert manifest["uuid-new"] == str(result)

    def test_ac5_2_fold_existing_singleton_into_cluster(self, temp_dir):
        """AC5.2 alt scenario: source UUID is itself an existing singleton.
        Stitch must fold its content into the target cluster and remove the
        orphaned singleton dir. Models the MELICA dad509ba case — a legacy
        singleton attached to an auto-stitched cluster."""
        archive_dir = temp_dir / "archives"
        # Build a cluster of 2 via auto-stitch (different customTitle from below).
        ta = _new_session_jsonl(temp_dir, "uuid-cluster-a", "feat-cluster",
                                 "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        tb = _new_session_jsonl(temp_dir, "uuid-cluster-b", "feat-cluster",
                                 "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        archive(session_id="uuid-cluster-a", transcript_path=ta,
                archive_dir=archive_dir, quiet=True)
        cluster_dir = archive(session_id="uuid-cluster-b", transcript_path=tb,
                              archive_dir=archive_dir, quiet=True)

        # Independent singleton (legacy, no customTitle).
        legacy_singleton_dir = _archive_singleton(
            temp_dir, archive_dir, "uuid-legacy", None,
            "2026-04-20T10:00:00Z", "2026-04-20T11:00:00Z",
        )
        assert legacy_singleton_dir.exists()

        # Stitch legacy into cluster.
        result = stitch_sessions(
            target_uuid="uuid-cluster-a",
            source_specs=[
                ("uuid-legacy", legacy_singleton_dir / "raw-transcript.jsonl"),
            ],
            archive_dir=archive_dir,
            quiet=True,
        )
        assert result == cluster_dir

        # Cluster has all three constituents.
        meta = json.loads((cluster_dir / "session.meta.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert set(ids) == {"uuid-cluster-a", "uuid-cluster-b", "uuid-legacy"}

        # Old legacy singleton dir is gone.
        assert not legacy_singleton_dir.exists(), (
            "stitched-away singleton dir must be cleaned up"
        )

        # Manifest fans legacy to the cluster, not its old dir.
        manifest = _catalog.load_manifest(archive_dir)
        assert manifest["uuid-legacy"] == str(cluster_dir)

    def test_ac5_3_missing_target_returns_none_and_errors(self, temp_dir, capsys):
        archive_dir = temp_dir / "archives"
        # Seed something so archive_dir exists but the target UUID isn't there.
        _archive_singleton(temp_dir, archive_dir, "uuid-known", "anything",
                            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        new_path = _new_session_jsonl(
            temp_dir, "uuid-source", None,
            "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z",
        )

        capsys.readouterr()  # discard noise from seeding
        result = stitch_sessions(
            target_uuid="nonexistent-uuid",
            source_specs=[("uuid-source", new_path)],
            archive_dir=archive_dir,
            quiet=False,
        )
        assert result is None
        captured = capsys.readouterr()
        assert "no archive found" in captured.err.lower()
        assert "nonexistent-uuid" in captured.err

    def test_ac5_4_already_constituent_source_is_noop(self, temp_dir, capsys):
        """AC5.4: attempting to stitch a UUID that's already a constituent
        of the target cluster is a no-op (warns, but does not error or
        duplicate the entry)."""
        archive_dir = temp_dir / "archives"
        ta = _new_session_jsonl(temp_dir, "uuid-a", "feat-x",
                                 "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z")
        tb = _new_session_jsonl(temp_dir, "uuid-b", "feat-x",
                                 "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
        archive(session_id="uuid-a", transcript_path=ta,
                archive_dir=archive_dir, quiet=True)
        cluster_dir = archive(session_id="uuid-b", transcript_path=tb,
                              archive_dir=archive_dir, quiet=True)
        meta_before = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )

        # uuid-b is already a constituent; attempt to re-stitch.
        # Source JSONL doesn't matter — we should never read it.
        capsys.readouterr()
        result = stitch_sessions(
            target_uuid="uuid-a",
            source_specs=[("uuid-b", tb)],
            archive_dir=archive_dir,
            quiet=False,
        )
        assert result == cluster_dir
        meta_after = json.loads(
            (cluster_dir / "session.meta.json").read_text(encoding="utf-8")
        )
        assert (
            meta_after["_constituent_sessions"]
            == meta_before["_constituent_sessions"]
        )
        captured = capsys.readouterr()
        assert "already" in (captured.out + captured.err).lower()

    def test_multiple_sources_per_invocation(self, temp_dir):
        """A single stitch_sessions call can attach N sources in one pass."""
        archive_dir = temp_dir / "archives"
        singleton_dir = _archive_singleton(
            temp_dir, archive_dir, "uuid-primary", "feat-multi",
            "2026-04-24T10:00:00Z", "2026-04-24T11:00:00Z",
        )

        srcs: list[tuple[str, Path]] = []
        for uid, day in [("x", "2026-05-01"), ("y", "2026-05-08"), ("z", "2026-05-16")]:
            p = _new_session_jsonl(
                temp_dir, f"uuid-{uid}", None,
                f"{day}T10:00:00Z", f"{day}T11:00:00Z",
            )
            srcs.append((f"uuid-{uid}", p))

        result = stitch_sessions(
            target_uuid="uuid-primary",
            source_specs=srcs,
            archive_dir=archive_dir,
            quiet=True,
        )
        assert result is not None
        assert result.name.endswith("-stitched")
        meta = json.loads((result / "session.meta.json").read_text(encoding="utf-8"))
        ids = [c["id"] for c in meta["_constituent_sessions"]]
        assert set(ids) == {"uuid-primary", "uuid-x", "uuid-y", "uuid-z"}
        # Ranks chronological — primary is earliest.
        assert meta["_constituent_sessions"][0]["id"] == "uuid-primary"
