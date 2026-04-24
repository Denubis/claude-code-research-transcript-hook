"""Tests for claude_transcript_archive.archive module."""

import importlib
import json
import subprocess
import time
from pathlib import Path

from claude_transcript_archive.archive import (
    archive,
    find_duplicates,
    generate_title_from_content,
    log_error,
    log_info,
    migrate_legacy,
    sanitize_filename,
)
from claude_transcript_archive.metadata import (
    compute_file_hash,
    create_session_metadata,
    detect_relationship_hints,
    extract_artifacts,
    extract_session_stats,
)

# =============================================================================
# Test generate_title_from_content
# =============================================================================


class TestGenerateTitleFromContent:
    def test_basic_title(self, sample_transcript_content):
        title = generate_title_from_content(sample_transcript_content)
        assert "help" in title.lower() or "python" in title.lower()

    def test_removes_greetings(self):
        # Only removes greetings followed by whitespace
        content = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello can you fix the bug?"}],
                },
            }
        )
        title = generate_title_from_content(content)
        assert not title.lower().startswith("hello")
        assert "fix the bug" in title.lower()

    def test_empty_content(self):
        title = generate_title_from_content("")
        assert title == "Untitled Session"

    def test_truncation(self):
        long_text = "A" * 100
        content = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": long_text}],
                },
            }
        )
        title = generate_title_from_content(content)
        assert len(title) <= 60

    def test_skips_ide_opened_file(self):
        """Test that IDE context messages are skipped for title generation."""
        ide_msg = "<ide_opened_file>The user opened /path/to/file.py</ide_opened_file>"
        content = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": ide_msg}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "Build a GUI for transcription"}],
                        },
                    }
                ),
            ]
        )
        title = generate_title_from_content(content)
        assert "ide_opened_file" not in title.lower()
        assert "gui" in title.lower() or "transcription" in title.lower()

    def test_skips_ide_selection(self):
        """Test that IDE selection messages are skipped."""
        content = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<ide_selection>selected code here</ide_selection>",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "Refactor this function"}],
                        },
                    }
                ),
            ]
        )
        title = generate_title_from_content(content)
        assert "ide_selection" not in title.lower()
        assert "refactor" in title.lower()

    def test_skips_short_messages(self):
        """Test that very short messages are skipped."""
        content = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "ok"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "Implement authentication"}],
                        },
                    }
                ),
            ]
        )
        title = generate_title_from_content(content)
        assert title != "ok"
        assert "authentication" in title.lower()


# =============================================================================
# Test sanitize_filename
# =============================================================================


class TestSanitizeFilename:
    def test_basic_sanitization(self):
        assert sanitize_filename("Hello World") == "hello-world"

    def test_special_chars(self):
        assert sanitize_filename("Test: with/special*chars?") == "test-withspecialchars"

    def test_truncation(self):
        long_title = "a" * 100
        result = sanitize_filename(long_title)
        assert len(result) <= 50

    def test_empty_result(self):
        assert sanitize_filename("???") == "untitled"


# =============================================================================
# Test compute_file_hash (lives in metadata, tested here for historical reasons)
# =============================================================================


class TestComputeFileHash:
    def test_hash_computation(self, temp_dir):
        test_file = temp_dir / "test.txt"
        test_file.write_text("Hello, World!")

        hash1 = compute_file_hash(test_file)
        assert len(hash1) == 64  # SHA256 hex length

        # Same content should give same hash
        test_file2 = temp_dir / "test2.txt"
        test_file2.write_text("Hello, World!")
        hash2 = compute_file_hash(test_file2)
        assert hash1 == hash2

        # Different content should give different hash
        test_file.write_text("Different content")
        hash3 = compute_file_hash(test_file)
        assert hash1 != hash3


# =============================================================================
# Test log_error
# =============================================================================


class TestLogError:
    def test_prints_to_stderr(self, capsys):
        log_error("Test error message", quiet=False)
        captured = capsys.readouterr()
        assert "Test error message" in captured.err

    def test_quiet_mode(self, capsys):
        log_error("Test error message", quiet=True)
        captured = capsys.readouterr()
        assert captured.err == ""


# =============================================================================
# Test log functions
# =============================================================================


class TestLogFunctions:
    def test_log_info_prints(self, capsys):
        """Test log_info prints to stdout."""
        log_info("Test message")
        captured = capsys.readouterr()
        assert "Test message" in captured.out

    def test_log_info_quiet(self, capsys):
        """Test log_info is silent in quiet mode."""
        log_info("Test message", quiet=True)
        captured = capsys.readouterr()
        assert captured.out == ""


