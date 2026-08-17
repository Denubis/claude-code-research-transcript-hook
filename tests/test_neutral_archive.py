"""Behavioral contract for the provider-neutral Markdown archive."""

import json
from dataclasses import replace
from pathlib import Path

import claude_transcript_archive.cli as transcript_cli
from claude_transcript_archive.claude import adapt_claude_records
from claude_transcript_archive.cli import generate_sources
from claude_transcript_archive.codex import adapt_codex_records
from claude_transcript_archive.discovery import SourceDescriptor, SourceStamp
from claude_transcript_archive.model import Omission, RawRecord, Session, Turn
from claude_transcript_archive.redaction import SecretFinding
from claude_transcript_archive.render import render_session


class _CleanScanner:
    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        assert candidate
        return ()


def _raw(order: int, value: dict[str, object]) -> RawRecord:
    return RawRecord(order=order, value=value)


def _omission_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in session.events:
        if isinstance(event, Omission):
            counts[event.category] = counts.get(event.category, 0) + event.count
    return counts


def test_claude_parent_keeps_main_dialogue_and_counts_sidechain() -> None:
    records = (
        _raw(
            1,
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "user-1",
                "cwd": "/repo",
                "message": {"role": "user", "content": "Human request"},
            },
        ),
        _raw(
            2,
            {
                "type": "assistant",
                "sessionId": "claude-session",
                "uuid": "assistant-1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": "Main-agent answer"},
                    ],
                },
            },
        ),
        _raw(
            3,
            {
                "type": "assistant",
                "sessionId": "claude-session",
                "uuid": "worker-1",
                "isSidechain": True,
                "message": {"role": "assistant", "content": "Worker answer"},
            },
        ),
    )

    session = adapt_claude_records(
        records,
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    turns = [event for event in session.events if isinstance(event, Turn)]
    assert [(turn.role, turn.text_blocks) for turn in turns] == [
        ("user", ("Human request",)),
        ("assistant", ("Main-agent answer",)),
    ]
    assert _omission_counts(session) == {"thinking": 1, "sidechain": 1}


def test_codex_parent_keeps_canonical_dialogue_and_counts_agent_messages() -> None:
    records = (
        _raw(
            1,
            {
                "type": "session_meta",
                "payload": {
                    "id": "codex-session",
                    "session_id": "codex-session",
                    "cwd": "/repo",
                },
            },
        ),
        _raw(
            2,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Human request"}],
                },
            },
        ),
        _raw(
            3,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Main-agent answer"}],
                },
            },
        ),
        _raw(
            4,
            {
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "author": "main",
                    "recipient": "worker",
                    "content": [{"type": "input_text", "text": "Worker dispatch"}],
                },
            },
        ),
        _raw(
            5,
            {
                "type": "response_item",
                "payload": {"type": "reasoning", "summary": ["private reasoning"]},
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    turns = [event for event in session.events if isinstance(event, Turn)]
    assert [(turn.role, turn.text_blocks) for turn in turns] == [
        ("user", ("Human request",)),
        ("assistant", ("Main-agent answer",)),
    ]
    assert _omission_counts(session) == {"sub-agent-message": 1, "reasoning": 1}


def test_render_is_one_markdown_file_with_three_ps_frontmatter() -> None:
    session = Session(
        source="claude",
        session_id="session-1",
        source_path=Path("/source/session-1.jsonl"),
        started_at=None,
        ended_at=None,
        working_directories=("/repo",),
        events=(),
        prompt_summary="What was asked",
        process_summary="How the agents worked",
        provenance_summary="Why the session matters",
        needs_review=False,
    )

    parts = render_session(session)

    assert [part.filename for part in parts] == ["transcript.md"]
    content = parts[0].content
    assert 'prompt_summary: "What was asked"' in content
    assert 'process_summary: "How the agents worked"' in content
    assert 'provenance_summary: "Why the session matters"' in content
    assert "needs_review: false" in content


def _write_parent_and_sidechain(tmp_path: Path) -> SourceDescriptor:
    parent = tmp_path / "session.jsonl"
    parent.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "session-1",
                "uuid": "user-1",
                "cwd": "/repo",
                "message": {"role": "user", "content": "Human dialogue"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sidechain = tmp_path / "session-1" / "subagents" / "agent-worker.jsonl"
    sidechain.parent.mkdir(parents=True)
    sidechain.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "isSidechain": True,
                "message": {"content": "Worker-only dialogue"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stat = parent.stat()
    return SourceDescriptor(
        tool="claude",
        session_id="session-1",
        path=parent,
        working_directories=("/repo",),
        stamp=SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        sidechain_shards=(sidechain,),
    )


def test_generation_is_markdown_only_and_preserves_three_ps(tmp_path: Path) -> None:
    source = _write_parent_and_sidechain(tmp_path)
    archive = tmp_path / "archive"
    state = archive / ".state.json"

    first = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    transcript = archive / "sessions" / "claude" / "session-1" / "transcript.md"
    transcript_cli.update_three_ps(
        transcript,
        prompt="Prompt summary",
        process="Process summary",
        provenance="Provenance summary",
    )
    changed_source = replace(source, recovered_working_directories=("/repo/old",))
    second = generate_sources(
        (changed_source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )

    output = transcript.read_text(encoding="utf-8")
    assert first.rendered == 1
    assert second.rendered == 1
    assert "Worker-only dialogue" not in output
    assert "| sub-agent-traffic | 1 |" in output
    assert 'prompt_summary: "Prompt summary"' in output
    assert 'process_summary: "Process summary"' in output
    assert 'provenance_summary: "Provenance summary"' in output
    assert "needs_review: false" in output
    assert sorted(path.name for path in transcript.parent.iterdir()) == ["transcript.md"]


def test_update_command_edits_one_parent_without_source_stores(tmp_path: Path) -> None:
    archive = tmp_path / "ai_transcripts"
    transcript = archive / "sessions" / "codex" / "session-2" / "transcript.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        render_session(
            Session(
                source="codex",
                session_id="session-2",
                source_path=Path("/source/rollout.jsonl"),
                started_at=None,
                ended_at=None,
                working_directories=("/repo",),
                events=(),
            )
        )[0].content,
        encoding="utf-8",
    )

    result = transcript_cli.main(
        (
            "update",
            "--archive-root",
            str(archive),
            "--tool",
            "codex",
            "--session-id",
            "session-2",
            "--prompt",
            "Prompt",
            "--process",
            "Process",
            "--provenance",
            "Provenance",
        )
    )

    assert result == 0
    assert transcript_cli._load_three_ps(transcript) == (
        "Prompt",
        "Process",
        "Provenance",
    )


def test_update_command_reads_three_ps_from_structured_json(tmp_path: Path) -> None:
    archive = tmp_path / "ai_transcripts"
    transcript = archive / "sessions" / "claude" / "session-3" / "transcript.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        render_session(
            Session(
                source="claude",
                session_id="session-3",
                source_path=Path("/source/session.jsonl"),
                started_at=None,
                ended_at=None,
                working_directories=("/repo",),
                events=(),
            )
        )[0].content,
        encoding="utf-8",
    )
    metadata = tmp_path / "three-ps.json"
    metadata.write_text(
        json.dumps(
            {
                "prompt": "Prompt with $variables and `commands`",
                "process": "Process with 'quotes'",
                "provenance": 'Provenance with "quotes"',
            }
        ),
        encoding="utf-8",
    )

    result = transcript_cli.main(
        (
            "update",
            "--archive-root",
            str(archive),
            "--tool",
            "claude",
            "--session-id",
            "session-3",
            "--metadata-file",
            str(metadata),
        )
    )

    assert result == 0
    assert transcript_cli._load_three_ps(transcript) == (
        "Prompt with $variables and `commands`",
        "Process with 'quotes'",
        'Provenance with "quotes"',
    )
