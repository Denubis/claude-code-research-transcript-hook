"""Tests for claude_transcript_archive.catalog module."""

import importlib
import json

from claude_transcript_archive.catalog import (
    get_catalog_path,
    get_manifest_path,
    load_catalog,
    load_manifest,
    rebuild_indexes,
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

    def test_manifest_paths_relative_on_disk(self, temp_dir):
        """Paths under archive_dir are written relative on disk so the manifest
        is portable when a repo's ai_transcripts/ is committed and cloned."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        session_dir = archive_dir / "2026-01-14-test"
        session_dir.mkdir()

        save_manifest(archive_dir, {"abc": str(session_dir)})
        on_disk = json.loads((archive_dir / ".session_manifest.json").read_text())
        assert on_disk["abc"] == "2026-01-14-test"

    def test_manifest_load_resolves_relative_paths(self, temp_dir):
        """load_manifest returns absolute paths so callers don't change."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        (archive_dir / ".session_manifest.json").write_text(
            json.dumps({"abc": "2026-01-14-test"})
        )
        loaded = load_manifest(archive_dir)
        assert loaded["abc"] == str(archive_dir / "2026-01-14-test")

    def test_manifest_legacy_absolute_paths_preserved(self, temp_dir):
        """Existing absolute-path manifests from prior versions still load."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        absolute = "/some/absolute/path"
        (archive_dir / ".session_manifest.json").write_text(
            json.dumps({"legacy": absolute})
        )
        loaded = load_manifest(archive_dir)
        assert loaded["legacy"] == absolute

    def test_manifest_paths_outside_archive_dir_kept_absolute(self, temp_dir):
        """Save preserves absolute paths that aren't under archive_dir."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        outside = temp_dir / "elsewhere" / "session"

        save_manifest(archive_dir, {"sid": str(outside)})
        on_disk = json.loads((archive_dir / ".session_manifest.json").read_text())
        assert on_disk["sid"] == str(outside)


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

    def test_load_catalog_normalises_session_id_to_id(self, temp_dir):
        """load_catalog should canonicalise legacy 'session_id' entries to 'id'
        so downstream readers see one schema."""
        legacy = {
            "schema_version": SCHEMA_VERSION,
            "sessions": [
                {"session_id": "legacy-1", "title": "L1"},
                {"id": "modern-1", "title": "M1"},
                {"session_id": "legacy-2", "id": "modern-2", "title": "Both"},
            ],
        }
        (temp_dir / "CATALOG.json").write_text(json.dumps(legacy))

        catalog = load_catalog(temp_dir)
        assert catalog["sessions"][0]["id"] == "legacy-1"
        assert catalog["sessions"][1]["id"] == "modern-1"
        # When both present, 'id' wins (matches update_catalog as canonical)
        assert catalog["sessions"][2]["id"] == "modern-2"

    def test_update_catalog_tolerates_session_id_entries(self, temp_dir):
        """Regression: a catalog written by rebuild_indexes uses 'session_id';
        update_catalog must not KeyError when looking up existing entries."""
        # Seed a catalog with the rebuild_indexes-style key
        legacy_catalog = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": None,
            "archive_location": str(temp_dir),
            "total_sessions": 1,
            "needs_review_count": 0,
            "sessions": [
                {
                    "session_id": "rebuilt-session",
                    "title": "From rebuild",
                    "started_at": "2026-01-14T09:00:00Z",
                    "directory": "rebuilt-dir",
                    "needs_review": False,
                    "trivial": False,
                }
            ],
        }
        (temp_dir / "CATALOG.json").write_text(json.dumps(legacy_catalog))

        new_meta = {
            "session": {
                "id": "fresh-session",
                "started_at": "2026-01-14T10:00:00Z",
                "duration_minutes": 5,
            },
            "auto_generated": {"title": "Fresh", "purpose": "", "tags": []},
            "archive": {"directory_name": "fresh-dir", "needs_review": True},
        }
        # Must not raise KeyError('id')
        update_catalog(temp_dir, new_meta)

        catalog = load_catalog(temp_dir)
        ids = {s.get("id") or s.get("session_id") for s in catalog["sessions"]}
        assert ids == {"rebuilt-session", "fresh-session"}


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


