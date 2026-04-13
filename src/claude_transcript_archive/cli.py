#!/usr/bin/env python3
"""Archive Claude Code transcripts with research-grade metadata.

Uses the IDW2025 reproducibility framework (three_ps: Prompt/Process/Provenance)
for structured research documentation.
"""

import argparse
import contextlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_transcript_archive import discovery as _discovery
from claude_transcript_archive import metadata as _metadata
from claude_transcript_archive import output as _output


def get_manifest_path(archive_dir: Path) -> Path:
    """Get the manifest file path for an archive directory."""
    return archive_dir / ".session_manifest.json"


def load_manifest(archive_dir: Path) -> dict:
    """Load session -> directory mapping."""
    manifest_path = get_manifest_path(archive_dir)
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {}


def save_manifest(archive_dir: Path, manifest: dict):
    """Save session -> directory mapping."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    get_manifest_path(archive_dir).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def get_catalog_path(archive_dir: Path) -> Path:
    """Get the catalog file path."""
    return archive_dir / "CATALOG.json"


def load_catalog(archive_dir: Path) -> dict:
    """Load CATALOG.json or create empty structure."""
    catalog_path = get_catalog_path(archive_dir)
    if catalog_path.exists():
        try:
            return json.loads(catalog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "schema_version": _metadata.SCHEMA_VERSION,
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
    get_catalog_path(archive_dir).write_text(json.dumps(catalog, indent=2), encoding="utf-8")


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
                if _metadata._is_ide_context_message(msg_content):
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


def write_metadata_sidecar(
    archive_dir: Path,
    transcript_path: Path,
    metadata: dict[str, Any],
):
    """Write session.meta.json to archive AND next to original transcript."""
    # Write to archive directory
    archive_meta_path = archive_dir / "session.meta.json"
    archive_meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    # Write sidecar next to original transcript
    sidecar_path = transcript_path.with_suffix(".jsonl.meta.json")
    with contextlib.suppress(PermissionError):
        sidecar_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def log_error(message: str, quiet: bool = False):
    """Print error message to stderr unless quiet mode."""
    if not quiet:
        print(f"Error: {message}", file=sys.stderr)


def log_info(message: str, quiet: bool = False):
    """Print info message to stdout unless quiet mode."""
    if not quiet:
        print(message)


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

    content = transcript_path.read_text(encoding="utf-8")
    if not content.strip():
        log_error(f"Transcript is empty: {transcript_path}", quiet)
        return None

    manifest = load_manifest(archive_dir)
    project_dir = _discovery.get_project_dir_from_transcript(transcript_path)

    # Check if we already have a directory for this session
    existing_dir = manifest.get(session_id)

    if existing_dir and not force_retitle and not force:
        output_dir = Path(existing_dir)
        # Check if content changed
        marker_file = output_dir / ".last_size"
        current_size = transcript_path.stat().st_size
        if marker_file.exists():
            last_size = int(marker_file.read_text(encoding="utf-8"))
            if current_size == last_size:
                return  # No changes
    else:
        output_dir = None

    # Generate or use title
    if provided_title:
        title = provided_title
    elif output_dir and (output_dir / ".title").exists():
        title = (output_dir / ".title").read_text(encoding="utf-8").strip()
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
    stats = _metadata.extract_session_stats(content)
    artifacts = _metadata.extract_artifacts(content, project_dir)
    relationship_hints = _metadata.detect_relationship_hints(content)

    # Find and copy plan files
    plan_files = _metadata.find_plan_files(transcript_path)
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
    _output.update_html_titles(output_dir, title)

    # Create metadata
    # If three_ps is provided, user has confirmed metadata - no review needed
    needs_review = three_ps is None
    metadata = _metadata.create_session_metadata(
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
        conversation_messages = _output.extract_conversation_messages(content)
        if conversation_messages:
            # Generate markdown with metadata header
            md_content = _output.generate_conversation_markdown(
                conversation_messages,
                title,
                metadata=metadata,
            )
            md_path = output_dir / "conversation.md"
            md_path.write_text(md_content, encoding="utf-8")
            log_info(f"Generated: {md_path}", quiet)

            # Generate PDF
            pdf_path = output_dir / "conversation.pdf"
            if _output.generate_conversation_pdf(
                conversation_messages,
                title,
                pdf_path,
                quiet=quiet,
                metadata=metadata,
            ):
                log_info(f"Generated: {pdf_path}", quiet)

    # Update catalog
    update_catalog(archive_dir, metadata)

    # Store title and size marker
    (output_dir / ".title").write_text(title, encoding="utf-8")
    (output_dir / ".last_size").write_text(str(transcript_path.stat().st_size), encoding="utf-8")

    # Keep raw backup
    (output_dir / "raw-transcript.jsonl").write_text(content, encoding="utf-8")

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
            discovered = _discovery.auto_discover_transcript()
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
    project_dir = _discovery.get_project_dir_from_transcript(transcript_path)
    archive_dir = _discovery.get_archive_dir(
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
