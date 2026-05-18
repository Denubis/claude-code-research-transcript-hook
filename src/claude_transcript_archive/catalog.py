"""Session manifest, catalog index, and metadata sidecar management."""

import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_transcript_archive import metadata as _metadata


def get_manifest_path(archive_dir: Path) -> Path:
    """Get the manifest file path for an archive directory."""
    return archive_dir / ".session_manifest.json"


def _to_portable(value: str, archive_dir: Path) -> str:
    """Relativise to archive_dir for portable on-disk storage.

    Paths under archive_dir are stored relative so a committed ai_transcripts/
    rebuilds correctly when cloned to a different machine. Paths elsewhere
    (or already relative) are preserved as-is.
    """
    if not Path(value).is_absolute():
        return value
    try:
        return str(Path(value).resolve().relative_to(archive_dir.resolve()))
    except ValueError:
        return value


def _from_portable(value: str, archive_dir: Path) -> str:
    """Resolve a stored manifest value back to an absolute path.

    Callers expect absolute paths. Relative values were written by save_manifest
    on this or a peer machine; absolute values are either legacy or external.
    """
    return value if Path(value).is_absolute() else str(archive_dir / value)


def load_manifest(archive_dir: Path) -> dict:
    """Load session -> directory mapping. Returns absolute paths."""
    manifest_path = get_manifest_path(archive_dir)
    if manifest_path.exists():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {sid: _from_portable(path, archive_dir) for sid, path in raw.items()}
    return {}


def save_manifest(archive_dir: Path, manifest: dict):
    """Save session -> directory mapping. Paths under archive_dir become
    relative on disk so a committed archive is portable across machines."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    portable = {sid: _to_portable(path, archive_dir) for sid, path in manifest.items()}
    get_manifest_path(archive_dir).write_text(
        json.dumps(portable, indent=2), encoding="utf-8"
    )


def get_catalog_path(archive_dir: Path) -> Path:
    """Get the catalog file path."""
    return archive_dir / "CATALOG.json"


def _normalise_session_entry(entry: dict) -> dict:
    """Canonicalise a catalog session entry on 'id'.

    Earlier versions of rebuild_indexes wrote 'session_id'; update_catalog
    writes 'id'. Normalise on load so readers see one schema.
    """
    if "id" not in entry and "session_id" in entry:
        entry["id"] = entry["session_id"]
    return entry


def load_catalog(archive_dir: Path) -> dict:
    """Load CATALOG.json or create empty structure."""
    catalog_path = get_catalog_path(archive_dir)
    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["sessions"] = [
                _normalise_session_entry(s) for s in catalog.get("sessions", [])
            ]
            return catalog
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


def _build_catalog_entry(session_metadata: dict, *, directory_fallback: str = "") -> dict:
    """Construct a catalog session entry from a session.meta.json dict.

    Shared between update_catalog (hook fires) and rebuild_indexes (manual
    regeneration) so they cannot drift. 'id' is canonical; rebuild used to
    write 'session_id' which broke update_catalog at catalog.py:82.
    """
    session = session_metadata.get("session", {})
    auto = session_metadata.get("auto_generated", {})
    archive = session_metadata.get("archive", {})
    return {
        "id": session.get("id"),
        "directory": archive.get("directory_name", directory_fallback),
        "title": auto.get("title", "Untitled"),
        "purpose": auto.get("purpose", ""),
        "started_at": session.get("started_at"),
        "duration_minutes": session.get("duration_minutes"),
        "tags": auto.get("tags", []),
        "needs_review": archive.get("needs_review", True),
        "trivial": archive.get("trivial", False),
    }


def update_catalog(archive_dir: Path, session_metadata: dict):
    """Update CATALOG.json with new/updated session entry."""
    catalog = load_catalog(archive_dir)

    session_id = session_metadata["session"]["id"]
    new_entry = _build_catalog_entry(session_metadata)

    # Update existing or append new. Tolerate legacy entries from
    # rebuild_indexes that keyed under "session_id" — see archive.py:680.
    existing_ids: dict[str, int] = {}
    for i, s in enumerate(catalog["sessions"]):
        key = s.get("id") or s.get("session_id")
        if key is not None:
            existing_ids[key] = i
    if session_id in existing_ids:
        catalog["sessions"][existing_ids[session_id]] = new_entry
    else:
        catalog["sessions"].append(new_entry)

    # Sort by date (newest first), handle None
    catalog["sessions"].sort(
        key=lambda s: s.get("started_at") or "", reverse=True
    )

    save_catalog(archive_dir, catalog)


def rebuild_indexes(archive_dir: Path) -> int:
    """Rebuild manifest and catalog from session.meta.json sidecars.

    Globs for */session.meta.json under archive_dir, reads each,
    rebuilds .session_manifest.json and CATALOG.json.

    Returns the number of sessions found.
    """
    manifest = {}
    sessions = []

    for sidecar_path in sorted(archive_dir.glob("*/session.meta.json")):
        try:
            metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue  # Skip malformed or unreadable sidecars

        session_id = metadata.get("session", {}).get("id")
        if not session_id:
            continue

        # Build manifest entry
        manifest[session_id] = str(sidecar_path.parent)

        # Build catalog session entry — shared shape with update_catalog
        sessions.append(
            _build_catalog_entry(metadata, directory_fallback=sidecar_path.parent.name)
        )

    # Save manifest
    save_manifest(archive_dir, manifest)

    # Build and save catalog
    needs_review_count = sum(1 for s in sessions if s.get("needs_review", True))
    catalog = {
        "schema_version": _metadata.SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "archive_location": str(archive_dir),
        "total_sessions": len(sessions),
        "needs_review_count": needs_review_count,
        "sessions": sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True),
    }
    save_catalog(archive_dir, catalog)

    return len(sessions)


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
