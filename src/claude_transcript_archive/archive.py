"""Archive orchestration: hash-based skip detection, directory naming, session archiving."""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from claude_transcript_archive import catalog as _catalog
from claude_transcript_archive import discovery as _discovery
from claude_transcript_archive import metadata as _metadata
from claude_transcript_archive import output as _output


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
                if _metadata.is_ide_context_message(msg_content):
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
    target: str | None = None,
) -> Path | None:
    """Archive a transcript with rich metadata.

    Returns the output directory path on success, None on failure or no-op.

    When target="branch", performs mount recovery if archive_dir is missing:
    checks for a 'transcripts' git branch and re-mounts the worktree.
    """
    # Mount recovery: if target is "branch", ensure worktree is mounted
    if target == "branch" and not archive_dir.exists():
        try:
            branch_check = subprocess.run(
                ["git", "branch", "--list", "transcripts"],
                capture_output=True,
                text=True,
                check=True,
            )
            if branch_check.stdout.strip():
                # Branch exists, re-mount worktree
                subprocess.run(
                    ["git", "worktree", "add", str(archive_dir), "transcripts"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                log_info(f"Re-mounted worktree at {archive_dir}", quiet)
            else:
                log_error(
                    "No transcripts branch found. Run 'claude-transcript-archive init' first.",
                    quiet,
                )
                return None
        except (subprocess.CalledProcessError, FileNotFoundError):
            log_error(
                "Git error during mount recovery. Run 'claude-transcript-archive init' first.",
                quiet,
            )
            return None

    if not transcript_path.exists():
        log_error(f"Transcript not found: {transcript_path}", quiet)
        return None

    content = transcript_path.read_text(encoding="utf-8")
    if not content.strip():
        log_error(f"Transcript is empty: {transcript_path}", quiet)
        return None

    manifest = _catalog.load_manifest(archive_dir)
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
    _catalog.save_manifest(archive_dir, manifest)

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
    _catalog.write_metadata_sidecar(output_dir, transcript_path, metadata)

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
    _catalog.update_catalog(archive_dir, metadata)

    # Store title and size marker
    (output_dir / ".title").write_text(title, encoding="utf-8")
    (output_dir / ".last_size").write_text(str(transcript_path.stat().st_size), encoding="utf-8")

    # Keep raw backup
    (output_dir / "raw-transcript.jsonl").write_text(content, encoding="utf-8")

    return output_dir
