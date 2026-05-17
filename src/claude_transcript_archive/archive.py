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


def log_warning(message: str, quiet: bool = False):
    """Print warning message to stderr unless quiet mode.

    Used for non-fatal events that the user should still see — directory-name
    collisions resolved by auto-suffix, manifest-pointer protection refusals,
    etc. Distinguished from log_error because the operation still succeeded.
    """
    if not quiet:
        print(f"Warning: {message}", file=sys.stderr)


def log_info(message: str, quiet: bool = False):
    """Print info message to stdout unless quiet mode."""
    if not quiet:
        print(message)


def _dir_belongs_to_other_session(output_dir: Path, session_id: str) -> bool:
    """Return True iff output_dir is already claimed by a different session.

    Used by archive() to detect the silent-clobber case: distinct session UUIDs
    whose title-derived directory names sanitise to the same slug (typically
    because their first user messages are identical boilerplate envelopes).
    A directory with no session.meta.json is treated as a collision too — it
    might be a partially-written archive from a different session, and silently
    sharing it would risk the same data-loss the named bug produced.
    """
    if not output_dir.exists():
        return False
    meta_path = output_dir / "session.meta.json"
    if not meta_path.exists():
        # Existing dir, no meta — could be partial write from another session.
        # Conservatively treat as collision.
        return True
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError):
        return True
    other_sid = meta.get("session", {}).get("id")
    return bool(other_sid) and other_sid != session_id


def _resolve_collision(
    archive_dir: Path,
    base_directory_name: str,
    session_id: str,
    quiet: bool,
) -> tuple[str, Path]:
    """Pick a directory name that doesn't collide with a different session.

    If `base_directory_name` already belongs to a different session, append a
    short hash of `session_id` (first 8 hex chars of the UUID, which is unique
    enough at 4 billion entries) and emit a stderr warning naming both sides
    so audits can find the event. Returns (final_directory_name, final_path).
    """
    base_path = archive_dir / base_directory_name
    if not _dir_belongs_to_other_session(base_path, session_id):
        return base_directory_name, base_path

    suffix = session_id.split("-", 1)[0][:8] if "-" in session_id else session_id[:8]
    suffixed_name = f"{base_directory_name}-{suffix}"
    suffixed_path = archive_dir / suffixed_name

    # If the suffixed name ALSO collides (astronomically unlikely with 8 hex
    # chars, but possible if the same user re-suffixed twice), fall back to
    # the full UUID. We don't loop forever — UUID is globally unique.
    if _dir_belongs_to_other_session(suffixed_path, session_id):
        suffixed_name = f"{base_directory_name}-{session_id}"
        suffixed_path = archive_dir / suffixed_name

    log_warning(
        f"Directory-name collision: '{base_directory_name}' already belongs to "
        f"a different session; using '{suffixed_name}' for session {session_id}",
        quiet,
    )
    return suffixed_name, suffixed_path


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

    # Match the rules pre-commit-hooks enforce: trailing newline, no trailing
    # whitespace. Without this, `update` runs in an in-tree archive bounce on
    # every commit even though the rest of the pipeline already normalises.
    sidecar_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    normalise_text_outputs(session_dir)
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


