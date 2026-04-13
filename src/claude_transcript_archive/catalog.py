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
        except (json.JSONDecodeError, ValueError):
            continue  # Skip malformed sidecars

        session_id = metadata.get("session", {}).get("id")
        if not session_id:
            continue

        archive_info = metadata.get("archive", {})
        directory_name = archive_info.get("directory_name", sidecar_path.parent.name)

        # Build manifest entry
        manifest[session_id] = str(sidecar_path.parent)

        # Build catalog session entry
        sessions.append({
            "session_id": session_id,
            "title": metadata.get("auto_generated", {}).get("title", "Untitled"),
            "started_at": metadata.get("session", {}).get("started_at"),
            "directory": directory_name,
            "needs_review": archive_info.get("needs_review", True),
            "trivial": archive_info.get("trivial", False),
        })

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
