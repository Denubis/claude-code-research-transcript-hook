"""Codex rollout JSONL adapter.

# pattern: Functional Core
"""

import json
from typing import TYPE_CHECKING, Never, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

from .model import (
    AdapterError,
    Boundary,
    CodexGitEvidence,
    Omission,
    RawRecord,
    Session,
    SourceLocation,
    ToolCallSummary,
    TranscriptEvent,
    Turn,
)

_TOOL_CALLS = frozenset(
    {
        "function_call",
        "custom_tool_call",
        "tool_search_call",
        "web_search_call",
    }
)
_TOOL_OUTPUTS = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "tool_search_output",
    }
)
_DUPLICATE_EVENTS = frozenset({"user_message", "agent_message", "context_compacted"})
_OPERATIONAL_EVENT_TYPES = frozenset(
    {
        "mcp_tool_call_end",
        "patch_apply_end",
        "sub_agent_activity",
        "task_complete",
        "task_started",
        "thread_settings_applied",
        "token_count",
        "web_search_end",
    }
)
_OPERATIONAL_RECORD_TYPES = frozenset(
    {
        "inter_agent_communication_metadata",
        "session_meta",
        "turn_context",
        "world_state",
    }
)
_VISIBLE_HARNESS_ENVELOPES = ("<turn_aborted>",)
_HIDDEN_HARNESS_ENVELOPES = {
    "<environment_context>": (
        "environment-context",
        "Environment context injected by the harness was not displayed",
    ),
    "<recommended_plugins>": (
        "recommended-plugins",
        "Recommended plugin context injected by the harness was not displayed",
    ),
}
_SESSION_OPENER_HEADING = "# AGENTS.md instructions"
_SESSION_OPENER_MARKERS = ("<INSTRUCTIONS>", "<environment_context>")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload(record: RawRecord, session_id: str) -> dict[str, object]:
    payload = record.value.get("payload")
    if not isinstance(payload, dict):
        detail = f"Codex session {session_id} line {record.order} has no payload"
        raise AdapterError(detail)
    return cast("dict[str, object]", payload)


def _timestamp(record: RawRecord) -> str | None:
    return _optional_string(record.value.get("timestamp"))


def _location(record: RawRecord) -> SourceLocation:
    payload = record.value.get("payload")
    stable_id = None
    if isinstance(payload, dict):
        payload_value = cast("dict[str, object]", payload)
        stable_id = _optional_string(payload_value.get("id")) or _optional_string(
            payload_value.get("call_id")
        )
    return SourceLocation(order=record.order, stable_id=stable_id)


def _omission(
    record: RawRecord,
    session_id: str,
    category: str,
    reason: str,
) -> Omission:
    return Omission(
        source="codex",
        session_id=session_id,
        location=_location(record),
        timestamp=_timestamp(record),
        category=category,
        reason=reason,
    )


def _unknown_response(
    record: RawRecord,
    session_id: str,
    payload_type: object,
) -> Never:
    detail = (
        f"Codex session {session_id} line {record.order} has unknown "
        f"conversation response {payload_type}"
    )
    raise AdapterError(detail)


