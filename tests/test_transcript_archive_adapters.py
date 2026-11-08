"""Behavioral tests for transcript source adapters."""

from pathlib import Path

import pytest

from claude_transcript_archive.claude import adapt_claude_records
from claude_transcript_archive.codex import adapt_codex_records
from claude_transcript_archive.model import (
    AdapterError,
    Boundary,
    Omission,
    RawRecord,
    Turn,
)
from claude_transcript_archive.render import render_session


def _raw(order: int, value: dict[str, object]) -> RawRecord:
    return RawRecord(order=order, value=value)


def _turns(events: tuple[Turn | Boundary | Omission, ...]) -> list[Turn]:
    return [event for event in events if isinstance(event, Turn)]


def _boundaries(events: tuple[Turn | Boundary | Omission, ...]) -> list[Boundary]:
    return [event for event in events if isinstance(event, Boundary)]


def _omission_counts(
    events: tuple[Turn | Boundary | Omission, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if isinstance(event, Omission):
            counts[event.category] = counts.get(event.category, 0) + event.count
    return counts


def test_claude_renders_only_main_dialogue_and_keeps_compaction() -> None:
    records = (
        _raw(
            1,
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "user-1",
                "timestamp": "2026-07-20T01:00:00Z",
                "cwd": "/repo",
                "gitBranch": "main",
                "message": {"role": "user", "content": "Human request"},
            },
        ),
        _raw(
            2,
            {
                "type": "assistant",
                "sessionId": "claude-session",
                "uuid": "assistant-1",
                "timestamp": "2026-07-20T01:00:01Z",
                "cwd": "/repo/worktree",
                "gitBranch": "feature",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": "Main-agent answer"},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                            "input": {"file_path": "/private"},
                        },
                    ],
                },
            },
        ),
        _raw(
            3,
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "tool-result-1",
                "timestamp": "2026-07-20T01:00:02Z",
                "cwd": "/repo/worktree",
                "gitBranch": "feature",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "private output",
                        }
                    ],
                },
            },
        ),
        _raw(
            4,
            {
                "type": "assistant",
                "sessionId": "claude-session",
                "uuid": "sidechain-1",
                "timestamp": "2026-07-20T01:00:03Z",
                "cwd": "/repo/worktree",
                "gitBranch": "feature",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Sub-agent answer"}],
                },
            },
        ),
        _raw(
            5,
            {
                "type": "system",
                "subtype": "compact_boundary",
                "sessionId": "claude-session",
                "uuid": "compact-1",
                "timestamp": "2026-07-20T01:00:04Z",
                "cwd": "/repo/worktree",
                "gitBranch": "feature",
                "compactMetadata": {"trigger": "auto"},
            },
        ),
        _raw(
            6,
            {
                "type": "attachment",
                "sessionId": "claude-session",
                "uuid": "attachment-1",
                "timestamp": "2026-07-20T01:00:05Z",
                "cwd": "/repo/worktree",
                "gitBranch": "feature",
                "attachment": {"content": "private attachment"},
            },
        ),
    )

    session = adapt_claude_records(
        records,
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    turns = _turns(session.events)
    assert [
        (
            turn.role,
            turn.text_blocks,
            tuple(tool.summary for tool in turn.tool_calls),
        )
        for turn in turns
    ] == [
        ("user", ("Human request",), ()),
        ("assistant", ("Main-agent answer",), ("Read /private",)),
    ]
    assert [turn.branch for turn in turns] == ["main", "feature"]
    assert session.working_directories == ("/repo", "/repo/worktree")
    assert session.claude_branches == ("main", "feature")
    assert [boundary.kind for boundary in _boundaries(session.events)] == ["compaction"]
    assert _omission_counts(session.events) == {
        "thinking": 1,
        "tool-output": 1,
        "sidechain": 1,
        "operational": 1,
    }


def test_claude_omits_meta_user_text_and_unknown_operations() -> None:
    records = (
        _raw(
            1,
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "meta-1",
                "timestamp": "2026-07-20T01:00:00Z",
                "isMeta": True,
                "message": {"role": "user", "content": "Injected context"},
            },
        ),
        _raw(
            2,
            {
                "type": "queue-operation",
                "sessionId": "claude-session",
                "uuid": "queue-1",
                "timestamp": "2026-07-20T01:00:01Z",
            },
        ),
    )

    session = adapt_claude_records(
        records,
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"operational": 2}


