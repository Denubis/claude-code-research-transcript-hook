"""Neutral transcript archive model.

# pattern: Functional Core
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

type SourceTool = Literal["claude", "codex"]
type DialogueRole = Literal["user", "assistant"]
type HarnessIdentification = Literal["claude-prompt-source", "leading-envelope"]


class ArchiveError(RuntimeError):
    """Base error for transcript archive failures."""


class AdapterError(ArchiveError):
    """Raised when a source record cannot be mapped without guessing."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One decoded JSONL record with its one-based source order."""

    order: int
    value: dict[str, object]


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Stable local locator for a source record."""

    order: int
    stable_id: str | None = None

    @property
    def label(self) -> str:
        """Return a human-readable locator without source content."""
        if self.stable_id is None:
            return f"line {self.order}"
        return f"line {self.order}, id {self.stable_id}"


@dataclass(frozen=True, slots=True)
class ToolCallSummary:
    """One compact tool report visible to the human."""

    name: str
    summary: str


@dataclass(frozen=True, slots=True)
class Turn:
    """One visible human, main-agent, or identified harness turn."""

    source: SourceTool
    session_id: str
    location: SourceLocation
    timestamp: str | None
    role: DialogueRole
    text_blocks: tuple[str, ...]
    harness_identification: HarnessIdentification | None = None
    tool_calls: tuple[ToolCallSummary, ...] = ()
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class Boundary:
    """A visible discontinuity in the model's available context."""

    source: SourceTool
    session_id: str
    location: SourceLocation
    timestamp: str | None
    kind: Literal["compaction", "rollback"]
    label: str


@dataclass(frozen=True, slots=True)
class Omission:
    """A counted source category intentionally absent from dialogue."""

    source: SourceTool
    session_id: str
    location: SourceLocation
    timestamp: str | None
    category: str
    reason: str
    count: int = 1


type TranscriptEvent = Turn | Boundary | Omission


@dataclass(frozen=True, slots=True)
class CodexGitEvidence:
    """Git evidence captured at Codex session start."""

    branch: str | None
    commit: str | None
    remote: str | None


@dataclass(frozen=True, slots=True)
class AppliedRedaction:
    """Public ledger entry for a successful source redaction."""

    location: SourceLocation
    json_pointer: str
    reason: str


@dataclass(frozen=True, slots=True)
class Session:
    """One source session normalized for deterministic rendering."""

    source: SourceTool
    session_id: str
    source_path: Path
    started_at: str | None
    ended_at: str | None
    working_directories: tuple[str, ...]
    events: tuple[TranscriptEvent, ...]
    recovered_working_directories: tuple[str, ...] = ()
    claude_branches: tuple[str, ...] = ()
    codex_git: CodexGitEvidence | None = None
    redactions: tuple[AppliedRedaction, ...] = ()
    sidechain_source_paths: tuple[Path, ...] = ()
    prompt_summary: str = ""
    process_summary: str = ""
    provenance_summary: str = ""
    needs_review: bool = True
