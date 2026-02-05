#!/usr/bin/env python3
"""Archive Claude Code transcripts with research-grade metadata.

Uses the IDW2025 reproducibility framework (three_ps: Prompt/Process/Provenance)
for structured research documentation.
"""

import argparse
import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
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

# LaTeX preamble for PDF generation with speaker turn styling
# Adapted from PromptGrimoire's conversation export
PDF_PREAMBLE = r"""
\usepackage{fontspec}
\setmainfont{DejaVu Serif}
\usepackage{xcolor}
\usepackage[a4paper,left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm]{geometry}
\usepackage[framemethod=tikz]{mdframed}
\usepackage{fancyvrb}
\usepackage{longtable}
\usepackage{booktabs}

% Speaker colours
\definecolor{usercolor}{HTML}{4A90D9}
\definecolor{assistantcolor}{HTML}{7B68EE}

% Paragraph formatting (no indent, paragraph spacing)
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5\baselineskip}

% Speaker turn environments with left border
\newmdenv[
  topline=false,
  bottomline=false,
  rightline=false,
  linewidth=3pt,
  linecolor=usercolor,
  innerleftmargin=1em,
  innerrightmargin=0pt,
  innertopmargin=0pt,
  innerbottommargin=0pt,
  skipabove=0pt,
  skipbelow=0pt
]{userturn}
\newmdenv[
  topline=false,
  bottomline=false,
  rightline=false,
  linewidth=3pt,
  linecolor=assistantcolor,
  innerleftmargin=1em,
  innerrightmargin=0pt,
  innertopmargin=0pt,
  innerbottommargin=0pt,
  skipabove=0pt,
  skipbelow=0pt
]{assistantturn}

% Pandoc compatibility
\providecommand{\tightlist}{%
  \setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\setlength{\emergencystretch}{3em}
"""

# Lua filter for pandoc to handle speaker turn markers
# Converts data-speaker attributes to mdframed environments
SPEAKER_LUA_FILTER = r"""
-- Pandoc Lua filter for speaker turn styling
-- Converts Div elements with data-speaker attribute to mdframed environments

local current_speaker = nil

function Div(elem)
  if FORMAT ~= 'latex' then return elem end

  local speaker = elem.attr.attributes['speaker']
  if speaker then
    local result = {}

    -- Close previous speaker turn if open
    if current_speaker then
      local prev_env = current_speaker == 'user' and 'userturn' or 'assistantturn'
      table.insert(result, pandoc.RawBlock('latex', '\\end{' .. prev_env .. '}'))
    end

    -- Emit speaker label
    local label = speaker == 'user' and 'User:' or 'Assistant:'
    local color = speaker == 'user' and 'usercolor' or 'assistantcolor'

    table.insert(result, pandoc.RawBlock('latex', string.format([[

\vspace{0.8\baselineskip}
\noindent{\footnotesize\textcolor{%s}{\textbf{%s}}}
\vspace{0.3\baselineskip}
]], color, label)))

    -- Open new speaker turn environment
    local new_env = speaker == 'user' and 'userturn' or 'assistantturn'
    table.insert(result, pandoc.RawBlock('latex', '\\begin{' .. new_env .. '}'))

    -- Add the actual content of the div
    for _, block in ipairs(elem.content) do
      table.insert(result, block)
    end

    current_speaker = speaker
    return result
  end

  return elem
end

function Pandoc(doc)
  if FORMAT ~= 'latex' then return doc end

  -- Close final speaker turn if open
  if current_speaker then
    local env = current_speaker == 'user' and 'userturn' or 'assistantturn'
    table.insert(doc.blocks, pandoc.RawBlock('latex', '\\end{' .. env .. '}'))
    current_speaker = nil
  end

  return doc
end
"""


def get_cc_project_path(project_dir: Path) -> str:
    """Get CC's path-encoded project ID.

    Claude Code encodes paths by replacing '/' with '-'.
    E.g., /home/user/project -> -home-user-project
    """
    return str(project_dir.resolve()).replace("/", "-")


def get_archive_dir(local: bool, output: str | None, project_dir: Path | None = None) -> Path:
    """Determine the archive directory based on options.

    For global archives, organizes by project using CC's path encoding.
    """
    if output:
        return Path(output).expanduser().resolve()
    if local:
        return Path.cwd() / "ai_transcripts"

    # Global archive - organize by project
    base_dir = Path.home() / ".claude" / "transcripts"
    if project_dir:
        cc_path = get_cc_project_path(project_dir)
        return base_dir / cc_path
    return base_dir