# =============================================================================
# Test rebuild_indexes
# =============================================================================


class TestRebuildIndexes:
    def test_rebuilds_from_sidecars(self, temp_dir):
        """3 archive dirs with sidecars -> correct catalog with 3 sessions."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()

        for i in range(3):
            session_dir = archive_dir / f"2024-01-0{i+1}-session-{i}"
            session_dir.mkdir()
            sidecar = {
                "session": {"id": f"session-{i}", "started_at": f"2024-01-0{i+1}T10:00:00"},
                "auto_generated": {"title": f"Session {i}"},
                "archive": {
                    "directory_name": session_dir.name,
                    "needs_review": (i % 2 == 0),  # 0 and 2 need review
                    "trivial": False,
                },
            }
            (session_dir / "session.meta.json").write_text(json.dumps(sidecar))

        count = rebuild_indexes(archive_dir)
        assert count == 3

        # Check manifest
        manifest = load_manifest(archive_dir)
        assert len(manifest) == 3
        assert "session-0" in manifest

        # Check catalog
        catalog = load_catalog(archive_dir)
        assert catalog["total_sessions"] == 3
        assert catalog["needs_review_count"] == 2
        assert len(catalog["sessions"]) == 3

    def test_needs_review_count_correct(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()

        for i, needs_review in enumerate([True, False, True, False, False]):
            session_dir = archive_dir / f"session-{i}"
            session_dir.mkdir()
            sidecar = {
                "session": {"id": f"s-{i}", "started_at": f"2024-01-0{i+1}T10:00:00"},
                "auto_generated": {"title": f"S{i}"},
                "archive": {
                    "directory_name": session_dir.name,
                    "needs_review": needs_review,
                    "trivial": False,
                },
            }
            (session_dir / "session.meta.json").write_text(json.dumps(sidecar))

        rebuild_indexes(archive_dir)
        catalog = load_catalog(archive_dir)
        assert catalog["needs_review_count"] == 2

    def test_rebuild_entries_match_update_catalog_shape(self, temp_dir):
        """rebuild_indexes and update_catalog must produce the same entry shape
        so the two functions cannot drift again. Key 'id' is canonical."""
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        session_dir = archive_dir / "2026-01-14-rebuild-shape"
        session_dir.mkdir()
        sidecar = {
            "session": {
                "id": "shape-test",
                "started_at": "2026-01-14T10:00:00Z",
                "duration_minutes": 12,
            },
            "auto_generated": {
                "title": "Shape Test",
                "purpose": "Verify shape",
                "tags": ["a", "b"],
            },
            "archive": {
                "directory_name": session_dir.name,
                "needs_review": False,
                "trivial": True,
            },
        }
        (session_dir / "session.meta.json").write_text(json.dumps(sidecar))

        rebuild_indexes(archive_dir)
        catalog = load_catalog(archive_dir)
        entry = catalog["sessions"][0]

        # Canonical key
        assert entry["id"] == "shape-test"
        # Full shape — includes the fields update_catalog also writes
        assert entry["title"] == "Shape Test"
        assert entry["purpose"] == "Verify shape"
        assert entry["tags"] == ["a", "b"]
        assert entry["duration_minutes"] == 12
        assert entry["directory"] == session_dir.name
        assert entry["needs_review"] is False
        assert entry["trivial"] is True

    def test_missing_sidecars_skipped(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()

        # One valid, one invalid
        good_dir = archive_dir / "good-session"
        good_dir.mkdir()
        (good_dir / "session.meta.json").write_text(json.dumps({
            "session": {"id": "good", "started_at": "2024-01-01T10:00:00"},
            "auto_generated": {"title": "Good"},
            "archive": {"directory_name": "good-session", "needs_review": False, "trivial": False},
        }))

        bad_dir = archive_dir / "bad-session"
        bad_dir.mkdir()
        (bad_dir / "session.meta.json").write_text("not valid json{{{")

        count = rebuild_indexes(archive_dir)
        assert count == 1
