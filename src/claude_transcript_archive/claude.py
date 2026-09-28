"""Claude Code JSONL adapter.

# pattern: Functional Core
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Never, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

from .model import (
    AdapterError,
    Boundary,
    HarnessIdentification,
    Omission,
    RawRecord,
    Session,
    SourceLocation,
    ToolCallSummary,
    TranscriptEvent,
    Turn,
)

_TEXT_BLOCK = "text"
_IMAGE_BLOCK = "image"
_THINKING_BLOCKS = frozenset({"thinking", "redacted_thinking"})
_TOOL_CALL_BLOCKS = frozenset({"tool_use", "server_tool_use"})
_TOOL_OUTPUT_BLOCKS = frozenset({"tool_result", "advisor_tool_result"})
_HARNESS_ENVELOPES = (
    "<task-notification>",
    "<command-name>",
    "<local-command-stdout>",
    "<bash-input>",
    "<bash-stdout>",
    "<command-message>",
)
_OPERATIONAL_RECORD_TYPES = frozenset(
    {
        "agent-setting",
        "ai-title",
        "attachment",
        "bridge-session",
        "file-history-delta",
        "file-history-snapshot",
        "last-prompt",
        "mode",
        "permission-mode",
        "pr-link",
        "queue-operation",
        "system",
    }
)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _location(record: RawRecord) -> SourceLocation:
    value = record.value
    return SourceLocation(
        order=record.order,
        stable_id=_optional_string(value.get("uuid")),
    )


def _timestamp(record: RawRecord) -> str | None:
    return _optional_string(record.value.get("timestamp"))


def _omission(
    record: RawRecord,
    session_id: str,
    category: str,
    reason: str,
) -> Omission:
    return Omission(
        source="claude",
        session_id=session_id,
        location=_location(record),
        timestamp=_timestamp(record),
        category=category,
        reason=reason,
    )


def _record_session_id(record: RawRecord) -> str | None:
    value = record.value
    return _optional_string(value.get("sessionId")) or _optional_string(value.get("session_id"))


def _validate_session(record: RawRecord, expected: str) -> None:
    actual = _record_session_id(record)
    if actual is not None and actual != expected:
        message = f"Claude session {expected} line {record.order} names session {actual}"
        raise AdapterError(message)


def _message_content(record: RawRecord, session_id: str) -> object:
    message = record.value.get("message")
    if not isinstance(message, dict):
        detail = f"Claude session {session_id} line {record.order} has no message"
        raise AdapterError(detail)
    return cast("dict[str, object]", message).get("content")


def _unknown_block(
    record: RawRecord,
    session_id: str,
    block_type: object,
) -> Never:
    detail = (
        f"Claude session {session_id} line {record.order} has unknown "
        f"conversation block {block_type}"
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


def _path_tool_summary(
    name: str,
    arguments: dict[str, object],
) -> str | None:
    actions = {"Read": "Read", "Write": "Wrote", "Edit": "Edited"}
    action = actions.get(name)
    path = _compact(arguments.get("file_path"))
    if action is None or path is None:
        return None
    return f"{action} {path}"


def _command_tool_summary(
    name: str,
    arguments: dict[str, object],
) -> str | None:
    path = _compact(arguments.get("file_path"))
    command = _compact(arguments.get("command"))
    if name == "Bash" and command is not None:
        return f"Ran {command}"
    if name == "WebFetch" and path is not None:
        return f"Fetched {path}"
    return None


def _search_tool_summary(
    name: str,
    arguments: dict[str, object],
) -> str | None:
    pattern = _compact(arguments.get("pattern"))
    query = _compact(arguments.get("query"))
    if name in {"Grep", "Glob"} and pattern is not None:
        return f"Searched for {pattern}"
    if name == "WebSearch" and query is not None:
        return f"Searched the web for {query}"
    return None


def _agent_tool_summary(
    name: str,
    arguments: dict[str, object],
) -> str | None:
    description = _compact(arguments.get("description"))
    if name in {"Agent", "Task"} and description is not None:
        return f"Explored {description}"
    if name in {"ToolSearch", "Skill"}:
        return "Explored"
    if name == "WebFetch":
        return "Fetched a web page"
    return None


def _tool_summary(block: dict[str, object]) -> ToolCallSummary:
    name = _optional_string(block.get("name")) or "unknown tool"
    tool_input = block.get("input")
    arguments = cast("dict[str, object]", tool_input) if isinstance(tool_input, dict) else {}
    formatters = (
        _path_tool_summary,
        _command_tool_summary,
        _search_tool_summary,
        _agent_tool_summary,
    )
    summary = next(
        (result for formatter in formatters if (result := formatter(name, arguments)) is not None),
        f"Used {name}",
    )
    return ToolCallSummary(name=name, summary=summary)


def _adapt_block(
    record: RawRecord,
    session_id: str,
    block: object,
    *,
    render_tools: bool,
) -> tuple[str | None, ToolCallSummary | None, Omission | None]:
    if not isinstance(block, dict):
        _unknown_block(record, session_id, type(block).__name__)
    block_value = cast("dict[str, object]", block)
    block_type = block_value.get("type")
    if block_type == _TEXT_BLOCK:
        block_text = block_value.get("text")
        if not isinstance(block_text, str):
            _unknown_block(record, session_id, "text-without-string")
        return block_text, None, None
    if block_type == _IMAGE_BLOCK:
        return (
            None,
            None,
            _omission(
                record,
                session_id,
                "image",
                "Image content is not archived",
            ),
        )
    if block_type in _THINKING_BLOCKS:
        return (
            None,
            None,
            _omission(
                record,
                session_id,
                "thinking",
                "Private model reasoning is not archived",
            ),
        )
    if block_type in _TOOL_CALL_BLOCKS:
        if render_tools:
            return None, _tool_summary(block_value), None
        return (
            None,
            None,
            _omission(
                record,
                session_id,
                "tool-call",
                "Sidechain tool calls are not archived",
            ),
        )
    if block_type in _TOOL_OUTPUT_BLOCKS:
        return (
            None,
            None,
            _omission(
                record,
                session_id,
                "tool-output",
                "Tool outputs are not archived",
            ),
        )
    _unknown_block(record, session_id, block_type)


def _adapt_content_list(
    record: RawRecord,
    session_id: str,
    content: list[object],
    *,
    render_tools: bool,
) -> tuple[tuple[str, ...], tuple[ToolCallSummary, ...], list[Omission]]:
    text: list[str] = []
    tools: list[ToolCallSummary] = []
    omissions: list[Omission] = []
    for block in content:
        block_text, tool, omission = _adapt_block(
            record,
            session_id,
            block,
            render_tools=render_tools,
        )
        if block_text is not None:
            text.append(block_text)
        if tool is not None:
            tools.append(tool)
        if omission is not None:
            omissions.append(omission)
    return tuple(text), tuple(tools), omissions


def _harness_identification(
    record: RawRecord,
    text_blocks: tuple[str, ...],
) -> HarnessIdentification | None:
    """Identify user-role harness traffic using two ordered evidence tiers.

    ``promptSource: system`` is decisive. Exact leading tags are a fallback
    over undocumented vendor internals and cannot detect untagged injections.
    """
    if record.value.get("type") != "user":
        return None
    if record.value.get("promptSource") == "system":
        return "claude-prompt-source"
    if text_blocks and any(text_blocks[0].startswith(tag) for tag in _HARNESS_ENVELOPES):
        return "leading-envelope"
    return None


def _adapt_dialogue(
    record: RawRecord,
    session_id: str,
) -> list[TranscriptEvent]:
    value = record.value
    if value.get("isSidechain") is True:
        content = _message_content(record, session_id)
        omissions: list[TranscriptEvent] = [
            _omission(
                record,
                session_id,
                "sidechain",
                "Sidechain dialogue is not archived",
            )
        ]
        if isinstance(content, list):
            _, _, nested = _adapt_content_list(
                record,
                session_id,
                cast("list[object]", content),
                render_tools=False,
            )
            omissions.extend(nested)
        return omissions
    if value.get("isMeta") is True or value.get("isCompactSummary") is True:
        return [
            _omission(
                record,
                session_id,
                "operational",
                "Injected or generated context is not dialogue",
            )
        ]

    content = _message_content(record, session_id)
    omissions: list[Omission] = []
    if isinstance(content, str):
        text = (content,)
        tools = ()
    elif isinstance(content, list):
        text, tools, omissions = _adapt_content_list(
            record,
            session_id,
            cast("list[object]", content),
            render_tools=True,
        )
    else:
        _unknown_block(record, session_id, type(content).__name__)

    record_type = value.get("type")
    role = "user" if record_type == "user" else "assistant"
    events: list[TranscriptEvent] = list(omissions)
    if text or tools:
        events.insert(
            0,
            Turn(
                source="claude",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                role=role,
                text_blocks=text,
                harness_identification=_harness_identification(record, text),
                tool_calls=tools,
                branch=_optional_string(value.get("gitBranch")),
            ),
        )
    elif not omissions:
        events.append(
            _omission(
                record,
                session_id,
                "operational",
                "Empty dialogue record has no visible text",
            )
        )
    return events


def _adapt_record(
    record: RawRecord,
    session_id: str,
) -> list[TranscriptEvent]:
    record_type = record.value.get("type")
    if record_type in {"user", "assistant"}:
        return _adapt_dialogue(record, session_id)
    if record_type == "system" and record.value.get("subtype") == "compact_boundary":
        metadata = record.value.get("compactMetadata")
        trigger = (
            cast("dict[str, object]", metadata).get("trigger")
            if isinstance(metadata, dict)
            else None
        )
        label = "Claude context compacted"
        if isinstance(trigger, str) and trigger:
            label = f"{label} ({trigger})"
        return [
            Boundary(
                source="claude",
                session_id=session_id,
                location=_location(record),
                timestamp=_timestamp(record),
                kind="compaction",
                label=label,
            )
        ]
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
            f"Unrecognised Claude source record type: {type_label}",
        )
    ]


def _ordered_strings(
    records: tuple[RawRecord, ...],
    key: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            value
            for record in records
            if (value := _optional_string(record.value.get(key))) is not None
        )
    )


def _timestamps(records: tuple[RawRecord, ...]) -> tuple[str, ...]:
    return tuple(value for record in records if (value := _timestamp(record)) is not None)


def adapt_claude_records(
    records: Iterable[RawRecord],
    *,
    session_id: str,
    source_path: Path,
) -> Session:
    """Map supported Claude records to the neutral archive model."""
    materialized = tuple(records)
    events: list[TranscriptEvent] = []
    for record in materialized:
        _validate_session(record, session_id)
        events.extend(_adapt_record(record, session_id))
    timestamps = _timestamps(materialized)
    return Session(
        source="claude",
        session_id=session_id,
        source_path=source_path,
        started_at=timestamps[0] if timestamps else None,
        ended_at=timestamps[-1] if timestamps else None,
        working_directories=_ordered_strings(materialized, "cwd"),
        claude_branches=_ordered_strings(materialized, "gitBranch"),
        events=tuple(events),
    )


def sidechain_shard_omission(
    records: Iterable[RawRecord],
    *,
    session_id: str,
    source_path: Path,
) -> Omission | None:
    """Validate and count one Claude sub-agent shard without adapting content."""
    materialized = tuple(records)
    for record in materialized:
        canonical = _optional_string(record.value.get("sessionId"))
        if canonical != session_id:
            raise AdapterError(
                f"Claude sidechain shard {source_path} line {record.order} "
                f"has canonical sessionId {canonical}"
            )
        if record.value.get("isSidechain") is not True:
            raise AdapterError(
                f"Claude sidechain shard {source_path} line {record.order} "
                "is not marked isSidechain true"
            )
    if not materialized:
        return None
    first = materialized[0]
    return Omission(
        source="claude",
        session_id=session_id,
        location=_location(first),
        timestamp=_timestamp(first),
        category="sub-agent-traffic",
        reason="Claude sub-agent shard records are not archived",
        count=len(materialized),
    )