def get_project_dir_from_transcript(transcript_path: Path) -> Path | None:
    """Extract project directory from transcript path.

    Transcript paths are typically like:
    ~/.claude/projects/-home-user-myproject/session-id.jsonl

    Claude Code encodes paths by replacing '/' with '-'. We try to decode
    by checking if the resulting path exists on the filesystem.
    """
    # Check if this is in the standard CC projects location
    projects_dir = Path.home() / ".claude" / "projects"
    try:
        rel_path = transcript_path.resolve().relative_to(projects_dir)
        # First component is the encoded project path
        encoded_path = rel_path.parts[0]

        # The encoding replaces / with - so we need to find which dashes
        # are path separators. We do this by trying to find a valid path.
        if encoded_path.startswith("-"):
            # Simple approach: replace all - with / and check if it exists
            decoded = encoded_path.replace("-", "/")
            candidate = Path(decoded)
            if candidate.exists():
                return candidate

            # If that doesn't work, try to be smarter by checking
            # progressively which segments exist
            parts = encoded_path[1:].split("-")  # Remove leading dash
            current = Path("/")
            for i, part in enumerate(parts):
                test_path = current / part
                if test_path.exists():
                    current = test_path
                else:
                    # Maybe this dash was part of the directory name
                    # Try combining with next parts
                    combined = part
                    for j in range(i + 1, len(parts)):
                        combined = f"{combined}-{parts[j]}"
                        test_path = current / combined
                        if test_path.exists():
                            current = test_path
                            break
                    else:
                        # Give up and return what we have
                        break

            if current != Path("/") and current.exists():
                return current

    except ValueError:
        pass
    return None


def get_manifest_path(archive_dir: Path) -> Path:
    """Get the manifest file path for an archive directory."""
    return archive_dir / ".session_manifest.json"


def load_manifest(archive_dir: Path) -> dict:
    """Load session -> directory mapping."""
    manifest_path = get_manifest_path(archive_dir)
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    return {}


