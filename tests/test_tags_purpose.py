"""Tests for tags and purpose parameter flow."""

import tempfile
from pathlib import Path

from claude_transcript_archive.metadata import create_session_metadata


def _make_stats():
    return {
        "turns": 1,
        "human_messages": 1,
        "assistant_messages": 0,
        "thinking_blocks": 0,
        "tool_calls": {"total": 0, "by_type": {}},
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
        "started_at": "2024-01-01T10:00:00",
        "ended_at": "2024-01-01T10:01:00",
        "duration_minutes": 1,
        "model": "test",
    }


def _make_transcript():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
        f.write('{"type":"user","message":{"content":"hi"}}\n')
    return Path(f.name)


class TestTagsPurposeInMetadata:
    def test_tags_in_session_metadata(self):
        """AC2.2: Tags appear in session.meta.json."""
        transcript = _make_transcript()
        try:
            metadata = create_session_metadata(
                session_id="test",
                transcript_path=transcript,
                stats=_make_stats(),
                title="Test",
                artifacts={"created": [], "modified": [], "referenced": []},
                relationship_hints={},
                plan_files=[],
                directory_name="test-dir",
                tags=["research", "analysis"],
                purpose="Integration testing",
            )
            assert metadata["auto_generated"]["tags"] == ["research", "analysis"]
            assert metadata["auto_generated"]["purpose"] == "Integration testing"
        finally:
            transcript.unlink()

    def test_tags_default_empty(self):
        """No tags -> empty list."""
        transcript = _make_transcript()
        try:
            metadata = create_session_metadata(
                session_id="test",
                transcript_path=transcript,
                stats=_make_stats(),
                title="Test",
                artifacts={"created": [], "modified": [], "referenced": []},
                relationship_hints={},
                plan_files=[],
                directory_name="test-dir",
            )
            assert metadata["auto_generated"]["tags"] == []
            assert metadata["auto_generated"]["purpose"] == ""
        finally:
            transcript.unlink()

    def test_tags_none_gives_empty_list(self):
        """Explicit None for tags -> empty list."""
        transcript = _make_transcript()
        try:
            metadata = create_session_metadata(
                session_id="test",
                transcript_path=transcript,
                stats=_make_stats(),
                title="Test",
                artifacts={"created": [], "modified": [], "referenced": []},
                relationship_hints={},
                plan_files=[],
                directory_name="test-dir",
                tags=None,
                purpose=None,
            )
            assert metadata["auto_generated"]["tags"] == []
            assert metadata["auto_generated"]["purpose"] == ""
        finally:
            transcript.unlink()