def test_claude_counts_invented_record_type_as_unrecognised() -> None:
    record = _raw(
        1,
        {
            "type": "future-transcript-shape",
            "sessionId": "claude-session",
            "uuid": "future-1",
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    omissions = [event for event in session.events if isinstance(event, Omission)]
    assert [(event.category, event.reason) for event in omissions] == [
        (
            "unrecognised",
            "Unrecognised Claude source record type: future-transcript-shape",
        )
    ]


def test_claude_labels_prompt_source_system_as_harness() -> None:
    record = _raw(
        1,
        {
            "type": "user",
            "sessionId": "claude-session",
            "uuid": "notice-1",
            "promptSource": "system",
            "message": {
                "role": "user",
                "content": "A background agent was stopped by the user",
            },
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert [
        (turn.role, turn.harness_identification, turn.text_blocks)
        for turn in _turns(session.events)
    ] == [
        (
            "user",
            "claude-prompt-source",
            ("A background agent was stopped by the user",),
        )
    ]


@pytest.mark.parametrize(
    "tag",
    (
        "<task-notification>",
        "<command-name>",
        "<local-command-stdout>",
        "<bash-input>",
        "<bash-stdout>",
        "<command-message>",
    ),
)
def test_claude_labels_exact_leading_harness_envelopes(tag: str) -> None:
    record = _raw(
        1,
        {
            "type": "user",
            "sessionId": "claude-session",
            "uuid": "envelope-1",
            "message": {"role": "user", "content": f"{tag}generated payload"},
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    turn = _turns(session.events)[0]
    assert turn.role == "user"
    assert turn.harness_identification == "leading-envelope"


def test_claude_prompt_source_takes_precedence_over_leading_envelope() -> None:
    record = _raw(
        1,
        {
            "type": "user",
            "sessionId": "claude-session",
            "uuid": "notice-1",
            "promptSource": "system",
            "message": {
                "role": "user",
                "content": "<task-notification>generated payload",
            },
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events)[0].harness_identification == "claude-prompt-source"


def test_claude_keeps_mid_message_envelope_quote_as_user() -> None:
    record = _raw(
        1,
        {
            "type": "user",
            "sessionId": "claude-session",
            "uuid": "user-1",
            "message": {
                "role": "user",
                "content": "The log quoted <task-notification> in the middle.",
            },
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    turn = _turns(session.events)[0]
    assert turn.role == "user"
    assert turn.harness_identification is None


def test_claude_keeps_text_and_records_image_omission() -> None:
    record = _raw(
        7,
        {
            "type": "user",
            "sessionId": "claude-session",
            "uuid": "user-7",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please inspect this image."},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "not-archived",
                        },
                    },
                ],
            },
        },
    )

    session = adapt_claude_records(
        (record,),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events)[0].text_blocks == ("Please inspect this image.",)
    assert _omission_counts(session.events) == {"image": 1}


def test_claude_rejects_unknown_conversation_content() -> None:
    record = _raw(
        7,
        {
            "type": "assistant",
            "sessionId": "claude-session",
            "uuid": "assistant-7",
            "message": {
                "role": "assistant",
                "content": [{"type": "new_dialogue_shape", "text": "unknown"}],
            },
        },
    )

    with pytest.raises(
        AdapterError,
        match=r"claude-session.*line 7.*new_dialogue_shape",
    ):
        adapt_claude_records(
            (record,),
            session_id="claude-session",
            source_path=Path("/source/claude-session.jsonl"),
        )


_TYPED_MID_TURN = "blargh, can we just have an allowlist?"


def _queued_message(
    order: int,
    prompt: object,
    *,
    origin: dict[str, object] | None,
    command_mode: str | None = "prompt",
    sidechain: bool = False,
    version: str | None = "2.1.318",
) -> RawRecord:
    attachment: dict[str, object] = {"type": "queued_command", "prompt": prompt}
    if origin is not None:
        attachment["origin"] = origin
    if command_mode is not None:
        attachment["commandMode"] = command_mode
    value: dict[str, object] = {
        "type": "attachment",
        "sessionId": "claude-session",
        "uuid": f"queued-{order}",
        "timestamp": f"2026-11-08T09:0{order}:00Z",
        "gitBranch": "main",
        "isSidechain": sidechain,
        "attachment": attachment,
    }
    if version is not None:
        value["version"] = version
    return _raw(order, value)


def _assistant_text(order: int, text: str) -> RawRecord:
    return _raw(
        order,
        {
            "type": "assistant",
            "sessionId": "claude-session",
            "uuid": f"assistant-{order}",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        },
    )


def test_claude_renders_a_message_typed_mid_turn_once_as_a_user_turn() -> None:
    records = (
        _assistant_text(1, "Working on the scanner."),
        _raw(
            2,
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "sessionId": "claude-session",
                "content": _TYPED_MID_TURN,
            },
        ),
        _raw(
            3,
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "tool-result-3",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": f"The user sent: {_TYPED_MID_TURN}",
                        }
                    ],
                },
            },
        ),
        _queued_message(4, _TYPED_MID_TURN, origin={"kind": "human"}),
        _raw(
            5,
            {
                "type": "queue-operation",
                "operation": "remove",
                "sessionId": "claude-session",
                "content": _TYPED_MID_TURN,
            },
        ),
        _assistant_text(6, "Adding the allowlist."),
    )

    session = adapt_claude_records(
        records,
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert [
        (turn.role, turn.text_blocks, turn.harness_identification, turn.location.order)
        for turn in _turns(session.events)
    ] == [
        ("assistant", ("Working on the scanner.",), None, 1),
        ("user", (_TYPED_MID_TURN,), None, 4),
        ("assistant", ("Adding the allowlist.",), None, 6),
    ]
    assert render_session(session)[0].content.count(_TYPED_MID_TURN) == 1


@pytest.mark.parametrize(
    ("origin", "command_mode"),
    (
        ({"kind": "peer", "from": "uds:/run/user/1000/cc-socks/1.sock"}, "prompt"),
        ({"kind": "task-notification"}, "task-notification"),
        (None, "task-notification"),
        ({"kind": "coordinator"}, None),
        ({"kind": "auto-continuation"}, "prompt"),
        (None, "prompt"),
    ),
    ids=("peer", "task", "unmarked-task", "coordinator", "auto-continuation", "unmarked"),
)
def test_claude_omits_queued_messages_not_marked_as_the_humans(
    origin: dict[str, object] | None,
    command_mode: str | None,
) -> None:
    session = adapt_claude_records(
        (_queued_message(1, "Not typed by the human", origin=origin, command_mode=command_mode),),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"operational": 1}


@pytest.mark.parametrize("version", ("2.1.97", "2.1.179"))
def test_claude_renders_unmarked_queued_prompts_from_before_sender_marking(version: str) -> None:
    session = adapt_claude_records(
        (_queued_message(1, "and fix all", origin=None, version=version),),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert [(turn.role, turn.text_blocks) for turn in _turns(session.events)] == [
        ("user", ("and fix all",))
    ]


@pytest.mark.parametrize(
    ("version", "command_mode"),
    (
        ("2.1.180", "prompt"),
        ("2.1.181", "prompt"),
        (None, "prompt"),
        ("2.1.100", "task-notification"),
    ),
    ids=("2.1.180", "2.1.181", "no-version", "old-task-notification"),
)
def test_claude_omits_other_unmarked_queued_records(
    version: str | None,
    command_mode: str,
) -> None:
    session = adapt_claude_records(
        (
            _queued_message(
                1, "Not typed by the human", origin=None, command_mode=command_mode, version=version
            ),
        ),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"operational": 1}


def test_claude_keeps_a_queued_messages_text_and_records_its_image_omission() -> None:
    prompt = [
        {"type": "text", "text": "Look at this screenshot."},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
    ]

    session = adapt_claude_records(
        (_queued_message(1, prompt, origin={"kind": "human"}),),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert [(turn.role, turn.text_blocks) for turn in _turns(session.events)] == [
        ("user", ("Look at this screenshot.",))
    ]
    assert _omission_counts(session.events) == {"image": 1}


def test_claude_does_not_render_a_queued_message_sent_to_a_sub_agent() -> None:
    session = adapt_claude_records(
        (_queued_message(1, "Sub-agent instruction", origin={"kind": "human"}, sidechain=True),),
        session_id="claude-session",
        source_path=Path("/source/claude-session.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"sidechain": 1}


def test_codex_renders_only_canonical_human_and_main_agent_messages() -> None:
    records = (
        _raw(
            1,
            {
                "type": "session_meta",
                "timestamp": "2026-07-20T02:00:00Z",
                "payload": {
                    "id": "codex-session",
                    "session_id": "codex-session",
                    "cwd": "/repo",
                    "git": {
                        "branch": "feature",
                        "commit_hash": "abc123",
                        "repository_url": "git@example.invalid:org/repo.git",
                    },
                },
            },
        ),
        _raw(
            2,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:01Z",
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
                "type": "event_msg",
                "timestamp": "2026-07-20T02:00:01Z",
                "payload": {"type": "user_message", "message": "Human request"},
            },
        ),
        _raw(
            4,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:02Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Main-agent answer"}],
                },
            },
        ),
        _raw(
            5,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:03Z",
                "payload": {
                    "type": "agent_message",
                    "author": "main",
                    "recipient": "worker",
                    "content": [
                        {"type": "input_text", "text": "Sub-agent dispatch"},
                        {
                            "type": "encrypted_content",
                            "encrypted_content": "opaque",
                        },
                    ],
                },
            },
        ),
        _raw(
            6,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:04Z",
                "payload": {"type": "reasoning", "summary": ["private"]},
            },
        ),
        _raw(
            7,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:05Z",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": '{"cmd":"private"}',
                },
            },
        ),
        _raw(
            8,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:06Z",
                "payload": {
                    "type": "function_call_output",
                    "output": "private output",
                },
            },
        ),
        _raw(
            9,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:07Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "input": "private input",
                },
            },
        ),
        _raw(
            10,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:08Z",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": "private output",
                },
            },
        ),
        _raw(
            11,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:09Z",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "Injected rules"}],
                },
            },
        ),
        _raw(
            12,
            {
                "type": "compacted",
                "timestamp": "2026-07-20T02:00:10Z",
                "payload": {"message": "private compaction payload"},
            },
        ),
        _raw(
            13,
            {
                "type": "event_msg",
                "timestamp": "2026-07-20T02:00:10Z",
                "payload": {"type": "context_compacted"},
            },
        ),
        _raw(
            14,
            {
                "type": "event_msg",
                "timestamp": "2026-07-20T02:00:11Z",
                "payload": {"type": "token_count", "info": {}},
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    turns = _turns(session.events)
    assert [
        (
            turn.role,
            turn.text_blocks,
            tuple(tool.summary for tool in turn.tool_calls),
        )
        for turn in turns
    ] == [
        ("user", ("Human request",), ()),
        ("assistant", ("Main-agent answer",), ()),
        ("assistant", (), ("Ran private",)),
        ("assistant", (), ("Applied patch",)),
    ]
    assert all(turn.branch is None for turn in turns)
    assert session.codex_git is not None
    assert session.codex_git.branch == "feature"
    assert session.codex_git.commit == "abc123"
    assert session.codex_git.remote == "git@example.invalid:org/repo.git"
    assert [boundary.kind for boundary in _boundaries(session.events)] == ["compaction"]
    assert _omission_counts(session.events) == {
        "duplicate-event": 2,
        "sub-agent-message": 1,
        "reasoning": 1,
        "tool-output": 2,
        "developer-message": 1,
        "operational": 1,
    }


def test_codex_rejects_unknown_conversation_shape() -> None:
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
                    "type": "new_message_shape",
                    "content": [{"type": "input_text", "text": "unknown"}],
                },
            },
        ),
    )

    with pytest.raises(
        AdapterError,
        match=r"codex-session.*line 2.*new_message_shape",
    ):
        adapt_codex_records(
            records,
            session_id="codex-session",
            source_path=Path("/source/rollout.jsonl"),
        )