def _compact(value: object, *, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def _arguments(payload: dict[str, object]) -> dict[str, object]:
    raw = payload.get("arguments")
    if not isinstance(raw, str):
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _custom_summary(
    payload_type: object,
    name: str | None,
) -> str | None:
    if payload_type != "custom_tool_call":
        return None
    summaries = {"apply_patch": "Applied patch", "exec": "Ran code"}
    return summaries.get(name) if name is not None else None


def _search_summary(
    payload_type: object,
    namespace: str | None,
) -> str | None:
    if payload_type == "tool_search_call":
        return "Explored tools"
    if payload_type == "web_search_call" or namespace == "web":
        return "Searched the web"
    if namespace in {"mcp__context7", "mcp__openaiDeveloperDocs"}:
        return "Queried documentation"
    return None


def _named_summary(
    payload: dict[str, object],
    name: str | None,
    namespace: str | None,
) -> str | None:
    if name == "exec_command":
        command = _compact(_arguments(payload).get("cmd"))
        return f"Ran {command}" if command is not None else "Ran command"
    summaries = {
        "apply_patch": "Applied patch",
        "update_plan": "Updated plan",
        "wait": "Continued command",
        "write_stdin": "Continued command",
    }
    if name in summaries:
        return summaries[name]
    if namespace == "collaboration":
        return "Coordinated agents"
    return None


def _tool_summary(payload: dict[str, object]) -> ToolCallSummary:
    payload_type = payload.get("type")
    name = _optional_string(payload.get("name"))
    namespace = _optional_string(payload.get("namespace"))
    qualified = "/".join(part for part in (namespace, name) if part)
    summary = (
        _custom_summary(payload_type, name)
        or _search_summary(payload_type, namespace)
        or _named_summary(payload, name, namespace)
        or f"Used {qualified or 'tool'}"
    )
    return ToolCallSummary(name=qualified or str(payload_type), summary=summary)


def _message_text(
    record: RawRecord,
    session_id: str,
    payload: dict[str, object],
) -> tuple[str, ...]:
    content = payload.get("content")
    if not isinstance(content, list):
        _unknown_response(record, session_id, "message-without-content")
    text: list[str] = []
    for block in cast("list[object]", content):
        if not isinstance(block, dict):
            _unknown_response(record, session_id, type(block).__name__)
        block_value = cast("dict[str, object]", block)
        block_type = block_value.get("type")
        if block_type not in {"input_text", "output_text"}:
            _unknown_response(record, session_id, block_type)
        value = block_value.get("text")
        if not isinstance(value, str):
            _unknown_response(record, session_id, "text-without-string")
        text.append(value)
    return tuple(text)


def _leading_harness_envelope(text_blocks: tuple[str, ...]) -> str | None:
    """Return a known exact leading Codex harness envelope, if present.

    Codex exposes no authorship field equivalent to Claude's ``promptSource``.
    This closed tag list cannot detect an untagged injection, which remains
    indistinguishable from human text.
    """
    if not text_blocks:
        return None
    tags = (*_VISIBLE_HARNESS_ENVELOPES, *_HIDDEN_HARNESS_ENVELOPES)
    return next((tag for tag in tags if text_blocks[0].startswith(tag)), None)


def _is_session_opener(text_blocks: tuple[str, ...]) -> bool:
    """Identify the ruled Codex session-opening harness injection."""
    if not text_blocks:
        return False
    leading_text = text_blocks[0].lstrip()
    if not leading_text:
        return False
    heading = leading_text.splitlines()[0].rstrip()
    has_heading = heading == _SESSION_OPENER_HEADING or (
        heading.startswith(f"{_SESSION_OPENER_HEADING} for ")
        and bool(heading.removeprefix(f"{_SESSION_OPENER_HEADING} for ").strip())
    )
    return has_heading and any(
        marker in block for block in text_blocks for marker in _SESSION_OPENER_MARKERS
    )


def _adapt_message(
    record: RawRecord,
    session_id: str,
    payload: dict[str, object],
) -> list[TranscriptEvent]:
    role = payload.get("role")
    if role in {"user", "assistant"}:
        text = _message_text(record, session_id, payload)
        if not text:
            return [
                _omission(
                    record,
                    session_id,
                    "operational",
                    "Empty dialogue record has no visible text",
                )
            ]
        if role == "user" and _is_session_opener(text):
            return [
                _omission(
                    record,
                    session_id,
                    "session-opener",
                    "Session-opening harness context was not displayed",
                )
            ]
        harness_envelope = _leading_harness_envelope(text) if role == "user" else None
        if harness_envelope in _HIDDEN_HARNESS_ENVELOPES:
            category, reason = _HIDDEN_HARNESS_ENVELOPES[harness_envelope]
            return [_omission(record, session_id, category, reason)]
        dialogue_role = "user" if role == "user" else "assistant"
        return [
            Turn(
                source="codex",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                role=dialogue_role,
                text_blocks=text,
                harness_identification=(
                    "leading-envelope" if harness_envelope in _VISIBLE_HARNESS_ENVELOPES else None
                ),
            )
        ]
    if role == "developer":
        return [
            _omission(
                record,
                session_id,
                "developer-message",
                "Developer injections are not archived",
            )
        ]
    _unknown_response(record, session_id, f"message-role-{role}")


def _adapt_response_item(
    record: RawRecord,
    session_id: str,
) -> list[TranscriptEvent]:
    payload = _payload(record, session_id)
    payload_type = payload.get("type")
    if payload_type == "message":
        return _adapt_message(record, session_id, payload)
    if payload_type == "agent_message":
        return [
            _omission(
                record,
                session_id,
                "sub-agent-message",
                "Inter-agent messages are not archived",
            )
        ]
    if payload_type == "reasoning":
        return [
            _omission(
                record,
                session_id,
                "reasoning",
                "Private model reasoning is not archived",
            )
        ]
    if payload_type in _TOOL_CALLS:
        return [
            Turn(
                source="codex",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                role="assistant",
                text_blocks=(),
                tool_calls=(_tool_summary(payload),),
            )
        ]
    if payload_type in _TOOL_OUTPUTS:
        return [
            _omission(
                record,
                session_id,
                "tool-output",
                "Tool outputs are not archived",
            )
        ]
    _unknown_response(record, session_id, payload_type)


def _adapt_event_message(
    record: RawRecord,
    session_id: str,
) -> list[TranscriptEvent]:
    payload_type = _payload(record, session_id).get("type")
    if payload_type in _DUPLICATE_EVENTS:
        return [
            _omission(
                record,
                session_id,
                "duplicate-event",
                "Duplicate event-message projection is not archived",
            )
        ]
    if payload_type == "thread_rolled_back":
        return [
            Boundary(
                source="codex",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                kind="rollback",
                label="Codex thread rolled back",
            )
        ]
    if payload_type == "turn_aborted":
        return [
            _omission(
                record,
                session_id,
                "aborted-turn",
                "Aborted turn events are not dialogue",
            )
        ]
    if payload_type in _OPERATIONAL_EVENT_TYPES:
        return [
            _omission(
                record,
                session_id,
                "operational",
                "Operational source record is not dialogue",
            )
        ]
    payload_label = payload_type if isinstance(payload_type, str) else repr(payload_type)
    return [
        _omission(
            record,
            session_id,
            "unrecognised",
            f"Unrecognised Codex source record type: event_msg/{payload_label}",
        )
    ]


def _adapt_record(
    record: RawRecord,
    session_id: str,
    *,
    first: bool,
) -> list[TranscriptEvent]:
    record_type = record.value.get("type")
    if record_type == "response_item":
        return _adapt_response_item(record, session_id)
    if record_type == "compacted":
        return [
            Boundary(
                source="codex",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                kind="compaction",
                label="Codex context compacted",
            )
        ]
    if record_type == "event_msg":
        return _adapt_event_message(record, session_id)
    if record_type == "session_meta" and first:
        return []
    if record_type in _OPERATIONAL_RECORD_TYPES:
        return [
            _omission(
                record,
                session_id,
                "operational",
                "Operational source record is not dialogue",
            )
        ]
    type_label = record_type if isinstance(record_type, str) else repr(record_type)
    return [
        _omission(
            record,
            session_id,
            "unrecognised",
            f"Unrecognised Codex source record type: {type_label}",
        )
    ]


def _first_metadata(
    records: tuple[RawRecord, ...],
    session_id: str,
) -> dict[str, object]:
    if not records or records[0].value.get("type") != "session_meta":
        raise AdapterError(f"Codex session {session_id} line 1 is not session_meta")
    payload = _payload(records[0], session_id)
    actual = _optional_string(payload.get("id")) or _optional_string(payload.get("session_id"))
    if actual != session_id:
        raise AdapterError(f"Codex session {session_id} line 1 names session {actual}")
    return payload


def _git_evidence(metadata: dict[str, object]) -> CodexGitEvidence | None:
    git = metadata.get("git")
    if not isinstance(git, dict):
        return None
    git_value = cast("dict[str, object]", git)
    return CodexGitEvidence(
        branch=_optional_string(git_value.get("branch")),
        commit=_optional_string(git_value.get("commit_hash")),
        remote=_optional_string(git_value.get("repository_url")),
    )


def _working_directories(
    records: tuple[RawRecord, ...],
    session_id: str,
) -> tuple[str, ...]:
    directories: dict[str, None] = {}
    for record in records:
        if record.value.get("type") not in {"session_meta", "turn_context"}:
            continue
        cwd = _optional_string(_payload(record, session_id).get("cwd"))
        if cwd is not None:
            directories[cwd] = None
    return tuple(directories)


def adapt_codex_records(
    records: Iterable[RawRecord],
    *,
    session_id: str,
    source_path: Path,
) -> Session:
    """Map supported Codex rollout records to the neutral archive model."""
    materialized = tuple(records)
    metadata = _first_metadata(materialized, session_id)
    events: list[TranscriptEvent] = []
    for index, record in enumerate(materialized):
        events.extend(_adapt_record(record, session_id, first=index == 0))
    timestamps = tuple(
        value for record in materialized if (value := _timestamp(record)) is not None
    )
    return Session(
        source="codex",
        session_id=session_id,
        source_path=source_path,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        working_directories=_working_directories(materialized, session_id),
        events=tuple(events),
        codex_git=_git_evidence(metadata),
    )


def subagent_rollout_omission(
    records: Iterable[RawRecord],
    *,
    parent_session_id: str,
    source_path: Path,
) -> Omission | None:
    """Validate and count one Codex sub-agent rollout without adapting content."""
    materialized = tuple(records)
    if not materialized:
        return None
    first = materialized[0]
    if first.value.get("type") != "session_meta":
        raise AdapterError(
            f"Codex sub-agent rollout {source_path} does not start with session_meta"
        )
    metadata = _payload(first, parent_session_id)
    source = metadata.get("source")
    if not isinstance(source, dict) or "subagent" not in source:
        raise AdapterError(f"Codex sub-agent rollout {source_path} has no subagent source")
    actual_parent = _optional_string(metadata.get("session_id"))
    if actual_parent != parent_session_id:
        raise AdapterError(f"Codex sub-agent rollout {source_path} names parent {actual_parent}")
    return Omission(
        source="codex",
        session_id=parent_session_id,
        location=_location(first),
        timestamp=_timestamp(first),
        category="sub-agent-traffic",
        reason="Codex sub-agent rollout records are not archived",
        count=len(materialized),
    )
