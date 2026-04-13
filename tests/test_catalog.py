"""Tests for claude_transcript_archive.catalog module."""

import importlib
import json

from claude_transcript_archive.catalog import (
    get_catalog_path,
    get_manifest_path,
    load_catalog,
    load_manifest,
    save_catalog,
    save_manifest,
    update_catalog,
    write_metadata_sidecar,
)
from claude_transcript_archive.metadata import SCHEMA_VERSION

# =============================================================================
# Test manifest functions
# =============================================================================


class TestManifestFunctions:
    def test_load_empty_manifest(self, temp_dir):
        result = load_manifest(temp_dir)
        assert result == {}

    def test_save_and_load_manifest(self, temp_dir):
        manifest = {"session1": "/path/to/session1", "session2": "/path/to/session2"}
        save_manifest(temp_dir, manifest)
        loaded = load_manifest(temp_dir)
        assert loaded == manifest

    def test_manifest_creates_directory(self, temp_dir):
        new_dir = temp_dir / "new" / "nested" / "dir"
        save_manifest(new_dir, {"test": "value"})
        assert new_dir.exists()
        assert (new_dir / ".session_manifest.json").exists()

    def test_get_manifest_path(self, temp_dir):
        result = get_manifest_path(temp_dir)
        assert result == temp_dir / ".session_manifest.json"


# =============================================================================
# Test catalog functions
# =============================================================================


class TestCatalogFunctions:
    def test_load_empty_catalog(self, temp_dir):
        result = load_catalog(temp_dir)
        assert result["schema_version"] == SCHEMA_VERSION
        assert result["sessions"] == []

    def test_save_and_load_catalog(self, temp_dir):
        catalog = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": None,
            "archive_location": str(temp_dir),
            "total_sessions": 1,
            "needs_review_count": 1,
            "sessions": [{"id": "test", "needs_review": True}],
        }
        save_catalog(temp_dir, catalog)
        loaded = load_catalog(temp_dir)
        assert loaded["total_sessions"] == 1
        assert loaded["needs_review_count"] == 1
        assert loaded["generated_at"] is not None

    def test_update_catalog_new_session(self, temp_dir):
        metadata = {
            "session": {
                "id": "new-session",
                "started_at": "2026-01-14T10:00:00Z",
                "duration_minutes": 30,
            },
            "auto_generated": {"title": "Test Session", "purpose": "Testing", "tags": ["test"]},
            "archive": {"directory_name": "2026-01-14-test", "needs_review": False},
        }
        update_catalog(temp_dir, metadata)
        catalog = load_catalog(temp_dir)
        assert len(catalog["sessions"]) == 1
        assert catalog["sessions"][0]["id"] == "new-session"

    def test_update_catalog_existing_session(self, temp_dir):
        # Add first session
        metadata1 = {
            "session": {
                "id": "session-1",
                "started_at": "2026-01-14T10:00:00Z",
                "duration_minutes": 30,
            },
            "auto_generated": {"title": "First", "purpose": "", "tags": []},
            "archive": {"directory_name": "dir1", "needs_review": True},
        }
        update_catalog(temp_dir, metadata1)

        # Update same session
        metadata2 = {
            "session": {
                "id": "session-1",
                "started_at": "2026-01-14T10:00:00Z",
                "duration_minutes": 45,
            },
            "auto_generated": {
                "title": "Updated",
                "purpose": "Now with purpose",
                "tags": ["updated"],
            },
            "archive": {"directory_name": "dir1", "needs_review": False},
        }
        update_catalog(temp_dir, metadata2)

        catalog = load_catalog(temp_dir)
        assert len(catalog["sessions"]) == 1
        assert catalog["sessions"][0]["title"] == "Updated"
        assert catalog["needs_review_count"] == 0

    def test_get_catalog_path(self, temp_dir):
        result = get_catalog_path(temp_dir)
        assert result == temp_dir / "CATALOG.json"

    def test_catalog_loads_existing(self, temp_dir):
        """Test loading existing catalog preserves data."""
        existing_catalog = {
            "schema_version": SCHEMA_VERSION,
            "sessions": [{"id": "existing", "title": "Test"}],
        }
        catalog_path = temp_dir / "CATALOG.json"
        catalog_path.write_text(json.dumps(existing_catalog))

        loaded = load_catalog(temp_dir)
        assert loaded["sessions"][0]["id"] == "existing"
        assert loaded["sessions"][0]["title"] == "Test"

    def test_catalog_empty_on_missing(self, temp_dir):
        """Test load_catalog returns empty structure when file doesn't exist."""
        loaded = load_catalog(temp_dir / "nonexistent")
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["sessions"] == []
        assert loaded["total_sessions"] == 0


# =============================================================================
# Test write_metadata_sidecar
# =============================================================================


class TestWriteMetadataSidecar:
    def test_writes_to_archive(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        transcript = temp_dir / "transcript.jsonl"
        transcript.touch()

        metadata = {"test": "data"}
        write_metadata_sidecar(archive_dir, transcript, metadata)

        assert (archive_dir / "session.meta.json").exists()
        loaded = json.loads((archive_dir / "session.meta.json").read_text())
        assert loaded == metadata

    def test_writes_sidecar_to_original(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        transcript = temp_dir / "transcript.jsonl"
        transcript.touch()

        metadata = {"test": "data"}
        write_metadata_sidecar(archive_dir, transcript, metadata)

        sidecar = temp_dir / "transcript.jsonl.meta.json"
        assert sidecar.exists()


# =============================================================================
# AC verification tests
# =============================================================================


class TestCatalogModuleDecomposition:
    def test_ac1_1_independent_import(self):
        catalog_mod = importlib.import_module("claude_transcript_archive.catalog")
        assert callable(catalog_mod.load_catalog)

    def test_ac1_3_no_reexport_from_cli(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "load_catalog")

    def test_ac1_3_no_reexport_manifest_from_cli(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "load_manifest")

    def test_ac1_3_no_reexport_write_metadata_from_cli(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "write_metadata_sidecar")