def test_codex_keeps_text_and_records_image_omission() -> None:
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
                    "content": [
                        {"type": "input_text", "text": "Please inspect this image."},
                        {
                            "type": "input_image",
                            "detail": "auto",
                            "image_url": "data:image/png;base64,not-archived",
                        },
                    ],
                },
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    assert _turns(session.events)[0].text_blocks == ("Please inspect this image.",)
    assert _omission_counts(session.events) == {"image": 1}


def test_codex_counts_invented_record_type_as_unrecognised() -> None:
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
                "type": "future-transcript-shape",
                "payload": {"type": "future-event"},
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    omissions = [event for event in session.events if isinstance(event, Omission)]
    assert [(event.category, event.reason) for event in omissions] == [
        (
            "unrecognised",
            "Unrecognised Codex source record type: future-transcript-shape",
        )
    ]


def test_codex_records_invented_event_type_in_unrecognised_reason() -> None:
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
                "type": "event_msg",
                "payload": {"type": "future-event"},
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    omissions = [event for event in session.events if isinstance(event, Omission)]
    assert [(event.category, event.reason) for event in omissions] == [
        (
            "unrecognised",
            "Unrecognised Codex source record type: event_msg/future-event",
        )
    ]


def test_codex_rollbacks_and_aborted_turns_have_distinct_treatment() -> None:
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
                "type": "compacted",
                "timestamp": "2026-07-20T02:00:00Z",
                "payload": {},
            },
        ),
        _raw(
            3,
            {
                "type": "event_msg",
                "timestamp": "2026-07-20T02:00:01Z",
                "payload": {"type": "thread_rolled_back"},
            },
        ),
        _raw(
            4,
            {
                "type": "event_msg",
                "timestamp": "2026-07-20T02:00:02Z",
                "payload": {"type": "turn_aborted"},
            },
        ),
        _raw(
            5,
            {
                "type": "response_item",
                "timestamp": "2026-07-20T02:00:03Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "<turn_aborted>Interrupted by the human",
                        }
                    ],
                },
            },
        ),
    )

    session = adapt_codex_records(
        records,
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    assert [(boundary.kind, boundary.label) for boundary in _boundaries(session.events)] == [
        ("compaction", "Codex context compacted"),
        ("rollback", "Codex thread rolled back"),
    ]
    assert _omission_counts(session.events) == {"aborted-turn": 1}
    assert [(turn.harness_identification, turn.text_blocks) for turn in _turns(session.events)] == [
        (
            "leading-envelope",
            ("<turn_aborted>Interrupted by the human",),
        )
    ]


def _codex_user_message_blocks(*text_blocks: str) -> tuple[RawRecord, RawRecord]:
    return (
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
                    "content": [{"type": "input_text", "text": text} for text in text_blocks],
                },
            },
        ),
    )


