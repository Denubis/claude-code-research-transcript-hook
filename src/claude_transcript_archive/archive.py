"""Archive orchestration: hash-based skip detection, directory naming, session archiving."""

import contextlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple


class StitchResult(NamedTuple):
    """Result of a ``stitch_sessions`` call.

    Carries the target directory plus counts of how each source was handled so
    CLI/UI layers can report contextually — "Stitched N into …" when work
    happened, "No changes — all N already constituent" when every source was a
    no-op, and the failure count when some sources couldn't be attached.
    """
    directory: Path
    attached: int
    skipped: int
    failed: int

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
        if not (item.is_dir() and (item / "session.meta.json").exists()):
            continue
        # A sibling .dvc pointer means the directory is DVC-archived in
        # place, not legacy: moving it would orphan the pointer.
        if (item.parent / (item.name + ".dvc")).exists():
            continue
        dest = target_dir / item.name
        if dest.exists():
            continue
        if not dry_run:
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


def stitch_sessions(
    target_uuid: str,
    source_specs: list[tuple[str, Path]],
    archive_dir: Path,
    *,
    quiet: bool = False,
) -> StitchResult | None:
    """Force-stitch ``source_specs`` into the archive identified by
    ``target_uuid`` (Phase 5, AC5.1-AC5.4).

    Dispatches to ``extend_cluster`` when the target is already a cluster, or
    to ``promote_singleton_to_cluster`` when the target is a singleton. This
    is the manual-override path for cases the customTitle-driven auto-stitch
    cannot cover — pre-customTitle legacy sessions like MELICA's dad509ba
    being the canonical example (DR4).

    Each source is an explicit ``(uuid, jsonl_path)`` pair. When a source was
    itself an existing singleton in the archive, its old directory is removed
    after a successful attach and its catalog entry pruned — its content now
    lives in the target's concatenated stream.

    Idempotent per AC5.4: a source UUID already represented by the target
    (as the cluster's primary, as one of its ``_constituent_sessions``, or as
    the singleton's own UUID — the MELICA-observed self-stitch case) is
    counted as skipped with a stdout note; subsequent sources in the same
    call still process. Returns ``None`` only on the AC5.3 failure
    (``target_uuid`` not in manifest); a stdout error names the missing UUID.
    Returns a ``StitchResult`` on success — directory may differ from the
    initial lookup because the first source's promotion renames the
    singleton dir, plus counts of attached / skipped / failed so the caller
    can render contextual output.
    """
    manifest = _catalog.load_manifest(archive_dir)
    if target_uuid not in manifest:
        log_error(f"stitch: no archive found for {target_uuid}", quiet)
        return None
    target_dir = Path(manifest[target_uuid])
    attached = 0
    skipped = 0
    failed = 0

    for source_uuid, source_jsonl in source_specs:
        # Re-read each iteration — promotion on the first source moves the
        # target directory, so the manifest is the source of truth.
        manifest = _catalog.load_manifest(archive_dir)
        target_dir = Path(manifest.get(target_uuid, str(target_dir)))

        meta_path = target_dir / "session.meta.json"
        is_cluster = False
        constituents: list[dict] = []
        target_session_id: str | None = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                is_cluster = bool(meta.get("archive", {}).get("stitched"))
                constituents = meta.get("_constituent_sessions", []) or []
                target_session_id = meta.get("session", {}).get("id")
            except (json.JSONDecodeError, ValueError, OSError):
                pass

        # Idempotency / no-op detection:
        # (a) source already a constituent (the cluster case — AC5.4), or
        # (b) source IS the target itself (the MELICA-observed degenerate
        #     case: stitch --into X X. For a cluster this is the same as
        #     case (a) because the primary is always a constituent; for a
        #     singleton it would otherwise dispatch to promote which then
        #     refuses via its self-promotion guard — counting that as
        #     "failed" misleads the user about what happened).
        if (
            any(c.get("id") == source_uuid for c in constituents)
            or source_uuid == target_session_id
        ):
            log_info(
                f"stitch: {source_uuid} already represents target "
                f"{target_dir.name} — no-op.",
                quiet,
            )
            skipped += 1
            continue

        # Capture source's old archive dir BEFORE dispatch — extend/promote
        # both update the manifest to point source_uuid at the target dir.
        source_old_dir: Path | None = None
        if source_uuid in manifest:
            candidate = Path(manifest[source_uuid])
            if candidate != target_dir:
                source_old_dir = candidate

        if is_cluster:
            result = extend_cluster(
                target_dir, source_uuid, source_jsonl, quiet=quiet,
            )
        else:
            result = promote_singleton_to_cluster(
                target_dir, source_uuid, source_jsonl, quiet=quiet,
            )

        if result is None:
            log_warning(
                f"stitch: failed to attach {source_uuid} to {target_dir.name}",
                quiet,
            )
            failed += 1
            continue

        attached += 1
        # Promotion renames target_dir — track the new location for the next
        # iteration via the manifest re-read at the top of the loop.
        target_dir = result

        if source_old_dir is not None and source_old_dir.exists():
            try:
                shutil.rmtree(source_old_dir)
                log_info(
                    f"stitch: removed orphan singleton dir "
                    f"{source_old_dir.name} (content now in {target_dir.name})",
                    quiet,
                )
            except OSError as exc:
                log_warning(
                    f"stitch: could not remove orphan {source_old_dir}: {exc}",
                    quiet,
                )
            # Prune the orphan source entry from the catalog. update_catalog
            # writes entries keyed under ``id``; rebuild_indexes writes
            # ``session_id``. Match either to handle both lineages.
            try:
                catalog_data = _catalog.load_catalog(archive_dir)
                catalog_data["sessions"] = [
                    s for s in catalog_data["sessions"]
                    if s.get("id") != source_uuid
                    and s.get("session_id") != source_uuid
                ]
                _catalog.save_catalog(archive_dir, catalog_data)
            except (OSError, KeyError) as exc:
                log_warning(
                    f"stitch: catalog prune failed for {source_uuid}: {exc}",
                    quiet,
                )

    # Final lookup — promotion-aware.
    manifest = _catalog.load_manifest(archive_dir)
    final_dir = Path(manifest.get(target_uuid, str(target_dir)))
    return StitchResult(
        directory=final_dir,
        attached=attached,
        skipped=skipped,
        failed=failed,
    )


