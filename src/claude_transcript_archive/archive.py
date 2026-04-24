"""Archive orchestration: hash-based skip detection, directory naming, session archiving."""

import contextlib
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

_NORMALISE_TEXT_SUFFIXES = frozenset({".md", ".html", ".json", ".jsonl", ".txt"})
_NORMALISE_TEXT_NAMES = frozenset({".title", ".last_size"})


def normalise_text_outputs(output_dir: Path) -> int:
    """Strip trailing whitespace and collapse trailing newlines to one.

    Walks output_dir recursively for files matching the text-suffix or
    text-name allowlist; binaries (.pdf, etc.) and unrelated files are left
    alone. Matches the rules pre-commit-hooks' ``trailing-whitespace`` and
    ``end-of-file-fixer`` enforce, so the generated archive does not bounce
    on every commit when stored in-tree. Returns the count of files rewritten.
    """
    rewritten = 0
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in _NORMALISE_TEXT_SUFFIXES and path.name not in _NORMALISE_TEXT_NAMES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = [line.rstrip() for line in original.splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        normalised = ("\n".join(lines) + "\n") if lines else ""
        if normalised != original:
            path.write_text(normalised, encoding="utf-8")
            rewritten += 1
    return rewritten


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


def update_metadata(
    session_dir: Path,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    purpose: str | None = None,
    prompt: str | None = None,
    process: str | None = None,
    provenance: str | None = None,
) -> bool:
    """Update metadata fields on an existing archived session.

    Modifies session.meta.json in place. Returns True if updated, False if skipped.
    Sets needs_review=False when all Three Ps are populated.
    """
    sidecar_path = session_dir / "session.meta.json"
    if not sidecar_path.exists():
        return False

    try:
        meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return False

    if title:
        meta.setdefault("auto_generated", {})["title"] = title
    if tags is not None:
        meta.setdefault("auto_generated", {})["tags"] = tags
    if purpose:
        meta.setdefault("auto_generated", {})["purpose"] = purpose
    if prompt:
        meta.setdefault("three_ps", {})["prompt_summary"] = prompt
    if process:
        meta.setdefault("three_ps", {})["process_summary"] = process
    if provenance:
        meta.setdefault("three_ps", {})["provenance_summary"] = provenance

    # If all Three Ps provided, mark as reviewed
    three_ps = meta.get("three_ps", {})
    if (
        three_ps.get("prompt_summary")
        and three_ps.get("process_summary")
        and three_ps.get("provenance_summary")
    ):
        meta.setdefault("archive", {})["needs_review"] = False

    sidecar_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return True


def regenerate_outputs(session_dir: Path, *, quiet: bool = False) -> bool:
    """Re-render output files from raw-transcript.jsonl in an archive directory.

    Returns True if regenerated, False if skipped (missing raw transcript).
    """

    raw_path = session_dir / "raw-transcript.jsonl"
    if not raw_path.exists():
        log_info(f"Warning: no raw-transcript.jsonl in {session_dir.name}, skipping", quiet=False)
        return False

    content = raw_path.read_text(encoding="utf-8")

    # Read title from sidecar or .title file
    title = "Untitled"
    metadata = None
    sidecar_path = session_dir / "session.meta.json"
    if sidecar_path.exists():
        try:
            meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
            title = meta.get("auto_generated", {}).get("title", title)
            metadata = meta
        except json.JSONDecodeError:
            pass
    title_file = session_dir / ".title"
    if title_file.exists():
        title = title_file.read_text(encoding="utf-8").strip() or title

    # Re-render HTML via claude-code-transcripts
    with contextlib.suppress(FileNotFoundError):
        subprocess.run(
            ["claude-code-transcripts", "json", str(raw_path), "-o", str(session_dir), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    _output.update_html_titles(session_dir, title)

    # Re-render markdown and PDF
    messages = _output.extract_conversation_messages(content)
    if messages:
        md_content = _output.generate_conversation_markdown(messages, title, metadata=metadata)
        (session_dir / "conversation.md").write_text(md_content, encoding="utf-8")

        pdf_path = session_dir / "conversation.pdf"
        _output.generate_conversation_pdf(messages, title, pdf_path, quiet=quiet, metadata=metadata)

    normalise_text_outputs(session_dir)

    return True


def find_duplicates(archive_dir: Path) -> list[tuple[str, list[Path]]]:
    """Find sessions with multiple archive directories.

    Scans */session.meta.json under archive_dir, groups by session_id.
    Returns list of (session_id, [dir1, dir2, ...]) for sessions with >1 directory.
    """
    session_dirs: dict[str, list[Path]] = {}
    for sidecar_path in sorted(archive_dir.glob("*/session.meta.json")):
        try:
            meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        sid = meta.get("session", {}).get("id")
        if sid:
            session_dirs.setdefault(sid, []).append(sidecar_path.parent)
    return [(sid, dirs) for sid, dirs in session_dirs.items() if len(dirs) > 1]


def migrate_legacy(legacy_dir: Path, target_dir: Path, *, dry_run: bool = True) -> list[str]:
    """Migrate archive directories from old ai_transcripts/ to target.

    Returns list of migrated session directory names.
    In dry_run mode, returns what would be migrated without moving files.
    """
    if not legacy_dir.exists():
        return []

    migrated = []
    for item in sorted(legacy_dir.iterdir()):
        if item.is_dir() and (item / "session.meta.json").exists():
            dest = target_dir / item.name
            if dry_run:
                migrated.append(item.name)
            elif not dest.exists():
                shutil.move(str(item), str(dest))
                migrated.append(item.name)
    return migrated


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
    trivial: bool = False,
    tags: list[str] | None = None,
    purpose: str | None = None,
) -> Path | None:
    """Archive a transcript with rich metadata.

    Returns the output directory path on success, None on failure or no-op.

    When target="branch", performs mount recovery if archive_dir is missing:
    checks for a 'transcripts' git branch and re-mounts the worktree.
    """
    # Mount recovery: if target is "branch", ensure worktree is mounted
    if target == "branch" and not archive_dir.exists():
        # Use archive_dir's parent as project root for git commands
        project_root = archive_dir.parent
        try:
            branch_check = subprocess.run(
                ["git", "branch", "--list", "transcripts"],
                cwd=project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            if branch_check.stdout.strip():
                # Branch exists, re-mount worktree
                subprocess.run(
                    ["git", "worktree", "add", str(archive_dir), "transcripts"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                )
                log_info(f"Re-mounted worktree at {archive_dir}", quiet)
            else:
                log_error(
                    "No transcripts branch found. Run 'claude-research-transcript init' first.",
                    quiet,
                )
                return None
        except (subprocess.CalledProcessError, FileNotFoundError):
            log_error(
                "Git error during mount recovery. Run 'claude-research-transcript init' first.",
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
            encoding="utf-8",
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
        trivial=trivial,
        project_dir=project_dir,
        tags=tags,
        purpose=purpose,
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

    # Normalise generated text artifacts so in-tree archives don't bounce
    # commits on trailing-whitespace / end-of-file-fixer pre-commit hooks.
    normalise_text_outputs(output_dir)

    return output_dir
