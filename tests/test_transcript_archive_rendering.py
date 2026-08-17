"""Behavioral and property tests for transcript Markdown rendering."""

import re
from collections import Counter
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from claude_transcript_archive.model import (
    Boundary,
    CodexGitEvidence,
    Omission,
    Session,
    SourceLocation,
    SourceTool,
    ToolCallSummary,
    Turn,
)
from claude_transcript_archive.render import normalize_text, render_session


def _session(
    *events: Turn | Boundary | Omission,
    source: SourceTool = "claude",
) -> Session:
    codex_git = None
    branches = ("main", "feature")
    if source == "codex":
        codex_git = CodexGitEvidence(
            branch="feature",
            commit="abc123",
            remote="git@example.invalid:org/repo.git",
        )
        branches = ()
    return Session(
        source=source,
        session_id="session-1",
        source_path=Path("/source/session-1.jsonl"),
        started_at="2026-07-20T01:00:00Z",
        ended_at="2026-07-20T02:00:00Z",
        working_directories=("/repo", "/repo/worktree"),
        claude_branches=branches,
        codex_git=codex_git,
        events=events,
    )


def test_render_is_stable_readable_and_hygienic() -> None:
    events = (
        Turn(
            source="claude",
            session_id="session-1",
            location=SourceLocation(1, "user-1"),
            timestamp="2026-07-20T01:00:00Z",
            role="user",
            text_blocks=("Question with trailing spaces  \r\nand ``` fence\r\n",),
            branch="main",
        ),
        Turn(
            source="claude",
            session_id="session-1",
            location=SourceLocation(2, "assistant-1"),
            timestamp="2026-07-20T01:00:01Z",
            role="assistant",
            text_blocks=("Answer",),
            tool_calls=(ToolCallSummary("Read", "Read src/example.py"),),
            branch="feature",
        ),
        Omission(
            source="claude",
            session_id="session-1",
            location=SourceLocation(3, "thinking-1"),
            timestamp="2026-07-20T01:00:02Z",
            category="thinking",
            reason="Private model reasoning is not archived",
        ),
        Boundary(
            source="claude",
            session_id="session-1",
            location=SourceLocation(4, "compact-1"),
            timestamp="2026-07-20T01:00:03Z",
            kind="compaction",
            label="Claude context compacted (auto)",
        ),
    )

    first = render_session(_session(*events))
    second = render_session(_session(*events))

    assert first == second
    assert len(first) == 1
    part = first[0]
    assert part.filename == "transcript.md"
    assert part.content.endswith("\n")
    assert not part.content.endswith("\n\n")
    assert all(line == line.rstrip() for line in part.content.removesuffix("\n").splitlines())
    assert "## User" in part.content
    assert "## Assistant" in part.content
    assert "Read src/example.py" in part.content
    assert "## Context boundary" in part.content
    assert "Claude context compacted (auto)" in part.content
    assert "| thinking | 1 | Private model reasoning is not archived |" in (part.content)
    assert "private reasoning" not in part.content
    assert "summary.md" not in part.content
    assert "````text" in part.content
    answer_line = part.content[: part.content.index("Answer")].count("\n") + 1
    assert part.source_for_line(answer_line) == SourceLocation(2, "assistant-1")


def test_codex_header_does_not_claim_per_turn_branch_attribution() -> None:
    event = Turn(
        source="codex",
        session_id="session-1",
        location=SourceLocation(2),
        timestamp="2026-07-20T01:00:01Z",
        role="assistant",
        text_blocks=("Answer",),
    )

    content = render_session(_session(event, source="codex"))[0].content

    assert "Session-start Git evidence, not per-turn attribution" in content
    assert "- Branch: feature" in content
    assert "- Commit: abc123" in content
    assert "Branch evidence:" not in content


def test_render_labels_harness_turn_without_user_attribution() -> None:
    event = Turn(
        source="claude",
        session_id="session-1",
        location=SourceLocation(2, "harness-1"),
        timestamp="2026-07-20T01:00:01Z",
        role="user",
        text_blocks=("<task-notification>Background result",),
        branch="feature",
        harness_identification="claude-prompt-source",
    )

    content = render_session(_session(event))[0].content

    assert "## Harness" in content
    assert "## User" not in content
    assert "<task-notification>Background result" in content


def test_render_distinguishes_rollback_from_compaction_boundary() -> None:
    events = (
        Boundary(
            source="codex",
            session_id="session-1",
            location=SourceLocation(2),
            timestamp="2026-07-20T01:00:01Z",
            kind="compaction",
            label="Codex context compacted",
        ),
        Boundary(
            source="codex",
            session_id="session-1",
            location=SourceLocation(3),
            timestamp="2026-07-20T01:00:02Z",
            kind="rollback",
            label="Codex thread rolled back",
        ),
    )

    content = render_session(_session(*events, source="codex"))[0].content

    assert content.count("## Context boundary") == 2
    assert "- Change: Codex context compacted" in content
    assert "- Change: Codex thread rolled back" in content


@given(st.text())
def test_normalization_is_idempotent_and_has_no_trailing_whitespace(
    value: str,
) -> None:
    normalized = normalize_text(value)

    assert normalize_text(normalized) == normalized
    assert "\r" not in normalized
    assert all(line == line.rstrip() for line in normalized.split("\n"))


def test_real_size_session_remains_one_complete_transcript() -> None:
    payload = "x" * 1400
    events = tuple(
        Turn(
            source="claude",
            session_id="session-1",
            location=SourceLocation(index),
            timestamp=f"2026-07-20T01:{index % 60:02d}:00Z",
            role="assistant",
            text_blocks=(f"turn-{index} {payload}",),
            branch="main",
        )
        for index in range(1, 1153)
    )

    parts = render_session(_session(*events))

    assert [part.filename for part in parts] == ["transcript.md"]
    turn_counts = Counter(re.findall(r"turn-(\d+) ", parts[0].content))
    assert turn_counts == Counter(str(index) for index in range(1, 1153))