def _codex_user_message(text: str) -> tuple[RawRecord, RawRecord]:
    return _codex_user_message_blocks(text)


def test_codex_labels_turn_aborted_as_harness() -> None:
    session = adapt_codex_records(
        _codex_user_message("<turn_aborted>Interrupted by the human"),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    assert [
        (turn.role, turn.harness_identification, turn.text_blocks)
        for turn in _turns(session.events)
    ] == [
        (
            "user",
            "leading-envelope",
            ("<turn_aborted>Interrupted by the human",),
        )
    ]


@pytest.mark.parametrize(
    ("tag", "category"),
    (
        ("<environment_context>", "environment-context"),
        ("<recommended_plugins>", "recommended-plugins"),
    ),
)
def test_codex_omits_never_displayed_harness_context(
    tag: str,
    category: str,
) -> None:
    session = adapt_codex_records(
        _codex_user_message(f"{tag}injected payload"),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {category: 1}


@pytest.mark.parametrize(
    "text",
    (
        ("# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>\nRepository rules\n</INSTRUCTIONS>"),
        (
            " \n# AGENTS.md instructions\n\n"
            "<environment_context>\nRepository context\n</environment_context>"
        ),
    ),
)
def test_codex_omits_session_opening_harness_context(text: str) -> None:
    session = adapt_codex_records(
        _codex_user_message(text),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    rendered = render_session(session)[0].content
    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"session-opener": 1}
    assert "## User\n" not in rendered
    assert "## Harness\n" not in rendered


def test_codex_accepts_session_opener_marker_in_later_text_block() -> None:
    session = adapt_codex_records(
        _codex_user_message_blocks(
            "# AGENTS.md instructions for /repo",
            "<INSTRUCTIONS>Repository rules</INSTRUCTIONS>",
        ),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    assert _turns(session.events) == []
    assert _omission_counts(session.events) == {"session-opener": 1}


def test_codex_requires_session_opener_heading_in_first_text_block() -> None:
    session = adapt_codex_records(
        _codex_user_message_blocks(
            " \n",
            ("# AGENTS.md instructions for /repo\n<INSTRUCTIONS>Human example</INSTRUCTIONS>"),
        ),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    turn = _turns(session.events)[0]
    assert turn.role == "user"
    assert turn.harness_identification is None


@pytest.mark.parametrize(
    "text",
    (
        (
            "The log quotes # AGENTS.md instructions for /repo in a sentence.\n"
            "<INSTRUCTIONS>human-authored example</INSTRUCTIONS>"
        ),
        "# AGENTS.md instructions",
    ),
)
def test_codex_keeps_session_opener_near_misses_as_user(text: str) -> None:
    session = adapt_codex_records(
        _codex_user_message(text),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    turn = _turns(session.events)[0]
    assert turn.role == "user"
    assert turn.harness_identification is None


def test_codex_keeps_mid_message_envelope_quote_as_user() -> None:
    session = adapt_codex_records(
        _codex_user_message("The log quoted <turn_aborted> in the middle."),
        session_id="codex-session",
        source_path=Path("/source/rollout.jsonl"),
    )

    turn = _turns(session.events)[0]
    assert turn.role == "user"
    assert turn.harness_identification is None