def _find_matching_cluster_or_singleton(
    archive_dir: Path,
    custom_title: str,
    *,
    quiet: bool = False,
) -> tuple[Path, bool] | None:
    """Scan the project's archives for one whose primary content carries the
    same ``customTitle`` (Phase 4 auto-stitch dispatch — DR1's match signal).

    Iterates the manifest's unique target directories (manifest fan-in means
    one cluster can have multiple manifest entries), reads each archive's
    ``raw-transcript.jsonl`` head for its ``customTitle``, and returns the
    most recently archived match as ``(archive_path, is_cluster)``. When
    multiple matches exist (legacy archives, partial promotions), emits a
    stderr warning naming the others so the user can reconcile via Phase 5's
    manual ``stitch`` CLI. Returns ``None`` when no archive matches.
    """
    manifest = _catalog.load_manifest(archive_dir)
    seen: set[str] = set()
    matches: list[tuple[Path, bool, str]] = []
    for dir_str in manifest.values():
        if dir_str in seen:
            continue
        seen.add(dir_str)
        archive_path = Path(dir_str)
        meta_path = archive_path / "session.meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        raw_path = archive_path / "raw-transcript.jsonl"
        if not raw_path.exists():
            continue
        try:
            archived_title = _metadata.extract_custom_title(
                raw_path.read_text(encoding="utf-8")
            )
        except OSError:
            continue
        if archived_title == custom_title:
            is_cluster = bool(meta.get("archive", {}).get("stitched"))
            archived_at = meta.get("archive", {}).get("archived_at") or ""
            matches.append((archive_path, is_cluster, archived_at))

    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(m[0].name for m in matches)
        log_warning(
            f"Multiple archives match customTitle '{custom_title}': {names}; "
            "using most recently archived. Reconcile via the manual `stitch` "
            "CLI (Phase 5) if this is wrong.",
            quiet,
        )
    matches.sort(key=lambda m: m[2], reverse=True)
    return matches[0][0], matches[0][1]


