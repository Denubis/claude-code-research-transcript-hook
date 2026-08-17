"""Deterministic Markdown rendering.

# pattern: Functional Core
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from .model import (
    ArchiveError,
    Boundary,
    Omission,
    Session,
    SourceLocation,
    TranscriptEvent,
    Turn,
)


class RenderError(ArchiveError):
    """Raised when a session cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class RenderedPart:
    """One complete Markdown candidate plus its source-line map."""

    filename: str
    content: str
    line_sources: tuple[SourceLocation | None, ...]

    def source_for_line(self, line_number: int) -> SourceLocation | None:
        """Return the source record for a one-based rendered line."""
        if line_number < 1 or line_number > len(self.line_sources):
            return None
        return self.line_sources[line_number - 1]


@dataclass(slots=True)
class _Lines:
    values: list[str]
    sources: list[SourceLocation | None]

    @classmethod
    def empty(cls) -> _Lines:
        return cls(values=[], sources=[])

    def add(
        self,
        *values: str,
        source: SourceLocation | None = None,
    ) -> None:
        self.values.extend(value.rstrip() for value in values)
        self.sources.extend(source for _ in values)

    def blank(self) -> None:
        if self.values and self.values[-1]:
            self.add("")

    def finish(self) -> tuple[str, tuple[SourceLocation | None, ...]]:
        while self.values and not self.values[-1]:
            self.values.pop()
            self.sources.pop()
        return "\n".join(self.values) + "\n", tuple(self.sources)


