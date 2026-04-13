"""JSONL parsing, token/cost extraction, artifact categorisation, and trivial classification."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# Schema version for metadata files
SCHEMA_VERSION = "1.0"

# File type mappings for artifact categorization
FILE_TYPE_MAPPINGS = {
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".sh": "code",
    ".bash": "code",
    ".r": "code",
    ".R": "code",
    ".sql": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".c": "code",
    ".cpp": "code",
    ".h": "code",
    ".hpp": "code",
    ".md": "document",
    ".txt": "document",
    ".rst": "document",
    ".tex": "document",
    ".json": "data",
    ".csv": "data",
    ".jsonl": "data",
    ".geojson": "data",
    ".xml": "data",
    ".yaml": "config",
    ".yml": "config",
    ".toml": "config",
    ".ini": "config",
    ".env": "config",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".svg": "image",
    ".pdf": "document",
    ".html": "document",
}

# Approximate pricing per 1M tokens (USD) - Claude Sonnet 4
INPUT_PRICE_PER_M = 3.0
OUTPUT_PRICE_PER_M = 15.0
CACHE_PRICE_PER_M = 0.30


def classify_session(content: str) -> str:
    """Classify a session as trivial or substantial.

    Counts assistant messages in JSONL content.
    Returns "trivial" if < 5 assistant messages, "substantial" otherwise.
    """
    assistant_count = 0
    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = entry.get("message", {}).get("role", "")
        entry_type = entry.get("type", "")
        if role == "assistant" or entry_type == "assistant":
            assistant_count += 1
    return "trivial" if assistant_count < 5 else "substantial"


def extract_session_stats(content: str) -> dict[str, Any]:
    """Extract rich metadata from transcript JSONL."""
    stats: dict[str, Any] = {
        "turns": 0,
        "human_messages": 0,
        "assistant_messages": 0,
        "thinking_blocks": 0,
        "tool_calls": {"total": 0, "by_type": {}},
        "tokens": {"input": 0, "output": 0, "cache_read": 0},
        "model": None,
        "claude_code_version": None,
        "timestamps": [],
    }

    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Extract timestamp
        ts = entry.get("timestamp")
        if ts:
            stats["timestamps"].append(ts)

        # Skip non-message entries
        entry_type = entry.get("type")
        if entry_type == "file-history-snapshot":
            continue

        message = entry.get("message", {})
        role = message.get("role", entry_type or "")

        if role == "user" or entry_type == "user":
            stats["human_messages"] += 1
            stats["turns"] += 1

        elif role == "assistant" or entry_type == "assistant":
            stats["assistant_messages"] += 1
            msg_content = message.get("content", [])

            if isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict):
                        block_type = block.get("type")

                        if block_type == "thinking":
                            stats["thinking_blocks"] += 1

                        elif block_type == "tool_use":
                            tool_name = block.get("name", "unknown")
                            stats["tool_calls"]["total"] += 1
                            stats["tool_calls"]["by_type"][tool_name] = (
                                stats["tool_calls"]["by_type"].get(tool_name, 0) + 1
                            )

            # Extract model from response
            if not stats["model"]:
                model = message.get("model")
                if model:
                    stats["model"] = model

        # Extract Claude Code version
        if not stats["claude_code_version"]:
            version = entry.get("version")
            if version:
                stats["claude_code_version"] = version

        # Extract token usage
        usage = entry.get("usage", {})
        if usage:
            stats["tokens"]["input"] += usage.get("input_tokens", 0)
            stats["tokens"]["output"] += usage.get("output_tokens", 0)
            stats["tokens"]["cache_read"] += usage.get("cache_read_input_tokens", 0)

    # Compute derived values
    if stats["timestamps"]:
        stats["started_at"] = min(stats["timestamps"])
        stats["ended_at"] = max(stats["timestamps"])
        try:
            start = datetime.fromisoformat(stats["started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(stats["ended_at"].replace("Z", "+00:00"))
            stats["duration_minutes"] = int((end - start).total_seconds() / 60)
        except (ValueError, TypeError):
            stats["duration_minutes"] = 0
    else:
        stats["started_at"] = None
        stats["ended_at"] = None
        stats["duration_minutes"] = 0

    del stats["timestamps"]
    return stats


def estimate_cost(stats: dict[str, Any]) -> float:
    """Estimate API cost based on token usage."""
    input_tokens = stats["tokens"].get("input", 0)
    output_tokens = stats["tokens"].get("output", 0)
    cache_tokens = stats["tokens"].get("cache_read", 0)

    cost = (
        (input_tokens / 1_000_000) * INPUT_PRICE_PER_M
        + (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_M
        + (cache_tokens / 1_000_000) * CACHE_PRICE_PER_M
    )
    return round(cost, 4)


def get_file_type(file_path: str) -> str:
    """Determine file type from extension."""
    ext = Path(file_path).suffix.lower()
    return FILE_TYPE_MAPPINGS.get(ext, "other")


def extract_artifacts(content: str, project_dir: Path | None = None) -> dict[str, list]:
    """Extract created/modified/referenced files from tool calls."""
    written_files: set[str] = set()
    edited_files: set[str] = set()
    read_files: set[str] = set()

    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        message = entry.get("message", {})
        msg_content = message.get("content", [])

        if isinstance(msg_content, list):
            for block in msg_content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue

                tool_name = block.get("name", "")
                tool_input = block.get("input", {})
                file_path = tool_input.get("file_path")

                if not file_path:
                    continue

                if tool_name == "Write":
                    written_files.add(file_path)
                elif tool_name == "Edit":
                    edited_files.add(file_path)
                elif tool_name == "Read":
                    read_files.add(file_path)

    # Deduplication logic:
    # - created + modified -> created only (was new)
    # - read + modified -> modified only (read was context)
    # - read only -> referenced
    created = written_files
    modified = edited_files - written_files
    referenced = read_files - edited_files - written_files

    def make_relative(path: str) -> str:
        """Convert to project-relative if possible.

        Always returns forward-slash paths (POSIX style) for consistent
        storage regardless of platform.
        """
        if project_dir:
            try:
                resolved = Path(path).resolve()
                return resolved.relative_to(project_dir.resolve()).as_posix()
            except ValueError:
                pass
        return path.replace("\\", "/")

    result: dict[str, list] = {"created": [], "modified": [], "referenced": []}

    for path in sorted(created):
        result["created"].append({
            "path": make_relative(path),
            "type": get_file_type(path),
        })

    for path in sorted(modified):
        result["modified"].append({
            "path": make_relative(path),
            "type": get_file_type(path),
        })

    for path in sorted(referenced):
        result["referenced"].append({
            "path": make_relative(path),
            "type": get_file_type(path),
        })

    return result


def detect_relationship_hints(content: str) -> dict[str, Any]:
    """Detect mentions of other sessions, continuation patterns."""
    hints: dict[str, Any] = {
        "continues_hint": None,
        "references_hints": [],
        "detection_notes": [],
    }

    uuid_pattern = re.compile(
        r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    continuation_patterns = [
        r"continu(?:e|ing|ed)\s+from",
        r"pick(?:ing)?\s+up\s+(?:from\s+)?where",
        r"previous\s+session",
        r"last\s+session",
        r"earlier\s+(?:session|conversation)",
    ]

    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        message = entry.get("message", {})
        role = message.get("role", "")

        # Only check user messages
        if role != "user" and entry_type != "user":
            continue

        msg_content = message.get("content", "")
        if isinstance(msg_content, list):
            msg_content = " ".join(
                block.get("text", "")
                for block in msg_content
                if isinstance(block, dict) and block.get("type") == "text"
            )

        if not isinstance(msg_content, str):
            continue

        # Look for session UUIDs
        for match in uuid_pattern.finditer(msg_content):
            uuid_str = match.group(1)
            if uuid_str not in hints["references_hints"]:
                hints["references_hints"].append(uuid_str)
                hints["detection_notes"].append(f"Found session ID reference: {uuid_str}")

        # Look for continuation language
        for pattern in continuation_patterns:
            if re.search(pattern, msg_content, re.IGNORECASE):
                hints["detection_notes"].append(
                    f"Found continuation language matching: '{pattern}'"
                )

    return hints


def find_plan_files(_transcript_path: Path) -> list[Path]:
    """Find any plan files associated with this session.

    Plan files are typically in ~/.claude/plans/
    """
    plans_dir = Path.home() / ".claude" / "plans"
    if not plans_dir.exists():
        return []

    # Return all .md files in plans directory
    # In a more sophisticated implementation, we could try to match
    # plan files to sessions based on content or timestamps
    return list(plans_dir.glob("*.md"))


def is_ide_context_message(text: str) -> bool:
    """Check if a message is just IDE context, not a real user request."""
    if not text:
        return True
    text = text.strip()
    # IDE context tags that shouldn't be used as titles
    ide_patterns = [
        r"^<ide_opened_file>",
        r"^<ide_selection>",
        r"^<ide_visible_files>",
        r"^<system-reminder>",
        r"^<command-name>",
    ]
    for pattern in ide_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    # Too short to be a real request
    return len(text) < 10


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def create_session_metadata(
    session_id: str,
    transcript_path: Path,
    stats: dict[str, Any],
    title: str,
    artifacts: dict[str, list],
    relationship_hints: dict[str, Any],
    plan_files: list[str],
    directory_name: str,
    three_ps: dict[str, str] | None = None,
    needs_review: bool = True,
    trivial: bool = False,
    project_dir: Path | None = None,
    tags: list[str] | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Create the complete session.meta.json structure."""
    file_hash = compute_file_hash(transcript_path)

    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session": {
            "id": session_id,
            "started_at": stats.get("started_at"),
            "ended_at": stats.get("ended_at"),
            "duration_minutes": stats.get("duration_minutes", 0),
        },
        "project": {
            "name": project_dir.name if project_dir else None,
            "directory": str(project_dir) if project_dir else None,
        },
        "model": {
            "provider": "anthropic",
            "model_id": stats.get("model", "unknown"),
            "claude_code_version": stats.get("claude_code_version"),
            "access_method": "claude-code-cli",
        },
        "statistics": {
            "turns": stats["turns"],
            "human_messages": stats["human_messages"],
            "assistant_messages": stats["assistant_messages"],
            "thinking_blocks": stats["thinking_blocks"],
            "tool_calls": stats["tool_calls"],
            "tokens": stats["tokens"],
            "estimated_cost_usd": estimate_cost(stats),
        },
        "artifacts": artifacts,
        "relationships": {
            "continues": relationship_hints.get("continues_hint"),
            "references": relationship_hints.get("references_hints", []),
            "isPartOf": [project_dir.name] if project_dir else [],
        },
        "auto_generated": {
            "title": title,
            "purpose": purpose or "",
            "tags": tags or [],
        },
        "three_ps": three_ps or {
            "prompt_summary": "",
            "process_summary": "",
            "provenance_summary": "",
        },
        "plan_files": plan_files,
        "archive": {
            "archived_at": datetime.now().isoformat(),
            "directory_name": directory_name,
            "jsonl_path": "raw-transcript.jsonl",
            "jsonl_sha256": file_hash,
            "jsonl_bytes": transcript_path.stat().st_size,
            "needs_review": needs_review,
            "trivial": trivial,
        },
    }

    # Include relationship hints for user review
    if relationship_hints.get("detection_notes"):
        metadata["_relationship_hints"] = relationship_hints

    return metadata