def promote_singleton_to_cluster(
    singleton_dir: Path,
    new_session_id: str,
    new_transcript_path: Path,
    *,
    quiet: bool = False,
) -> Path | None:
    """In-place upgrade of a singleton archive to a stitched cluster (Phase 4,
    AC4.1/AC4.3).

    Implements DR3's rename-before-rewrite sequence: pre-rename validation
    (missing inputs abort cleanly, leaving the singleton intact); compute the
    new cluster name ``YYYY-MM-DD-<customTitle>-stitched`` with collision
    resolution; ``os.rename`` the directory; then write the concatenated
    ``raw-transcript.jsonl`` and ``<primary-uuid>.jsonl`` mirror, rewrite the
    meta in stitched schema, fan both old- and new-UUIDs into the manifest,
    and re-render HTML.

    Curated Three Ps and tags on the singleton are preserved into the
    cluster's meta — promotion is a structural upgrade, not a metadata reset.

    Best-effort once the rename succeeds: post-rename failures log warnings
    via ``log_warning`` but the function still returns ``new_cluster_dir`` so
    the caller sees the new path. A pre-rename failure returns ``None`` and
    leaves the singleton untouched.
    """
    archive_dir = singleton_dir.parent

    if new_session_id == singleton_dir.name:
        log_warning(
            "promote: refusing self-promotion — new_session_id matches "
            "singleton dir name.",
            quiet,
        )
        return None

    if not singleton_dir.is_dir():
        log_warning(f"promote: singleton dir not found: {singleton_dir}", quiet)
        return None
    if not new_transcript_path.exists():
        log_warning(
            f"promote: new transcript not found: {new_transcript_path}", quiet,
        )
        return None

    singleton_meta_path = singleton_dir / "session.meta.json"
    if not singleton_meta_path.exists():
        log_warning(f"promote: singleton meta not found: {singleton_meta_path}", quiet)
        return None
    try:
        singleton_meta = json.loads(singleton_meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log_warning(f"promote: cannot read singleton meta: {exc}", quiet)
        return None

    singleton_session_id = singleton_meta.get("session", {}).get("id")
    if not singleton_session_id:
        log_warning("promote: singleton meta missing session.id", quiet)
        return None
    if singleton_session_id == new_session_id:
        log_warning(
            f"promote: refusing self-promotion — new_session_id "
            f"{new_session_id} matches singleton's UUID.",
            quiet,
        )
        return None

    singleton_raw_path = singleton_dir / "raw-transcript.jsonl"
    if not singleton_raw_path.exists():
        log_warning("promote: singleton raw-transcript.jsonl missing", quiet)
        return None
    try:
        singleton_content = singleton_raw_path.read_text(encoding="utf-8")
    except OSError as exc:
        log_warning(f"promote: cannot read singleton raw: {exc}", quiet)
        return None

    try:
        new_content = new_transcript_path.read_text(encoding="utf-8")
    except OSError as exc:
        log_warning(f"promote: cannot read new transcript: {exc}", quiet)
        return None
    if not new_content.strip():
        log_warning(f"promote: new transcript is empty: {new_transcript_path}", quiet)
        return None

    singleton_started = singleton_meta.get("session", {}).get("started_at") or ""
    singleton_ended = singleton_meta.get("session", {}).get("ended_at") or ""
    singleton_stats = _metadata.extract_session_stats(singleton_content)

    new_stats = _metadata.extract_session_stats(new_content)
    new_started = new_stats.get("started_at") or ""
    new_ended = new_stats.get("ended_at") or ""

    parsed = [
        {
            "session_id": singleton_session_id,
            "content": singleton_content,
            "started_at": singleton_started,
            "ended_at": singleton_ended,
            "stats": singleton_stats,
        },
        {
            "session_id": new_session_id,
            "content": new_content,
            "started_at": new_started,
            "ended_at": new_ended,
            "stats": new_stats,
        },
    ]
    parsed.sort(key=lambda p: p["started_at"])
    primary = parsed[0]
    primary_session_id = primary["session_id"]
    primary_started = primary["started_at"]
    latest_ended = max((p["ended_at"] or p["started_at"]) for p in parsed)

    title = (
        _metadata.extract_custom_title(primary["content"])
        or singleton_meta.get("auto_generated", {}).get("title")
        or "untitled"
    )
    date_str = (
        primary_started[:10] if primary_started else datetime.now().strftime("%Y-%m-%d")
    )
    safe_title = sanitize_filename(title)
    base_directory_name = (
        f"{date_str}-{safe_title or primary_session_id[:8]}-stitched"
    )

    directory_name, new_cluster_dir = _resolve_collision(
        archive_dir, base_directory_name, primary_session_id, quiet,
    )
    # Guard against clobbering an existing UNRELATED archive at the target name.
    # _resolve_collision returns the path even if it exists when the existing
    # session is the same; for promotion, "same session" can only be ourself
    # (singleton_dir name == new name, which is a no-op rename).
    if (
        new_cluster_dir.exists()
        and new_cluster_dir != singleton_dir
    ):
        log_warning(
            f"promote: target directory {new_cluster_dir.name} already "
            "exists; aborting to avoid clobbering.",
            quiet,
        )
        return None

    total_user = sum(p["stats"].get("human_messages", 0) for p in parsed)
    total_assistant = sum(p["stats"].get("assistant_messages", 0) for p in parsed)

    concatenated_parts: list[str] = []
    for p in parsed:
        body = p["content"]
        if not body.endswith("\n"):
            body = body + "\n"
        concatenated_parts.append(body)
    concatenated = "".join(concatenated_parts)
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

    model_id = (
        singleton_meta.get("model", {}).get("model_id")
        or new_stats.get("model")
        or "unknown"
    )
    if model_id == "unknown":
        model_id = new_stats.get("model") or model_id
    cc_version = (
        singleton_meta.get("model", {}).get("claude_code_version")
        or new_stats.get("claude_code_version")
    )

    project_dir_str = singleton_meta.get("project", {}).get("directory")
    project_dir = Path(project_dir_str) if project_dir_str else None

    singleton_three_ps = singleton_meta.get("three_ps") or {}
    has_curated_three_ps = any(
        singleton_three_ps.get(k)
        for k in ("prompt_summary", "process_summary", "provenance_summary")
    )
    needs_review = not has_curated_three_ps
    tags = singleton_meta.get("auto_generated", {}).get("tags") or None
    purpose = singleton_meta.get("auto_generated", {}).get("purpose") or None

    constituents_for_meta = [(p["session_id"], p["started_at"]) for p in parsed]

    # DR3 step 4 — rename. Past this point we return new_cluster_dir even on
    # partial failures so the caller sees the new path.
    if new_cluster_dir != singleton_dir:
        try:
            singleton_dir.rename(new_cluster_dir)
        except OSError as exc:
            log_warning(
                f"promote: rename failed ({singleton_dir.name} → "
                f"{new_cluster_dir.name}): {exc}; singleton untouched.",
                quiet,
            )
            return None

    new_raw_path = new_cluster_dir / "raw-transcript.jsonl"
    try:
        new_raw_path.write_text(concatenated, encoding="utf-8")
    except OSError as exc:
        log_warning(f"promote: raw-transcript write failed: {exc}", quiet)
        return new_cluster_dir

    primary_jsonl_path = new_cluster_dir / f"{primary_session_id}.jsonl"
    try:
        primary_jsonl_path.write_text(concatenated, encoding="utf-8")
    except OSError as exc:
        log_warning(f"promote: primary jsonl mirror write failed: {exc}", quiet)

    metadata = _metadata.create_stitched_metadata(
        primary_session_id=primary_session_id,
        constituents=constituents_for_meta,
        raw_transcript_path=new_raw_path,
        aggregated_stats=aggregated_stats,
        directory_name=directory_name,
        started_at=primary_started,
        ended_at=latest_ended,
        duration_minutes=duration_minutes,
        model_id=model_id,
        claude_code_version=cc_version,
        title=title,
        three_ps=singleton_three_ps if has_curated_three_ps else None,
        needs_review=needs_review,
        trivial=False,
        project_dir=project_dir,
        tags=tags,
        purpose=purpose,
    )

    new_meta_path = new_cluster_dir / "session.meta.json"
    try:
        new_meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except OSError as exc:
        log_warning(f"promote: meta write failed: {exc}", quiet)

    manifest = _catalog.load_manifest(archive_dir)
    manifest[singleton_session_id] = str(new_cluster_dir)
    manifest[new_session_id] = str(new_cluster_dir)
    try:
        _catalog.save_manifest(archive_dir, manifest)
    except OSError as exc:
        log_warning(f"promote: manifest save failed: {exc}", quiet)

    try:
        _catalog.update_catalog(archive_dir, metadata)
    except (OSError, KeyError) as exc:
        log_warning(f"promote: catalog update failed: {exc}", quiet)

    try:
        result = subprocess.run(
            [
                "claude-code-transcripts", "json", str(new_raw_path),
                "-o", str(new_cluster_dir), "--json",
            ],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        if result.returncode != 0:
            log_warning(
                f"promote: claude-code-transcripts: {result.stderr.strip()}", quiet,
            )
    except FileNotFoundError:
        log_warning(
            "promote: claude-code-transcripts not on PATH; HTML not re-rendered.",
            quiet,
        )

    _output.update_html_titles(new_cluster_dir, title)

    (new_cluster_dir / ".title").write_text(title, encoding="utf-8")
    (new_cluster_dir / ".last_size").write_text(
        str(new_transcript_path.stat().st_size), encoding="utf-8",
    )

    normalise_text_outputs(new_cluster_dir)
    return new_cluster_dir


def extend_cluster(
    cluster_dir: Path,
    new_session_id: str,
    new_transcript_path: Path,
    *,
    quiet: bool = False,
) -> Path | None:
    """Append one constituent to an already-stitched cluster (Phase 4, AC4.2-AC4.4).

    Mirrors the MELICA scripts/stitch_archive_extend.py contract verbatim:
    appends the new JSONL to both ``raw-transcript.jsonl`` and the primary's
    ``<primary-uuid>.jsonl`` mirror, increments statistics, appends a new
    ``_constituent_sessions`` entry with ``rank = N + 1``, stamps
    ``archive.extended_at``, and fans the manifest so the new UUID resolves to
    this cluster.

    Idempotent on the new UUID: when ``new_session_id`` is already a
    constituent, returns ``cluster_dir`` unchanged after a stderr warning
    (matches ``stitch_archive_extend.py`` lines 189-192 — AC4.4's contract).

    Best-effort: errors are logged via log_warning; the function never raises
    because ``archive()`` calls it from the hook path which must stay
    non-fatal. Returns ``cluster_dir`` on success or after idempotent no-op,
    ``None`` when the cluster is unreadable or the new transcript is missing.
    """
    archive_dir = cluster_dir.parent

    meta_path = cluster_dir / "session.meta.json"
    if not meta_path.exists():
        log_warning(f"extend_cluster: no session.meta.json in {cluster_dir}", quiet)
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log_warning(f"extend_cluster: cannot read cluster meta {meta_path}: {exc}", quiet)
        return None

    constituents = meta.setdefault("_constituent_sessions", [])
    if any(c.get("id") == new_session_id for c in constituents):
        log_warning(
            f"extend_cluster: UUID {new_session_id} already in "
            f"_constituent_sessions of {cluster_dir.name} — no-op.",
            quiet,
        )
        return cluster_dir

    if not new_transcript_path.exists():
        log_warning(f"extend_cluster: transcript not found: {new_transcript_path}", quiet)
        return None
    try:
        new_content = new_transcript_path.read_text(encoding="utf-8")
    except OSError as exc:
        log_warning(f"extend_cluster: cannot read {new_transcript_path}: {exc}", quiet)
        return None
    if not new_content.strip():
        log_warning(f"extend_cluster: transcript is empty: {new_transcript_path}", quiet)
        return None

    raw_path = cluster_dir / "raw-transcript.jsonl"
    primary_uuid = meta.get("session", {}).get("id")
    primary_jsonl_path = (
        cluster_dir / f"{primary_uuid}.jsonl" if primary_uuid else None
    )

    # Ensure single newline boundary before appending.
    appended = new_content if new_content.endswith("\n") else new_content + "\n"

    try:
        with raw_path.open("a", encoding="utf-8") as f:
            f.write(appended)
        if primary_jsonl_path and primary_jsonl_path.exists():
            with primary_jsonl_path.open("a", encoding="utf-8") as f:
                f.write(appended)
    except OSError as exc:
        log_warning(f"extend_cluster: append failed for {cluster_dir.name}: {exc}", quiet)
        return None

    new_stats = _metadata.extract_session_stats(new_content)
    new_user = new_stats.get("human_messages", 0)
    new_assistant = new_stats.get("assistant_messages", 0)
    new_lines = sum(1 for line in new_content.split("\n") if line.strip())

    stats = meta.setdefault("statistics", {})
    stats["user_messages"] = stats.get("user_messages", 0) + new_user
    stats["assistant_messages"] = stats.get("assistant_messages", 0) + new_assistant
    stats["turns"] = stats.get("user_messages", 0) + stats.get("assistant_messages", 0)
    stats["jsonl_lines"] = stats.get("jsonl_lines", 0) + new_lines
    stats["raw_transcript_bytes"] = raw_path.stat().st_size

    constituents.append({"id": new_session_id, "rank": len(constituents) + 1})

    new_ended_at = new_stats.get("ended_at")
    if new_ended_at:
        current_ended = meta.get("session", {}).get("ended_at") or ""
        if new_ended_at > current_ended:
            meta["session"]["ended_at"] = new_ended_at
            started = meta.get("session", {}).get("started_at")
            try:
                start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(new_ended_at.replace("Z", "+00:00"))
                meta["session"]["duration_minutes"] = int(
                    (end_dt - start_dt).total_seconds() / 60
                )
            except (AttributeError, ValueError, TypeError):
                pass

    meta.setdefault("archive", {})["extended_at"] = datetime.now().isoformat()

    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError as exc:
        log_warning(f"extend_cluster: meta write failed for {cluster_dir.name}: {exc}", quiet)
        return cluster_dir

    manifest = _catalog.load_manifest(archive_dir)
    manifest[new_session_id] = str(cluster_dir)
    try:
        _catalog.save_manifest(archive_dir, manifest)
    except OSError as exc:
        log_warning(f"extend_cluster: manifest save failed: {exc}", quiet)

    try:
        _catalog.update_catalog(archive_dir, meta)
    except (OSError, KeyError) as exc:
        log_warning(f"extend_cluster: catalog update failed: {exc}", quiet)

    normalise_text_outputs(cluster_dir)
    return cluster_dir


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

    # Cluster-constituent guard (Phase 4): if existing_dir points to a stitched
    # cluster, this UUID is a constituent. The standard singleton-rewrite path
    # would overwrite the cluster meta and clobber every other constituent's
    # metadata.
    #
    # Phase 6 addition (AC6.1): when three_ps is supplied (the /transcript
    # path), route the Three Ps to the cluster's meta via update_metadata so
    # the curated summary describes the whole arc rather than disappearing.
    # Otherwise: return None on unchanged content (same as the standard
    # same-size short-circuit), else return the cluster dir with a warning —
    # in-place constituent slice updates remain a Phase 5 manual stitch task.
    if existing_dir:
        existing_dir_path = Path(existing_dir)
        existing_meta_path_g = existing_dir_path / "session.meta.json"
        if existing_meta_path_g.exists():
            try:
                existing_meta_g = json.loads(
                    existing_meta_path_g.read_text(encoding="utf-8")
                )
                if existing_meta_g.get("archive", {}).get("stitched"):
                    if three_ps is not None:
                        update_metadata(
                            existing_dir_path,
                            title=provided_title,
                            tags=tags,
                            purpose=purpose,
                            prompt=three_ps.get("prompt_summary"),
                            process=three_ps.get("process_summary"),
                            provenance=three_ps.get("provenance_summary"),
                        )
                        return existing_dir_path
                    marker = existing_dir_path / ".last_size"
                    current_size = transcript_path.stat().st_size
                    if (
                        marker.exists()
                        and int(marker.read_text(encoding="utf-8")) == current_size
                    ):
                        return None
                    log_warning(
                        f"Session {session_id} is a constituent of cluster "
                        f"'{existing_dir_path.name}'; transcript content "
                        "differs from the cluster's recorded baseline but "
                        "in-place constituent updates are deferred to the "
                        "Phase 5 manual `stitch` CLI.",
                        quiet,
                    )
                    return existing_dir_path
            except (json.JSONDecodeError, ValueError, OSError):
                pass

    # Auto-stitch dispatch (Phase 4): only fires for NEW sessions (not yet in
    # manifest) arriving via the hook path (three_ps is None — /transcript
    # supplies three_ps and stays singleton; Phase 6 will surface clusters to
    # /transcript). The match signal is DR1's `customTitle` equality.
    if not existing_dir and three_ps is None:
        custom_title = _metadata.extract_custom_title(content)
        if custom_title:
            match = _find_matching_cluster_or_singleton(
                archive_dir, custom_title, quiet=quiet,
            )
            if match is not None:
                match_dir, is_cluster = match
                if is_cluster:
                    return extend_cluster(
                        match_dir, session_id, transcript_path, quiet=quiet,
                    )
                return promote_singleton_to_cluster(
                    match_dir, session_id, transcript_path, quiet=quiet,
                )

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