def save_manifest(archive_dir: Path, manifest: dict):
    """Save session -> directory mapping."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    get_manifest_path(archive_dir).write_text(json.dumps(manifest, indent=2))


def get_catalog_path(archive_dir: Path) -> Path:
    """Get the catalog file path."""
    return archive_dir / "CATALOG.json"


def load_catalog(archive_dir: Path) -> dict:
    """Load CATALOG.json or create empty structure."""
    catalog_path = get_catalog_path(archive_dir)
    if catalog_path.exists():
        try:
            return json.loads(catalog_path.read_text())
        except json.JSONDecodeError:
            pass
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": None,
        "archive_location": str(archive_dir),
        "total_sessions": 0,
        "needs_review_count": 0,
        "sessions": [],
    }


def save_catalog(archive_dir: Path, catalog: dict):
    """Save CATALOG.json."""
    catalog["generated_at"] = datetime.now().isoformat()
    catalog["total_sessions"] = len(catalog["sessions"])
    catalog["needs_review_count"] = sum(
        1 for s in catalog["sessions"] if s.get("needs_review", True)
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    get_catalog_path(archive_dir).write_text(json.dumps(catalog, indent=2))


def update_catalog(archive_dir: Path, session_metadata: dict):
    """Update CATALOG.json with new/updated session entry."""
    catalog = load_catalog(archive_dir)

    session_id = session_metadata["session"]["id"]
    new_entry = {
        "id": session_id,
        "directory": session_metadata["archive"]["directory_name"],
        "title": session_metadata["auto_generated"]["title"],
        "purpose": session_metadata["auto_generated"]["purpose"],
        "started_at": session_metadata["session"]["started_at"],
        "duration_minutes": session_metadata["session"]["duration_minutes"],
        "tags": session_metadata["auto_generated"].get("tags", []),
        "needs_review": session_metadata["archive"]["needs_review"],
    }

    # Update existing or append new
    existing_ids = {s["id"]: i for i, s in enumerate(catalog["sessions"])}
    if session_id in existing_ids:
        catalog["sessions"][existing_ids[session_id]] = new_entry
    else:
        catalog["sessions"].append(new_entry)

    # Sort by date (newest first), handle None
    catalog["sessions"].sort(
        key=lambda s: s.get("started_at") or "", reverse=True
    )

    save_catalog(archive_dir, catalog)


def extract_session_stats(content: str) -> dict[str, Any]:
    """Extract rich metadata from transcript JSONL."""
    stats = {
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
        """Convert to project-relative if possible."""
        if project_dir:
            try:
                resolved = Path(path).resolve()
                return str(resolved.relative_to(project_dir.resolve()))
            except ValueError:
                pass
        return path

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
    hints = {
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


def _is_ide_context_message(text: str) -> bool:
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


def generate_title_from_content(content: str) -> str:
    """Generate a meaningful title from transcript content.

    Extracts the first substantive user message and creates a title.
    Skips IDE context messages like <ide_opened_file> tags.
    """
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

        if role == "user" or entry_type == "user":
            msg_content = message.get("content", "")
            if isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict) and block.get("text"):
                        msg_content = block["text"]
                        break
                else:
                    continue

            if isinstance(msg_content, str) and msg_content.strip():
                # Skip IDE context messages
                if _is_ide_context_message(msg_content):
                    continue

                # Clean and truncate
                title = msg_content.strip()
                # Remove common prefixes
                greeting_pattern = r"^(hi|hello|hey|please|can you|could you)\s+"
                title = re.sub(greeting_pattern, "", title, flags=re.IGNORECASE)
                # Take first sentence or first 60 chars
                title = re.split(r"[.!?\n]", title)[0]
                return title[:60].strip() or "Untitled Session"

    return "Untitled Session"


def sanitize_filename(title: str) -> str:
    """Make title safe for filesystem."""
    safe = re.sub(r"[^\w\s-]", "", title)
    safe = re.sub(r"\s+", "-", safe)
    return safe[:50].lower().strip("-") or "untitled"


def update_html_titles(output_dir: Path, title: str):
    """Update HTML file titles to use the conversation title."""
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text()
        # Replace generic title
        content = re.sub(
            r"<title>Claude Code transcript[^<]*</title>",
            f"<title>{title}</title>",
            content,
        )
        # Add title as h1 at top of body if index.html
        if html_file.name == "index.html":
            content = re.sub(
                r"(<body[^>]*>)",
                f'\\1\n<h1 style="margin: 20px; font-family: system-ui;">{title}</h1>',
                content,
                count=1,
            )
        html_file.write_text(content)


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
    project_dir: Path | None = None,
) -> dict[str, Any]:
    """Create the complete session.meta.json structure."""
    file_hash = compute_file_hash(transcript_path)

    metadata = {
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
            "purpose": "",  # To be filled by interactive mode
            "tags": [],
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
        },
    }

    # Include relationship hints for user review
    if relationship_hints.get("detection_notes"):
        metadata["_relationship_hints"] = relationship_hints

    return metadata


def write_metadata_sidecar(
    archive_dir: Path,
    transcript_path: Path,
    metadata: dict[str, Any],
):
    """Write session.meta.json to archive AND next to original transcript."""
    # Write to archive directory
    archive_meta_path = archive_dir / "session.meta.json"
    archive_meta_path.write_text(json.dumps(metadata, indent=2))

    # Write sidecar next to original transcript
    sidecar_path = transcript_path.with_suffix(".jsonl.meta.json")
    with contextlib.suppress(PermissionError):
        sidecar_path.write_text(json.dumps(metadata, indent=2))


def log_error(message: str, quiet: bool = False):
    """Print error message to stderr unless quiet mode."""
    if not quiet:
        print(f"Error: {message}", file=sys.stderr)


def log_info(message: str, quiet: bool = False):
    """Print info message to stdout unless quiet mode."""
    if not quiet:
        print(message)


def format_tool_summary(tool_name: str, tool_input: dict) -> str:
    """Format a tool call as a one-line summary.

    Examples:
        Read: src/cli.py
        Write: output.txt
        Edit: src/main.py
        Bash: git status
        Grep: pattern in *.py
    """
    if tool_name == "Read":
        file_path = tool_input.get("file_path", "unknown")
        # Make path relative-looking by taking just filename or last 2 components
        path = Path(file_path)
        if len(path.parts) > 2:
            display = "/".join(path.parts[-2:])
        else:
            display = path.name
        return f"Read: {display}"

    if tool_name == "Write":
        file_path = tool_input.get("file_path", "unknown")
        path = Path(file_path)
        return f"Write: {path.name}"

    if tool_name == "Edit":
        file_path = tool_input.get("file_path", "unknown")
        path = Path(file_path)
        return f"Edit: {path.name}"

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        # Truncate long commands
        if len(command) > 60:
            command = command[:57] + "..."
        return f"Bash: `{command}`"

    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"Grep: '{pattern}' in {path or '.'}"

    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f"Glob: {pattern}"

    if tool_name == "Task":
        description = tool_input.get("description", "")
        return f"Task: {description}"

    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return f"WebFetch: {url[:50]}..." if len(url) > 50 else f"WebFetch: {url}"

    if tool_name == "WebSearch":
        query = tool_input.get("query", "")
        return f"WebSearch: '{query}'"

    # Generic fallback
    return f"{tool_name}"


def extract_conversation_messages(content: str) -> list[dict]:
    """Extract user and assistant messages from transcript JSONL.

    Returns a list of message dicts:
    {
        "role": "user" | "assistant",
        "text": str,
        "tool_calls": [{"name": str, "summary": str}, ...]
    }

    Filters out:
    - Thinking blocks
    - System messages (unless they contain user-visible content)
    - Tool results (incorporated into tool_call summaries)
    """
    messages = []

    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        if entry_type == "file-history-snapshot":
            continue

        message = entry.get("message", {})
        role = message.get("role", entry_type or "")
        msg_content = message.get("content", [])

        if role == "user" or entry_type == "user":
            # Extract text from user message, skip tool results
            text_parts = []
            if isinstance(msg_content, str):
                text_parts.append(msg_content)
            elif isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        # Skip tool_result blocks - they're responses to assistant

            text = "\n".join(text_parts).strip()
            if text:
                # Skip system reminder messages and IDE context
                if text.startswith("<system-reminder>"):
                    continue
                if text.startswith("<ide_"):
                    continue
                # Skip skill injections (loaded skill content appears as user message)
                if text.startswith("# ") and len(text) > 500:
                    # Long markdown content starting with heading is likely a skill
                    continue
                if "<command-name>" in text or "<command-message>" in text:
                    # Command invocation metadata
                    continue
                if "Base directory for this skill:" in text:
                    # Skill loading metadata
                    continue
                if text.startswith("Launching skill:"):
                    # Skill launch confirmation
                    continue
                messages.append({
                    "role": "user",
                    "text": text,
                    "tool_calls": [],
                })

        elif role == "assistant" or entry_type == "assistant":
            text_parts = []
            tool_calls = []

            if isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type")

                    if block_type == "text":
                        text_parts.append(block.get("text", ""))

                    elif block_type == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        summary = format_tool_summary(tool_name, tool_input)
                        tool_calls.append({"name": tool_name, "summary": summary})

                    # Skip thinking blocks - user requested no thinking

            text = "\n".join(text_parts).strip()
            if text or tool_calls:
                messages.append({
                    "role": "assistant",
                    "text": text,
                    "tool_calls": tool_calls,
                })

    return messages


def generate_conversation_markdown(
    messages: list[dict],
    title: str,
    metadata: dict | None = None,
) -> str:
    """Generate readable markdown from conversation messages.

    Args:
        messages: List of message dicts from extract_conversation_messages
        title: Title for the document
        metadata: Optional session metadata dict for header info
    """
    lines = [f"# {title}", ""]

    # Add metadata header if available
    if metadata:
        session = metadata.get("session", {})
        stats = metadata.get("statistics", {})
        model_info = metadata.get("model", {})

        if session.get("started_at"):
            lines.append(f"**Date**: {session['started_at'][:10]}")
        if model_info.get("model_id"):
            lines.append(f"**Model**: {model_info['model_id']}")
        if model_info.get("claude_code_version"):
            lines.append(f"**Claude Code**: v{model_info['claude_code_version']}")
        if session.get("duration_minutes"):
            lines.append(f"**Duration**: {session['duration_minutes']} minutes")
        if stats.get("turns"):
            lines.append(f"**Turns**: {stats['turns']}")
        if stats.get("estimated_cost_usd") and stats["estimated_cost_usd"] > 0:
            lines.append(f"**Estimated cost**: ${stats['estimated_cost_usd']:.2f}")
        lines.append("")
        lines.append("---")
        lines.append("")

    for msg in messages:
        role = msg["role"]
        text = msg["text"]
        tool_calls = msg.get("tool_calls", [])

        # Speaker header
        if role == "user":
            lines.append("## User")
        else:
            lines.append("## Assistant")
        lines.append("")

        # Message text
        if text:
            lines.append(text)
            lines.append("")

        # Tool calls as bullet list
        if tool_calls:
            lines.append("**Tools used:**")
            for tc in tool_calls:
                lines.append(f"- {tc['summary']}")
            lines.append("")

    return "\n".join(lines)


def sanitize_for_pdf(text: str) -> str:
    """Remove characters that cause issues in LaTeX/PDF generation.

    Removes control characters and other problematic Unicode while
    preserving normal whitespace and printable characters.
    """
    if not text:
        return text

    # Remove control characters except tab, newline, carriage return
    # Control chars are U+0000-U+001F and U+007F-U+009F
    result = []
    for char in text:
        code = ord(char)
        # Keep tab (9), newline (10), carriage return (13), and normal printable
        if code == 9 or code == 10 or code == 13 or code >= 32:
            # Also skip DEL (127) and C1 control chars (128-159)
            if code != 127 and not (128 <= code <= 159):
                result.append(char)

    return "".join(result)


def generate_conversation_html_for_pdf(messages: list[dict], title: str) -> str:
    """Generate HTML with speaker markers for pandoc PDF conversion.

    Uses data-speaker attributes that the Lua filter converts to
    mdframed environments with colored borders.
    """
    import html as html_module

    safe_title = sanitize_for_pdf(title)
    lines = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>{html_module.escape(safe_title)}</title>",
        "</head>",
        "<body>",
        # Note: Title comes from pandoc metadata, not <h1>, to avoid duplication
    ]

    for msg in messages:
        role = msg["role"]
        text = sanitize_for_pdf(msg["text"])
        tool_calls = msg.get("tool_calls", [])

        # Open speaker div with data-speaker attribute
        lines.append(f'<div data-speaker="{role}">')

        # Message text as paragraphs
        if text:
            for para in text.split("\n\n"):
                para = para.strip()
                if para:
                    # Preserve single newlines within paragraphs as <br>
                    para_lines = para.split("\n")
                    para_html = "<br>\n".join(html_module.escape(line) for line in para_lines)
                    lines.append(f"<p>{para_html}</p>")

        # Tool calls as list
        if tool_calls:
            lines.append("<p><strong>Tools used:</strong></p>")
            lines.append("<ul>")
            for tc in tool_calls:
                summary = sanitize_for_pdf(tc["summary"])
                lines.append(f"<li>{html_module.escape(summary)}</li>")
            lines.append("</ul>")

        lines.append("</div>")

    lines.extend(["</body>", "</html>"])
    return "\n".join(lines)


def generate_conversation_pdf(
    messages: list[dict],
    title: str,
    output_path: Path,
    quiet: bool = False,
) -> bool:
    """Generate PDF from conversation using pandoc with speaker styling.

    Returns True on success, False on failure.
    """
    # Sanitize title for LaTeX
    safe_title = sanitize_for_pdf(title)

    # Generate HTML with speaker markers
    html_content = generate_conversation_html_for_pdf(messages, safe_title)

    # Write temporary files for pandoc
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Write HTML input
        html_path = tmpdir_path / "input.html"
        html_path.write_text(html_content)

        # Write Lua filter
        filter_path = tmpdir_path / "speaker.lua"
        filter_path.write_text(SPEAKER_LUA_FILTER)

        # Write LaTeX header
        header_path = tmpdir_path / "header.tex"
        header_path.write_text(PDF_PREAMBLE)

        # Run pandoc
        cmd = [
            "pandoc",
            str(html_path),
            "-f", "html+native_divs",
            "-t", "pdf",
            "--pdf-engine=lualatex",
            f"--include-in-header={header_path}",
            f"--lua-filter={filter_path}",
            "-V", "documentclass=article",
            "-V", "papersize=a4",
            f"--metadata=title:{safe_title}",
            "-o", str(output_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,  # 2 minutes should be plenty for PDF generation
            )
            if result.returncode != 0:
                if not quiet:
                    print(f"Warning: PDF generation failed: {result.stderr}", file=sys.stderr)
                return False
            return True
        except subprocess.TimeoutExpired:
            if not quiet:
                print("Warning: PDF generation timed out", file=sys.stderr)
            return False
        except FileNotFoundError:
            if not quiet:
                print("Warning: pandoc not found, skipping PDF generation", file=sys.stderr)
            return False


def auto_discover_transcript() -> tuple[Path, str] | None:
    """Auto-discover the most recent transcript for the current project.

    Returns (transcript_path, session_id) or None if not found.
    """
    cwd = Path.cwd()
    # Claude Code encodes paths by replacing / with -
    # /home/user/project -> -home-user-project (leading / becomes single -)
    encoded_path = str(cwd).replace("/", "-")
    projects_dir = Path.home() / ".claude" / "projects" / encoded_path

    if not projects_dir.exists():
        return None

    # Find most recent .jsonl file
    jsonl_files = list(projects_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None

    # Sort by modification time, most recent first
    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    transcript_path = jsonl_files[0]

    # Session ID is the filename without extension
    session_id = transcript_path.stem

    return transcript_path, session_id


def archive(
    session_id: str,
    transcript_path: Path,
    archive_dir: Path,
    force: bool = False,
    force_retitle: bool = False,
    provided_title: str | None = None,
    quiet: bool = False,
    three_ps: dict[str, str] | None = None,
) -> Path | None:
    """Archive a transcript with rich metadata.

    Returns the output directory path on success, None on failure or no-op.
    """
    if not transcript_path.exists():
        log_error(f"Transcript not found: {transcript_path}", quiet)
        return None

    content = transcript_path.read_text()
    if not content.strip():
        log_error(f"Transcript is empty: {transcript_path}", quiet)
        return None

    manifest = load_manifest(archive_dir)
    project_dir = get_project_dir_from_transcript(transcript_path)

    # Check if we already have a directory for this session
    existing_dir = manifest.get(session_id)

    if existing_dir and not force_retitle and not force:
        output_dir = Path(existing_dir)
        # Check if content changed
        marker_file = output_dir / ".last_size"
        current_size = transcript_path.stat().st_size
        if marker_file.exists():
            last_size = int(marker_file.read_text())
            if current_size == last_size:
                return  # No changes
    else:
        output_dir = None

    # Generate or use title
    if provided_title:
        title = provided_title
    elif output_dir and (output_dir / ".title").exists():
        title = (output_dir / ".title").read_text().strip()
    else:
        title = generate_title_from_content(content)

    # Create directory name if needed
    if not output_dir or force_retitle:
        safe_title = sanitize_filename(title)
        date_str = datetime.now().strftime("%Y-%m-%d")
        directory_name = f"{date_str}-{safe_title or session_id[:8]}"
        output_dir = archive_dir / directory_name

        # If retitling, rename old directory
        if existing_dir and force_retitle and Path(existing_dir).exists():
            Path(existing_dir).rename(output_dir)
    else:
        directory_name = output_dir.name

    # Update manifest
    manifest[session_id] = str(output_dir)
    save_manifest(archive_dir, manifest)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract rich metadata
    stats = extract_session_stats(content)
    artifacts = extract_artifacts(content, project_dir)
    relationship_hints = detect_relationship_hints(content)

    # Find and copy plan files
    plan_files = find_plan_files(transcript_path)
    plan_file_names = []
    if plan_files:
        plans_archive_dir = output_dir / "plans"
        plans_archive_dir.mkdir(exist_ok=True)
        for plan_file in plan_files:
            dest = plans_archive_dir / plan_file.name
            shutil.copy2(plan_file, dest)
            plan_file_names.append(plan_file.name)

    # Generate HTML using claude-code-transcripts
    try:
        result = subprocess.run(
            [
                "claude-code-transcripts",
                "json",
                str(transcript_path),
                "-o",
                str(output_dir),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            log_error(f"claude-code-transcripts failed: {result.stderr}", quiet)
    except FileNotFoundError:
        log_error(
            "claude-code-transcripts not found. Install with: pip install claude-code-transcripts",
            quiet,
        )

    # Update HTML titles
    update_html_titles(output_dir, title)

    # Create metadata
    # If three_ps is provided, user has confirmed metadata - no review needed
    needs_review = three_ps is None
    metadata = create_session_metadata(
        session_id=session_id,
        transcript_path=transcript_path,
        stats=stats,
        title=title,
        artifacts=artifacts,
        relationship_hints=relationship_hints,
        plan_files=plan_file_names,
        directory_name=directory_name,
        three_ps=three_ps,
        needs_review=needs_review,
        project_dir=project_dir,
    )

    # Write metadata sidecars
    write_metadata_sidecar(output_dir, transcript_path, metadata)

    # Generate markdown and PDF conversation exports (only when /transcript invoked)
    if three_ps is not None:
        conversation_messages = extract_conversation_messages(content)
        if conversation_messages:
            # Generate markdown with metadata header
            md_content = generate_conversation_markdown(
                conversation_messages,
                title,
                metadata=metadata,
            )
            md_path = output_dir / "conversation.md"
            md_path.write_text(md_content)
            log_info(f"Generated: {md_path}", quiet)

            # Generate PDF
            pdf_path = output_dir / "conversation.pdf"
            if generate_conversation_pdf(
                conversation_messages,
                title,
                pdf_path,
                quiet=quiet,
            ):
                log_info(f"Generated: {pdf_path}", quiet)

    # Update catalog
    update_catalog(archive_dir, metadata)

    # Store title and size marker
    (output_dir / ".title").write_text(title)
    (output_dir / ".last_size").write_text(str(transcript_path.stat().st_size))

    # Keep raw backup
    (output_dir / "raw-transcript.jsonl").write_text(content)

    return output_dir


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Archive Claude Code transcripts with research-grade metadata",
        epilog="Reads JSON payload from stdin with transcript_path and session_id.",
    )
    parser.add_argument(
        "--title",
        type=str,
        help="Title to use (typically from /transcript skill)",
    )
    parser.add_argument(
        "--retitle",
        action="store_true",
        help="Force regenerate title/rename directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if transcript unchanged",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Archive to ./ai_transcripts/ instead of ~/.claude/transcripts/",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Custom output directory (overrides --local)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress error messages",
    )
    parser.add_argument(
        "--transcript",
        type=str,
        help="Path to transcript file (alternative to stdin JSON)",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Session ID (alternative to stdin JSON)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        help="Three Ps: Prompt summary (what was the user trying to accomplish?)",
    )
    parser.add_argument(
        "--process",
        type=str,
        help="Three Ps: Process summary (how was Claude Code used?)",
    )
    parser.add_argument(
        "--provenance",
        type=str,
        help="Three Ps: Provenance summary (role in broader research context)",
    )
    args = parser.parse_args()

    # Determine input source: CLI arguments, stdin, or auto-discovery
    transcript_path = None
    session_id = None

    if args.transcript and args.session_id:
        # Manual invocation with explicit arguments
        transcript_path = Path(args.transcript)
        session_id = args.session_id
    elif args.transcript or args.session_id:
        # Partial CLI args provided - error
        log_error("Both --transcript and --session-id must be provided together", args.quiet)
        sys.exit(1)
    else:
        # Try stdin first (for hook mode)
        if not sys.stdin.isatty():
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                try:
                    payload = json.loads(stdin_content)
                    transcript_path = Path(payload.get("transcript_path", ""))
                    session_id = payload.get("session_id", "")
                except json.JSONDecodeError:
                    pass  # Not valid JSON, fall through to auto-discovery

        # If no valid stdin, try auto-discovery
        if not transcript_path or not session_id:
            discovered = auto_discover_transcript()
            if discovered:
                transcript_path, session_id = discovered
                log_info(f"Auto-discovered: {transcript_path}", args.quiet)
            else:
                log_error(
                    "No transcript found. Run from a project directory or use "
                    "--transcript and --session-id arguments.",
                    args.quiet,
                )
                sys.exit(1)

    if not transcript_path or not session_id:
        log_error("Missing transcript_path or session_id in input", args.quiet)
        sys.exit(1)

    # Determine project directory for organizing global archives
    project_dir = get_project_dir_from_transcript(transcript_path)
    archive_dir = get_archive_dir(
        local=args.local,
        output=args.output,
        project_dir=project_dir if not args.local else None,
    )

    # Build three_ps if any are provided
    three_ps = None
    if args.prompt or args.process or args.provenance:
        three_ps = {
            "prompt_summary": args.prompt or "",
            "process_summary": args.process or "",
            "provenance_summary": args.provenance or "",
        }

    output_dir = archive(
        session_id,
        transcript_path,
        archive_dir,
        force=args.force,
        force_retitle=args.retitle,
        provided_title=args.title,
        quiet=args.quiet,
        three_ps=three_ps,
    )

    if output_dir:
        log_info(f"Archived to: {output_dir}", args.quiet)
        log_info(f"View transcript: {output_dir / 'index.html'}", args.quiet)


if __name__ == "__main__":
    main()