def normalize_text(value: str) -> str:
    """Normalize line endings and strip trailing whitespace per source line."""
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _table_cell(value: str) -> str:
    return normalize_text(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _longest_backtick_run(value: str) -> int:
    longest = 0
    current = 0
    for character in value:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _add_text_block(
    lines: _Lines,
    value: str,
    source: SourceLocation,
) -> None:
    normalized = normalize_text(value).strip("\n")
    fence = "`" * max(3, _longest_backtick_run(normalized) + 1)
    lines.add(f"{fence}text", source=source)
    if normalized:
        lines.add(*normalized.split("\n"), source=source)
    lines.add(fence, source=source)


def _add_front_matter(
    lines: _Lines,
    session: Session,
) -> None:
    lines.add(
        "---",
        "archive_version: 2",
        f"source_tool: {_json(session.source)}",
        f"session_id: {_json(session.session_id)}",
        f"source_file: {_json(str(session.source_path))}",
        f"started_at: {_json(session.started_at)}",
        f"ended_at: {_json(session.ended_at)}",
        f"working_directories: {_json(list(session.working_directories))}",
        f"recovered: {str(bool(session.recovered_working_directories)).lower()}",
        f"recovered_working_directories: {_json(list(session.recovered_working_directories))}",
        f"prompt_summary: {_json(session.prompt_summary)}",
        f"process_summary: {_json(session.process_summary)}",
        f"provenance_summary: {_json(session.provenance_summary)}",
        f"needs_review: {str(session.needs_review).lower()}",
    )
    if session.source == "claude":
        lines.add(f"claude_branches: {_json(list(session.claude_branches))}")
        lines.add(
            "sidechain_source_files: "
            f"{_json([str(path) for path in session.sidechain_source_paths])}"
        )
    lines.add("---")
    lines.blank()


def _add_source_evidence(lines: _Lines, session: Session) -> None:
    lines.add("## Source evidence")
    if session.working_directories:
        for directory in session.working_directories:
            lines.add(f"- Working directory: {directory}")
    else:
        lines.add("- Working directory: unavailable")
    for directory in session.recovered_working_directories:
        lines.add(f"- Recovered working directory: {directory}")
    lines.blank()
    if session.source == "claude":
        lines.add("## Claude branch evidence")
        if session.claude_branches:
            for branch in session.claude_branches:
                lines.add(f"- Branch seen in source records: {branch}")
        else:
            lines.add("- No branch value was present in source records")
    else:
        lines.add("## Session-start Git evidence, not per-turn attribution")
        evidence = session.codex_git
        lines.add(f"- Branch: {evidence.branch if evidence else 'unavailable'}")
        lines.add(f"- Commit: {evidence.commit if evidence else 'unavailable'}")
        lines.add(f"- Remote: {evidence.remote if evidence else 'unavailable'}")
    lines.blank()


def _add_turn(lines: _Lines, turn: Turn) -> None:
    heading = (
        "Harness"
        if turn.harness_identification is not None
        else "User"
        if turn.role == "user"
        else "Assistant"
    )
    lines.add(
        f"## {heading}",
        source=turn.location,
    )
    lines.add(
        f"- Timestamp: {turn.timestamp or 'unavailable'}",
        f"- Source: {turn.location.label}",
        source=turn.location,
    )
    if turn.source == "claude":
        lines.add(
            f"- Branch evidence: {turn.branch or 'unavailable'}",
            source=turn.location,
        )
    if turn.tool_calls:
        lines.add("### Visible tool reports", source=turn.location)
        for tool in turn.tool_calls:
            lines.add(
                f"- {normalize_text(tool.summary).replace(chr(10), ' ')}",
                source=turn.location,
            )
    for block in turn.text_blocks:
        lines.blank()
        _add_text_block(lines, block, turn.location)
    lines.blank()


def _add_boundary(lines: _Lines, boundary: Boundary) -> None:
    lines.add("## Context boundary", source=boundary.location)
    lines.add(
        f"- Timestamp: {boundary.timestamp or 'unavailable'}",
        f"- Source: {boundary.location.label}",
        f"- Change: {boundary.label}",
        source=boundary.location,
    )
    lines.blank()


def _omissions(
    events: tuple[TranscriptEvent, ...],
) -> dict[tuple[str, str], tuple[int, SourceLocation]]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    locations: dict[tuple[str, str], SourceLocation] = {}
    for event in events:
        if not isinstance(event, Omission):
            continue
        key = (event.category, event.reason)
        totals[key] += event.count
        locations.setdefault(key, event.location)
    return {
        key: (count, locations[key])
        for key, count in sorted(totals.items(), key=lambda item: item[0])
    }


def _add_omissions(lines: _Lines, events: tuple[TranscriptEvent, ...]) -> None:
    lines.add("## Omissions")
    omissions = _omissions(events)
    if not omissions:
        lines.add("No source records were omitted from this part.")
        lines.blank()
        return
    lines.add("| Category | Count | Reason |", "|---|---:|---|")
    for (category, reason), (count, location) in omissions.items():
        lines.add(
            f"| {_table_cell(category)} | {count} | {_table_cell(reason)} |",
            source=location,
        )
    lines.add(
        "",
        "The omitted material remains available in the local source JSONL named "
        "in the front matter.",
    )
    lines.blank()


def _part_order_bounds(
    events: tuple[TranscriptEvent, ...],
) -> tuple[int, int] | None:
    if not events:
        return None
    orders = [event.location.order for event in events]
    return min(orders), max(orders)


def _add_redactions(
    lines: _Lines,
    session: Session,
    events: tuple[TranscriptEvent, ...],
) -> None:
    lines.add("## Redactions")
    bounds = _part_order_bounds(events)
    redactions = tuple(
        redaction
        for redaction in session.redactions
        if bounds is None or bounds[0] <= redaction.location.order <= bounds[1]
    )
    if not redactions:
        lines.add("No approved redactions apply to this part.")
        return
    lines.add("| Source | JSON pointer | Reason |", "|---|---|---|")
    for redaction in redactions:
        lines.add(
            f"| {_table_cell(redaction.location.label)} | "
            f"{_table_cell(redaction.json_pointer)} | "
            f"{_table_cell(redaction.reason)} |",
            source=redaction.location,
        )


def _render_part(
    session: Session,
    events: tuple[TranscriptEvent, ...],
) -> RenderedPart:
    lines = _Lines.empty()
    _add_front_matter(lines, session)
    lines.add("# Transcript")
    lines.blank()
    _add_source_evidence(lines, session)
    for event in events:
        if isinstance(event, Turn):
            _add_turn(lines, event)
        elif isinstance(event, Boundary):
            _add_boundary(lines, event)
    _add_omissions(lines, events)
    _add_redactions(lines, session, events)
    content, sources = lines.finish()
    return RenderedPart(
        filename="transcript.md",
        content=content,
        line_sources=sources,
    )


def render_session(session: Session) -> tuple[RenderedPart, ...]:
    """Render one parent session into one stable Markdown transcript."""
    return (_render_part(session, session.events),)