def stitch_cluster(
    members: list[tuple[Path, str]],
    archive_dir: Path,
    *,
    quiet: bool = False,
    title: str | None = None,
    three_ps: dict[str, str] | None = None,
    tags: list[str] | None = None,
    purpose: str | None = None,
    trivial: bool = False,
) -> Path | None:
    """Build a fresh stitched archive directory from N (>=2) constituent JSONLs.

    Concatenates the constituent transcripts in chronological order, writes the
    MELICA-shaped stitched session.meta.json (see create_stitched_metadata),
    fans every constituent UUID into the archive's manifest pointing at the new
    cluster directory, and renders HTML/MD/PDF as for any other archive.

    Returns the cluster directory path on success, None on a per-member read
    failure. Raises ValueError if fewer than 2 members are supplied — single
    sessions belong to archive(), not stitch_cluster().

    Naming: cluster directory is ``YYYY-MM-DD-<sanitised-title>-stitched``
    where the date is the earliest constituent's start. Title comes from the
    explicit ``title`` argument if supplied, else from extract_custom_title()
    on the primary (the earliest constituent), else from generate_title_from_
    content() as a last resort.
    """
    if len(members) < 2:
        msg = "stitch_cluster requires at least 2 members; use archive() for singletons"
        raise ValueError(msg)

    parsed: list[dict] = []
    for transcript_path, session_id in members:
        if not transcript_path.exists():
            log_error(f"Transcript not found: {transcript_path}", quiet)
            return None
        content = transcript_path.read_text(encoding="utf-8")
        if not content.strip():
            log_error(f"Transcript is empty: {transcript_path}", quiet)
            return None
        stats = _metadata.extract_session_stats(content)
        parsed.append({
            "path": transcript_path,
            "session_id": session_id,
            "content": content,
            "stats": stats,
            "started_at": stats.get("started_at") or "",
            "ended_at": stats.get("ended_at") or "",
        })

    # Chronological sort — rank in _constituent_sessions follows this order.
    parsed.sort(key=lambda p: p["started_at"])
    primary = parsed[0]
    primary_session_id = primary["session_id"]
    primary_started = primary["started_at"]
    latest_ended = max((p["ended_at"] or p["started_at"]) for p in parsed)

    # Resolve cluster title. customTitle is the auto-stitch detection signal
    # (DR1), so the primary's customTitle is the natural cluster label.
    if title is None:
        custom = _metadata.extract_custom_title(primary["content"])
        title = custom or generate_title_from_content(primary["content"])

    date_str = primary_started[:10] if primary_started else datetime.now().strftime("%Y-%m-%d")
    safe_title = sanitize_filename(title)
    base_directory_name = (
        f"{date_str}-{safe_title or primary_session_id[:8]}-stitched"
    )

    directory_name, output_dir = _resolve_collision(
        archive_dir, base_directory_name, primary_session_id, quiet
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Concatenate JSONLs — each constituent's content trimmed to ensure a single
    # newline boundary between blocks (avoids accidental blank lines messing up
    # downstream line counts).
    concatenated_parts: list[str] = []
    for p in parsed:
        body = p["content"]
        if not body.endswith("\n"):
            body = body + "\n"
        concatenated_parts.append(body)
    concatenated = "".join(concatenated_parts)

    raw_path = output_dir / "raw-transcript.jsonl"
    raw_path.write_text(concatenated, encoding="utf-8")
    # Mirror to <primary-uuid>.jsonl for claude-code-transcripts' file-name
    # convention; both files hold the same concatenated stream (MELICA verbatim).
    primary_jsonl_path = output_dir / f"{primary_session_id}.jsonl"
    primary_jsonl_path.write_text(concatenated, encoding="utf-8")

    total_user = sum(p["stats"].get("human_messages", 0) for p in parsed)
    total_assistant = sum(p["stats"].get("assistant_messages", 0) for p in parsed)
    jsonl_lines = sum(1 for line in concatenated.split("\n") if line.strip())

    aggregated_stats = {
        "turns": total_user + total_assistant,
        "user_messages": total_user,
        "assistant_messages": total_assistant,
        "jsonl_lines": jsonl_lines,
    }

    try:
        start_dt = datetime.fromisoformat(primary_started.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(latest_ended.replace("Z", "+00:00"))
        duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
    except (ValueError, TypeError, AttributeError):
        duration_minutes = 0

    model_id: str | None = None
    cc_version: str | None = None
    for p in parsed:
        if not model_id:
            model_id = p["stats"].get("model")
        if not cc_version:
            cc_version = p["stats"].get("claude_code_version")
        if model_id and cc_version:
            break

    project_dir = _discovery.get_project_dir_from_transcript(primary["path"])
    constituents_for_meta = [(p["session_id"], p["started_at"]) for p in parsed]

    needs_review = three_ps is None
    metadata = _metadata.create_stitched_metadata(
        primary_session_id=primary_session_id,
        constituents=constituents_for_meta,
        raw_transcript_path=raw_path,
        aggregated_stats=aggregated_stats,
        directory_name=directory_name,
        started_at=primary_started,
        ended_at=latest_ended,
        duration_minutes=duration_minutes,
        model_id=model_id,
        claude_code_version=cc_version,
        title=title,
        three_ps=three_ps,
        needs_review=needs_review,
        trivial=trivial,
        project_dir=project_dir,
        tags=tags,
        purpose=purpose,
    )

    # Fan manifest — every constituent UUID points at the cluster (AC3.1/AC3.2).
    manifest = _catalog.load_manifest(archive_dir)
    for p in parsed:
        manifest[p["session_id"]] = str(output_dir)
    _catalog.save_manifest(archive_dir, manifest)

    archive_meta_path = output_dir / "session.meta.json"
    archive_meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    # Sidecar next to each constituent's original JSONL so per-source lookups
    # (e.g. /transcript invoked from a constituent UUID) resolve to cluster meta.
    for p in parsed:
        sidecar_path = p["path"].with_suffix(".jsonl.meta.json")
        with contextlib.suppress(PermissionError):
            sidecar_path.write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )

    # Render HTML via claude-code-transcripts on concatenated raw.
    try:
        result = subprocess.run(
            [
                "claude-code-transcripts",
                "json",
                str(raw_path),
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

    _output.update_html_titles(output_dir, title)

    # MD and PDF only when Three Ps supplied (matches singleton convention).
    if three_ps is not None:
        conv_msgs = _output.extract_conversation_messages(concatenated)
        if conv_msgs:
            md_content = _output.generate_conversation_markdown(
                conv_msgs, title, metadata=metadata
            )
            (output_dir / "conversation.md").write_text(md_content, encoding="utf-8")
            pdf_path = output_dir / "conversation.pdf"
            _output.generate_conversation_pdf(
                conv_msgs, title, pdf_path, quiet=quiet, metadata=metadata
            )

    _catalog.update_catalog(archive_dir, metadata)

    (output_dir / ".title").write_text(title, encoding="utf-8")
    normalise_text_outputs(output_dir)

    return output_dir


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

    # Manifest-pointer protection: if a curated archive already exists for this
    # session_id (non-empty Three Ps) and the incoming run does NOT supply its
    # own Three Ps, refuse to regenerate the directory name and preserve the
    # existing Three Ps in the rewritten metadata. Without this, a sweep-style
    # bulk re-archive (or any --force from a hook) silently overwrites curated
    # metadata with the auto-generated empty version — the failure mode the
    # MELICA audit caught in 3 confirmed cases (66368cd6, 25bb361f, 986491f2).
    _THREE_PS_KEYS = ("prompt_summary", "process_summary", "provenance_summary")
    incoming_three_ps_empty = not three_ps or not any(
        (three_ps or {}).get(k) for k in _THREE_PS_KEYS
    )
    if existing_dir and incoming_three_ps_empty:
        existing_meta_path = Path(existing_dir) / "session.meta.json"
        if existing_meta_path.exists():
            try:
                existing_meta = json.loads(existing_meta_path.read_text(encoding="utf-8"))
                existing_tp = existing_meta.get("three_ps", {}) or {}
                if any(existing_tp.get(k) for k in _THREE_PS_KEYS):
                    if force or force_retitle:
                        log_warning(
                            f"Manifest-pointer protection: session {session_id} "
                            f"already archived to '{Path(existing_dir).name}' with "
                            "curated Three Ps; refusing to regenerate without new "
                            "Three Ps (--force ignored). Pass --prompt/--process/"
                            "--provenance to overwrite intentionally.",
                            quiet,
                        )
                        force = False
                        force_retitle = False
                    # Preserve the curated Three Ps into the new metadata write.
                    three_ps = {k: existing_tp.get(k, "") for k in _THREE_PS_KEYS}
            except (json.JSONDecodeError, ValueError, OSError):
                pass

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
        base_directory_name = f"{date_str}-{safe_title or session_id[:8]}"

        # Silent-clobber guard: when distinct UUIDs sanitise to the same slug
        # (e.g. all-boilerplate first messages), pick a UUID-suffixed name so
        # each session keeps its own directory. Same-UUID re-archive falls
        # through unchanged because _dir_belongs_to_other_session returns False
        # when the existing meta matches our session_id.
        directory_name, output_dir = _resolve_collision(
            archive_dir, base_directory_name, session_id, quiet
        )

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