# =============================================================================
# Test archive function
# =============================================================================


class TestArchiveFunction:
    def test_archive_creates_output(self, temp_dir, sample_transcript_content):
        """Test that archive creates expected output files."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        archive(
            session_id="test-session-123",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )

        # Check that archive directory was created
        assert archive_dir.exists()
        # Find the created session directory
        session_dirs = list(archive_dir.glob("*-*"))
        assert len(session_dirs) == 1

        session_dir = session_dirs[0]
        assert (session_dir / "session.meta.json").exists()
        assert (session_dir / "raw-transcript.jsonl").exists()
        assert (session_dir / ".title").exists()
        assert (session_dir / ".last_size").exists()

        # Check manifest was created
        assert (archive_dir / ".session_manifest.json").exists()

        # Check catalog was created
        assert (archive_dir / "CATALOG.json").exists()

    def test_archive_nonexistent_file(self, temp_dir, capsys):
        """Test archive with nonexistent file."""
        archive_dir = temp_dir / "archives"
        archive(
            session_id="test",
            transcript_path=Path("/nonexistent/file.jsonl"),
            archive_dir=archive_dir,
            quiet=False,
        )
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_archive_empty_file(self, temp_dir, capsys):
        """Test archive with empty file."""
        transcript = temp_dir / "empty.jsonl"
        transcript.write_text("")
        archive_dir = temp_dir / "archives"

        archive(
            session_id="test",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=False,
        )
        captured = capsys.readouterr()
        assert "empty" in captured.err

    def test_archive_with_title(self, temp_dir, sample_transcript_content):
        """Test archive with provided title."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            provided_title="Custom Title",
            quiet=True,
        )

        session_dirs = list(archive_dir.glob("*custom-title*"))
        assert len(session_dirs) == 1
        assert (session_dirs[0] / ".title").read_text() == "Custom Title\n"

    def test_archive_skips_unchanged(self, temp_dir, sample_transcript_content):
        """Test that archive skips if file unchanged."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        # First archive
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )

        session_dirs = list(archive_dir.glob("*-*"))
        session_dir = session_dirs[0]
        meta_mtime = (session_dir / "session.meta.json").stat().st_mtime

        # Second archive (unchanged)
        time.sleep(0.1)
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )

        # Metadata should not have changed
        new_meta_mtime = (session_dir / "session.meta.json").stat().st_mtime
        assert new_meta_mtime == meta_mtime

    def test_archive_force_updates(self, temp_dir, sample_transcript_content):
        """Test that --force updates even when unchanged."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        # First archive
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )

        session_dirs = list(archive_dir.glob("*-*"))
        session_dir = session_dirs[0]
        meta_mtime = (session_dir / "session.meta.json").stat().st_mtime

        # Second archive with force
        time.sleep(0.1)
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            force=True,
            quiet=True,
        )

        # Metadata should have changed
        new_meta_mtime = (session_dir / "session.meta.json").stat().st_mtime
        assert new_meta_mtime > meta_mtime

    def test_archive_retitle(self, temp_dir, sample_transcript_content):
        """Test --retitle renames directory."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        # First archive with initial title
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            provided_title="Original Title",
            quiet=True,
        )

        old_dirs = list(archive_dir.glob("*original-title*"))
        assert len(old_dirs) == 1

        # Retitle
        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            provided_title="New Title",
            force_retitle=True,
            quiet=True,
        )

        # Old dir should be gone, new dir should exist
        old_dirs = list(archive_dir.glob("*original-title*"))
        new_dirs = list(archive_dir.glob("*new-title*"))
        assert len(old_dirs) == 0
        assert len(new_dirs) == 1

    def test_archive_with_three_ps_sets_no_review(self, temp_dir, sample_transcript_content):
        """Test that providing three_ps sets needs_review to False."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        three_ps = {
            "prompt_summary": "User wanted to test the archive function",
            "process_summary": "Used Read and Edit tools to modify files",
            "provenance_summary": "Part of testing suite development",
        }

        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            three_ps=three_ps,
            quiet=True,
        )

        session_dirs = list(archive_dir.glob("*-*"))
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]

        # Check metadata has needs_review = False
        meta_path = session_dir / "session.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["archive"]["needs_review"] is False
        assert meta["three_ps"]["prompt_summary"] == "User wanted to test the archive function"
        assert meta["three_ps"]["process_summary"] == "Used Read and Edit tools to modify files"
        assert meta["three_ps"]["provenance_summary"] == "Part of testing suite development"

    def test_archive_without_three_ps_sets_needs_review(self, temp_dir, sample_transcript_content):
        """Test that not providing three_ps sets needs_review to True."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text(sample_transcript_content)
        archive_dir = temp_dir / "archives"

        archive(
            session_id="test-session",
            transcript_path=transcript,
            archive_dir=archive_dir,
            quiet=True,
        )

        session_dirs = list(archive_dir.glob("*-*"))
        session_dir = session_dirs[0]

        meta_path = session_dir / "session.meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["archive"]["needs_review"] is True


# =============================================================================
# Test edge cases for coverage
# =============================================================================


class TestEdgeCases:
    def test_extract_stats_with_file_history_snapshot(self):
        """Test that file-history-snapshot entries are skipped."""
        content = json.dumps(
            {
                "type": "file-history-snapshot",
                "timestamp": "2026-01-14T10:00:00.000Z",
                "files": {},
            }
        )
        stats = extract_session_stats(content)
        assert stats["turns"] == 0

    def test_extract_artifacts_read_then_edit(self):
        """Test that files read then edited appear in modified, not referenced."""
        content = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Read",
                                    "input": {"file_path": "/test.py"},
                                },
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Edit",
                                    "input": {"file_path": "/test.py"},
                                },
                            ],
                        },
                    }
                ),
            ]
        )
        artifacts = extract_artifacts(content)
        assert len(artifacts["modified"]) == 1
        assert len(artifacts["referenced"]) == 0

    def test_create_metadata_without_project(
        self, sample_transcript_file, sample_transcript_content
    ):
        """Test metadata creation without project directory."""
        stats = extract_session_stats(sample_transcript_content)
        artifacts = extract_artifacts(sample_transcript_content)
        hints = detect_relationship_hints(sample_transcript_content)

        metadata = create_session_metadata(
            session_id="test-123",
            transcript_path=sample_transcript_file,
            stats=stats,
            title="Test",
            artifacts=artifacts,
            relationship_hints=hints,
            plan_files=[],
            directory_name="2026-01-14-test",
            three_ps=None,
            needs_review=True,
            project_dir=None,
        )
        # project_dir=None results in None values
        assert metadata["project"]["name"] is None
        assert metadata["project"]["directory"] is None
        assert metadata["three_ps"]["prompt_summary"] == ""
        assert metadata["relationships"]["isPartOf"] == []


# =============================================================================
# AC verification tests
# =============================================================================


class TestArchiveModuleDecomposition:
    def test_ac1_1_independent_import(self):
        """archive module can be imported independently."""
        assert callable(archive)
        assert archive.__module__ == "claude_transcript_archive.archive"

    def test_ac1_3_no_reexport_from_cli(self):
        """CLI module does not re-export archive internals."""
        cli = importlib.import_module("claude_transcript_archive.cli")
        # generate_title_from_content should NOT be on cli
        assert not hasattr(cli, "generate_title_from_content")


# =============================================================================
# Test mount recovery
# =============================================================================


class TestMountRecovery:
    def test_branch_target_remounts_missing_worktree(self, temp_dir, monkeypatch):
        """Missing .ai-transcripts/ + branch exists -> auto-remounts."""
        archive_dir = temp_dir / ".ai-transcripts"
        # Don't create archive_dir -- it's missing
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"role":"user","content":"Hello"}}\n')

        # Mock subprocess.run to simulate: branch exists, worktree add succeeds
        call_log = []
        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            call_log.append(cmd)
            if cmd[:3] == ["git", "branch", "--list"]:
                return type("R", (), {"stdout": "  transcripts\n", "returncode": 0})()
            if cmd[:3] == ["git", "worktree", "add"]:
                # Actually create the directory so archive can proceed
                archive_dir.mkdir(parents=True)
                return type("R", (), {"stdout": "", "returncode": 0})()
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr("claude_transcript_archive.archive.subprocess.run", mock_run)

        archive(
            "test-session",
            transcript,
            archive_dir,
            target="branch",
            quiet=True,
        )
        # Should have tried to remount
        assert any("worktree" in str(c) for c in call_log)

    def test_branch_target_no_branch_returns_none(self, temp_dir, monkeypatch):
        """Missing .ai-transcripts/ + no branch -> returns None with error."""
        archive_dir = temp_dir / ".ai-transcripts"
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"role":"user","content":"Hello"}}\n')

        def mock_run(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "branch", "--list"]:
                return type("R", (), {"stdout": "", "returncode": 0})()
            return subprocess.run(cmd, *args, check=False, **kwargs)

        monkeypatch.setattr("claude_transcript_archive.archive.subprocess.run", mock_run)

        result = archive(
            "test-session",
            transcript,
            archive_dir,
            target="branch",
            quiet=True,
        )
        assert result is None

    def test_existing_worktree_no_recovery_needed(self, temp_dir, sample_transcript_content):
        """Existing .ai-transcripts/ -> normal archive, no recovery."""
        archive_dir = temp_dir / ".ai-transcripts"
        archive_dir.mkdir()
        transcript = temp_dir / "test.jsonl"
        transcript.write_text(sample_transcript_content)

        # This should work normally without any git calls
        result = archive(
            "test-session",
            transcript,
            archive_dir,
            target="branch",
            quiet=True,
        )
        # Should proceed normally (archive creates output dir)
        assert result is not None
        assert result.exists()

    def test_non_branch_target_no_recovery(self, temp_dir, sample_transcript_content):
        """target='main' -> no mount recovery, writes to archive_dir directly."""
        archive_dir = temp_dir / "ai_transcripts"
        transcript = temp_dir / "test.jsonl"
        transcript.write_text(sample_transcript_content)

        # archive_dir doesn't exist but target is not "branch" so no recovery
        # archive() will create it via mkdir(parents=True)
        result = archive(
            "test-session",
            transcript,
            archive_dir,
            target="main",
            quiet=True,
        )
        # Should proceed normally
        assert result is not None

    def test_git_error_during_recovery_returns_none(self, temp_dir, monkeypatch):
        """Git subprocess failure during recovery -> returns None."""
        archive_dir = temp_dir / ".ai-transcripts"
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"role":"user","content":"Hello"}}\n')

        def mock_run(cmd, *args, **kwargs):
            if cmd[:3] == ["git", "branch", "--list"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.run(cmd, *args, check=False, **kwargs)

        monkeypatch.setattr("claude_transcript_archive.archive.subprocess.run", mock_run)

        result = archive(
            "test-session",
            transcript,
            archive_dir,
            target="branch",
            quiet=True,
        )
        assert result is None


# =============================================================================
# Test find_duplicates
# =============================================================================


class TestFindDuplicates:
    def test_detects_duplicates(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        for i in range(2):
            d = archive_dir / f"2024-01-0{i + 1}-session"
            d.mkdir()
            (d / "session.meta.json").write_text(
                json.dumps(
                    {
                        "session": {"id": "same-session"},
                        "archive": {"directory_name": d.name},
                    }
                )
            )
        dupes = find_duplicates(archive_dir)
        assert len(dupes) == 1
        assert dupes[0][0] == "same-session"
        assert len(dupes[0][1]) == 2

    def test_no_duplicates(self, temp_dir):
        archive_dir = temp_dir / "archive"
        archive_dir.mkdir()
        for sid in ["session-a", "session-b"]:
            d = archive_dir / f"2024-01-01-{sid}"
            d.mkdir()
            (d / "session.meta.json").write_text(
                json.dumps(
                    {
                        "session": {"id": sid},
                        "archive": {"directory_name": d.name},
                    }
                )
            )
        assert find_duplicates(archive_dir) == []


# =============================================================================
# Test migrate_legacy
# =============================================================================


class TestMigrateLegacy:
    def test_migrates_archive_dirs(self, temp_dir):
        legacy = temp_dir / "ai_transcripts"
        legacy.mkdir()
        target = temp_dir / ".ai-transcripts"
        target.mkdir()

        # Create legacy archive with sidecar
        d = legacy / "2024-01-01-old-session"
        d.mkdir()
        (d / "session.meta.json").write_text('{"session": {"id": "old"}}')

        migrated = migrate_legacy(legacy, target, dry_run=False)
        assert len(migrated) == 1
        assert (target / "2024-01-01-old-session").exists()
        assert not (legacy / "2024-01-01-old-session").exists()

    def test_dry_run_no_changes(self, temp_dir):
        legacy = temp_dir / "ai_transcripts"
        legacy.mkdir()
        target = temp_dir / ".ai-transcripts"
        target.mkdir()

        d = legacy / "2024-01-01-old-session"
        d.mkdir()
        (d / "session.meta.json").write_text('{"session": {"id": "old"}}')

        migrated = migrate_legacy(legacy, target, dry_run=True)
        assert len(migrated) == 1  # reports what would be migrated
        assert (legacy / "2024-01-01-old-session").exists()  # still there

    def test_nonexistent_legacy_dir(self, temp_dir):
        assert migrate_legacy(temp_dir / "nope", temp_dir / "target", dry_run=True) == []
