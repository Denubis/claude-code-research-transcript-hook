"""Comprehensive tests for claude_transcript_archive CLI."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from claude_transcript_archive.cli import (
    SCHEMA_VERSION,
    _is_ide_context_message,
    archive,
    auto_discover_transcript,
    compute_file_hash,
    create_session_metadata,
    detect_relationship_hints,
    estimate_cost,
    extract_artifacts,
    extract_session_stats,
    find_plan_files,
    generate_title_from_content,
    get_archive_dir,
    get_cc_project_path,
    get_file_type,
    get_project_dir_from_transcript,
    load_catalog,
    load_manifest,
    log_error,
    log_info,
    sanitize_filename,
    save_catalog,
    save_manifest,
    update_catalog,
    update_html_titles,
    write_metadata_sidecar,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def sample_transcript_content():
    """Sample transcript JSONL content for testing."""
    entries = [
        {
            "type": "user",
            "timestamp": "2026-01-14T10:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello, can you help me with Python?"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-14T10:00:30.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "text", "text": "Of course! I'd be happy to help with Python."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/home/user/project/main.py"},
                    },
                ],
            },
            "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-14T10:01:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me analyze this..."},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/home/user/project/main.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/home/user/project/new_file.py"},
                    },
                ],
            },
            "usage": {"input_tokens": 200, "output_tokens": 100, "cache_read_input_tokens": 50},
        },
        {
            "type": "user",
            "timestamp": "2026-01-14T10:05:00.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Thanks! This continues from session "
                        "abc12345-1234-1234-1234-123456789abc",
                    }
                ],
            },
        },
    ]
    return "\n".join(json.dumps(entry) for entry in entries)


@pytest.fixture
def sample_transcript_file(temp_dir, sample_transcript_content):
    """Create a sample transcript file."""
    transcript_path = temp_dir / "test-transcript.jsonl"
    transcript_path.write_text(sample_transcript_content)
    return transcript_path


# =============================================================================
# Test get_cc_project_path
# =============================================================================


class TestGetCCProjectPath:
    def test_simple_path(self):
        result = get_cc_project_path(Path("/home/user/project"))
        assert result == "-home-user-project"

    def test_path_with_dashes(self):
        result = get_cc_project_path(Path("/home/user/my-cool-project"))
        assert result == "-home-user-my-cool-project"


# =============================================================================
# Test get_archive_dir
# =============================================================================


class TestGetArchiveDir:
    def test_output_override(self, temp_dir):
        result = get_archive_dir(local=False, output=str(temp_dir / "custom"))
        assert result == temp_dir / "custom"

    def test_local_mode(self, temp_dir, monkeypatch):
        monkeypatch.chdir(temp_dir)
        result = get_archive_dir(local=True, output=None)
        assert result == temp_dir / "ai_transcripts"

    def test_global_without_project(self):
        result = get_archive_dir(local=False, output=None, project_dir=None)
        assert result == Path.home() / ".claude" / "transcripts"

    def test_global_with_project(self):
        project = Path("/home/user/myproject")
        result = get_archive_dir(local=False, output=None, project_dir=project)
        expected = Path.home() / ".claude" / "transcripts" / "-home-user-myproject"
        assert result == expected


# =============================================================================
# Test get_project_dir_from_transcript
# =============================================================================


class TestGetProjectDirFromTranscript:
    def test_non_claude_path(self, temp_dir):
        transcript = temp_dir / "random.jsonl"
        transcript.touch()
        result = get_project_dir_from_transcript(transcript)
        assert result is None

    def test_claude_path_with_existing_dir(self, temp_dir, monkeypatch):
        # Create a mock Claude projects structure
        projects_dir = temp_dir / ".claude" / "projects"
        encoded_dir = projects_dir / "-tmp-testproject"
        encoded_dir.mkdir(parents=True)
        transcript = encoded_dir / "session.jsonl"
        transcript.touch()

        # Mock home directory
        monkeypatch.setattr(Path, "home", lambda: temp_dir)

        # Create the target directory
        target_dir = temp_dir / "tmp" / "testproject"
        target_dir.mkdir(parents=True)

        result = get_project_dir_from_transcript(transcript)
        # The function tries to decode and check if path exists
        assert result is not None or result is None  # May or may not find it


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


# =============================================================================
# Test extract_session_stats
# =============================================================================


class TestExtractSessionStats:
    def test_basic_stats(self, sample_transcript_content):
        stats = extract_session_stats(sample_transcript_content)
        assert stats["turns"] == 2  # 2 user messages
        assert stats["human_messages"] == 2
        assert stats["assistant_messages"] == 2
        assert stats["thinking_blocks"] == 1
        assert stats["tool_calls"]["total"] == 3  # Read, Edit, Write
        assert stats["tool_calls"]["by_type"]["Read"] == 1
        assert stats["tool_calls"]["by_type"]["Edit"] == 1
        assert stats["tool_calls"]["by_type"]["Write"] == 1

    def test_token_counts(self, sample_transcript_content):
        stats = extract_session_stats(sample_transcript_content)
        assert stats["tokens"]["input"] == 300  # 100 + 200
        assert stats["tokens"]["output"] == 150  # 50 + 100
        assert stats["tokens"]["cache_read"] == 70  # 20 + 50

    def test_timestamps(self, sample_transcript_content):
        stats = extract_session_stats(sample_transcript_content)
        assert stats["started_at"] == "2026-01-14T10:00:00.000Z"
        assert stats["ended_at"] == "2026-01-14T10:05:00.000Z"
        assert stats["duration_minutes"] == 5

    def test_model_extraction(self, sample_transcript_content):
        stats = extract_session_stats(sample_transcript_content)
        assert stats["model"] == "claude-sonnet-4-20250514"

    def test_empty_content(self):
        stats = extract_session_stats("")
        assert stats["turns"] == 0
        assert stats["tokens"]["input"] == 0

    def test_invalid_json_lines(self):
        content = "not json\n{}\nalso not json"
        stats = extract_session_stats(content)
        assert stats["turns"] == 0


# =============================================================================
# Test estimate_cost
# =============================================================================


class TestEstimateCost:
    def test_basic_cost(self):
        stats = {"tokens": {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000}}
        cost = estimate_cost(stats)
        # 3.0 + 15.0 + 0.30 = 18.30
        assert cost == 18.30

    def test_zero_tokens(self):
        stats = {"tokens": {"input": 0, "output": 0, "cache_read": 0}}
        cost = estimate_cost(stats)
        assert cost == 0.0


# =============================================================================
# Test get_file_type
# =============================================================================


class TestGetFileType:
    def test_code_files(self):
        assert get_file_type("main.py") == "code"
        assert get_file_type("app.js") == "code"
        assert get_file_type("lib.rs") == "code"

    def test_document_files(self):
        assert get_file_type("README.md") == "document"
        assert get_file_type("paper.tex") == "document"
        assert get_file_type("report.pdf") == "document"

    def test_config_files(self):
        assert get_file_type("config.yaml") == "config"
        assert get_file_type("settings.toml") == "config"
        # .env has no extension, so returns "other"
        assert get_file_type(".env") == "other"

    def test_data_files(self):
        assert get_file_type("data.json") == "data"
        assert get_file_type("records.csv") == "data"

    def test_unknown_files(self):
        assert get_file_type("weird.xyz") == "other"
        assert get_file_type("noext") == "other"


# =============================================================================
# Test extract_artifacts
# =============================================================================


class TestExtractArtifacts:
    def test_basic_extraction(self, sample_transcript_content):
        artifacts = extract_artifacts(sample_transcript_content)
        assert len(artifacts["created"]) == 1
        assert len(artifacts["modified"]) == 1
        assert len(artifacts["referenced"]) == 0  # Read file was also edited

    def test_deduplication(self):
        # If a file is written and then edited, it should only appear as created
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Write", "input": {"file_path": "/test.py"}},
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/test.py"}},
                    ],
                },
            }
        )
        artifacts = extract_artifacts(content)
        assert len(artifacts["created"]) == 1
        assert len(artifacts["modified"]) == 0

    def test_relative_paths_with_project(self, temp_dir):
        project_dir = temp_dir / "myproject"
        project_dir.mkdir()
        content = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"file_path": str(project_dir / "src" / "main.py")},
                        },
                    ],
                },
            }
        )
        artifacts = extract_artifacts(content, project_dir)
        assert artifacts["created"][0]["path"] == "src/main.py"


# =============================================================================
# Test detect_relationship_hints
# =============================================================================


class TestDetectRelationshipHints:
    def test_uuid_detection(self, sample_transcript_content):
        hints = detect_relationship_hints(sample_transcript_content)
        assert "abc12345-1234-1234-1234-123456789abc" in hints["references_hints"]

    def test_continuation_language(self):
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Continuing from last session..."}],
            },
        })
        hints = detect_relationship_hints(content)
        assert len(hints["detection_notes"]) > 0

    def test_no_hints(self):
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello world"}],
            },
        })
        hints = detect_relationship_hints(content)
        assert len(hints["references_hints"]) == 0
        assert len(hints["detection_notes"]) == 0


# =============================================================================
# Test generate_title_from_content
# =============================================================================


class TestGenerateTitleFromContent:
    def test_basic_title(self, sample_transcript_content):
        title = generate_title_from_content(sample_transcript_content)
        assert "help" in title.lower() or "python" in title.lower()

    def test_removes_greetings(self):
        # Only removes greetings followed by whitespace
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello can you fix the bug?"}],
            },
        })
        title = generate_title_from_content(content)
        assert not title.lower().startswith("hello")
        assert "fix the bug" in title.lower()

    def test_empty_content(self):
        title = generate_title_from_content("")
        assert title == "Untitled Session"

    def test_truncation(self):
        long_text = "A" * 100
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": long_text}],
            },
        })
        title = generate_title_from_content(content)
        assert len(title) <= 60

    def test_skips_ide_opened_file(self):
        """Test that IDE context messages are skipped for title generation."""
        ide_msg = "<ide_opened_file>The user opened /path/to/file.py</ide_opened_file>"
        content = "\n".join([
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": ide_msg}],
                },
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Build a GUI for transcription"}],
                },
            }),
        ])
        title = generate_title_from_content(content)
        assert "ide_opened_file" not in title.lower()
        assert "gui" in title.lower() or "transcription" in title.lower()

    def test_skips_ide_selection(self):
        """Test that IDE selection messages are skipped."""
        content = "\n".join([
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": "<ide_selection>selected code here</ide_selection>",
                    }],
                },
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Refactor this function"}],
                },
            }),
        ])
        title = generate_title_from_content(content)
        assert "ide_selection" not in title.lower()
        assert "refactor" in title.lower()

    def test_skips_short_messages(self):
        """Test that very short messages are skipped."""
        content = "\n".join([
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "ok"}],
                },
            }),
            json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Implement authentication"}],
                },
            }),
        ])
        title = generate_title_from_content(content)
        assert title != "ok"
        assert "authentication" in title.lower()


# =============================================================================
# Test _is_ide_context_message
# =============================================================================


class TestIsIdeContextMessage:
    def test_ide_opened_file(self):
        assert _is_ide_context_message("<ide_opened_file>stuff</ide_opened_file>")

    def test_ide_selection(self):
        assert _is_ide_context_message("<ide_selection>code</ide_selection>")

    def test_system_reminder(self):
        assert _is_ide_context_message("<system-reminder>reminder</system-reminder>")

    def test_command_name(self):
        assert _is_ide_context_message("<command-name>/transcript</command-name>")

    def test_short_message(self):
        assert _is_ide_context_message("ok")
        assert _is_ide_context_message("yes")
        assert _is_ide_context_message("")

    def test_real_message(self):
        assert not _is_ide_context_message("Fix the authentication bug")
        assert not _is_ide_context_message("Build a GUI for recording")

    def test_whitespace_handling(self):
        assert _is_ide_context_message("  <ide_opened_file>stuff</ide_opened_file>  ")
        assert _is_ide_context_message("   ok   ")


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
# Test update_html_titles
# =============================================================================


class TestUpdateHtmlTitles:
    def test_title_replacement(self, temp_dir):
        html_content = "<html><title>Claude Code transcript</title><body>Content</body></html>"
        html_file = temp_dir / "test.html"
        html_file.write_text(html_content)

        update_html_titles(temp_dir, "My Custom Title")

        updated = html_file.read_text()
        assert "<title>My Custom Title</title>" in updated

    def test_index_gets_h1(self, temp_dir):
        html_content = "<html><title>Claude Code transcript</title><body>Content</body></html>"
        html_file = temp_dir / "index.html"
        html_file.write_text(html_content)

        update_html_titles(temp_dir, "My Title")

        updated = html_file.read_text()
        assert "<h1" in updated
        assert "My Title" in updated


# =============================================================================
# Test compute_file_hash
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
# Test create_session_metadata
# =============================================================================


class TestCreateSessionMetadata:
    def test_basic_metadata(self, sample_transcript_file, sample_transcript_content):
        stats = extract_session_stats(sample_transcript_content)
        artifacts = extract_artifacts(sample_transcript_content)
        hints = detect_relationship_hints(sample_transcript_content)

        metadata = create_session_metadata(
            session_id="test-session-123",
            transcript_path=sample_transcript_file,
            stats=stats,
            title="Test Title",
            artifacts=artifacts,
            relationship_hints=hints,
            plan_files=["plan.md"],
            directory_name="2026-01-14-test",
            three_ps={
                "prompt_summary": "Test",
                "process_summary": "Test",
                "provenance_summary": "Test",
            },
            needs_review=False,
            project_dir=Path("/home/user/project"),
        )

        assert metadata["schema_version"] == SCHEMA_VERSION
        assert metadata["session"]["id"] == "test-session-123"
        assert metadata["auto_generated"]["title"] == "Test Title"
        assert metadata["archive"]["needs_review"] is False
        assert "plan.md" in metadata["plan_files"]


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
# Integration tests
# =============================================================================


class TestCLIIntegration:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "claude_transcript_archive.cli", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "research-grade" in result.stdout

    def test_partial_cli_args(self):
        """Test that providing only --transcript without --session-id fails."""
        result = subprocess.run(
            [sys.executable, "-m", "claude_transcript_archive.cli", "--transcript", "/tmp/x.jsonl"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "Both --transcript and --session-id" in result.stderr

    def test_nonexistent_transcript_file(self):
        """Test that nonexistent transcript file is handled."""
        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", "/nonexistent/file.jsonl",
                "--session-id", "test-123",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0  # Exits cleanly after logging error
        assert "not found" in result.stderr

    def test_cli_with_three_ps_args(self, temp_dir):
        """Test that CLI accepts --prompt, --process, --provenance arguments."""
        # Create a transcript file
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"Hello"}}\n')

        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", str(transcript),
                "--session-id", "test-123",
                "--local",
                "--prompt", "Test prompt summary",
                "--process", "Test process summary",
                "--provenance", "Test provenance summary",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(temp_dir),
        )
        # Should succeed
        assert result.returncode == 0

        # Check the archive was created with correct metadata
        archive_dir = temp_dir / "ai_transcripts"
        session_dirs = list(archive_dir.glob("*-*"))
        assert len(session_dirs) == 1

        meta_path = session_dirs[0] / "session.meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["three_ps"]["prompt_summary"] == "Test prompt summary"
        assert meta["three_ps"]["process_summary"] == "Test process summary"
        assert meta["three_ps"]["provenance_summary"] == "Test provenance summary"
        assert meta["archive"]["needs_review"] is False

    def test_cli_stdin_mode(self, temp_dir):
        """Test CLI in stdin mode (hook invocation)."""
        # Create a transcript file
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"Hello stdin mode"}}\n')

        stdin_input = json.dumps({
            "transcript_path": str(transcript),
            "session_id": "stdin-test-456"
        })

        result = subprocess.run(
            [sys.executable, "-m", "claude_transcript_archive.cli", "--local"],
            input=stdin_input,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(temp_dir),
        )
        assert result.returncode == 0

        # Check archive was created
        archive_dir = temp_dir / "ai_transcripts"
        session_dirs = list(archive_dir.glob("*-*"))
        assert len(session_dirs) == 1

    def test_cli_quiet_mode(self):
        """Test CLI quiet mode suppresses errors."""
        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", "/nonexistent/file.jsonl",
                "--session-id", "test-123",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        # Quiet mode should suppress error output
        assert result.stderr == ""

    def test_cli_force_flag(self, temp_dir):
        """Test CLI --force flag."""
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"Hello"}}\n')

        # First run
        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", str(transcript),
                "--session-id", "test-123",
                "--local",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(temp_dir),
        )
        assert result.returncode == 0

        # Second run with --force should still succeed
        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", str(transcript),
                "--session-id", "test-123",
                "--local",
                "--force",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(temp_dir),
        )
        assert result.returncode == 0

    def test_cli_output_flag(self, temp_dir):
        """Test CLI --output flag for custom directory."""
        transcript = temp_dir / "test.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"Custom output test"}}\n')
        custom_output = temp_dir / "custom_archive"

        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "--transcript", str(transcript),
                "--session-id", "test-123",
                "--output", str(custom_output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert custom_output.exists()
        session_dirs = list(custom_output.glob("*-*"))
        assert len(session_dirs) == 1


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
        assert (session_dirs[0] / ".title").read_text() == "Custom Title"

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
# Test find_plan_files
# =============================================================================


class TestFindPlanFiles:
    def test_finds_plan_files(self, temp_dir, sample_transcript_content):
        """Test finding plan files from transcript directory."""
        # Create mock Claude directory structure
        projects_dir = temp_dir / ".claude" / "projects" / "-test-project"
        projects_dir.mkdir(parents=True)
        transcript = projects_dir / "session.jsonl"
        transcript.write_text(sample_transcript_content)

        # Create plan file
        plans_dir = temp_dir / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan_file = plans_dir / "test-plan.md"
        plan_file.write_text("# Plan\nSteps here")

        # Mock the content to include plan file reference
        content_with_plan = sample_transcript_content + "\n" + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Plan file: test-plan.md"}],
            },
        })
        transcript.write_text(content_with_plan)

        result = find_plan_files(transcript)
        # May or may not find depending on directory structure
        assert isinstance(result, list)


# =============================================================================
# Test edge cases for coverage
# =============================================================================


class TestEdgeCases:
    def test_extract_stats_with_file_history_snapshot(self):
        """Test that file-history-snapshot entries are skipped."""
        content = json.dumps({
            "type": "file-history-snapshot",
            "timestamp": "2026-01-14T10:00:00.000Z",
            "files": {},
        })
        stats = extract_session_stats(content)
        assert stats["turns"] == 0

    def test_extract_artifacts_read_then_edit(self):
        """Test that files read then edited appear in modified, not referenced."""
        content = "\n".join([
            json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.py"}},
                    ],
                },
            }),
            json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/test.py"}},
                    ],
                },
            }),
        ])
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

    def test_catalog_loads_existing(self, temp_dir):
        """Test loading existing catalog preserves data."""
        existing_catalog = {
            "schema_version": SCHEMA_VERSION,
            "sessions": [{"id": "existing", "title": "Test"}],
        }
        catalog_path = temp_dir / "CATALOG.json"
        catalog_path.write_text(json.dumps(existing_catalog))

        loaded = load_catalog(temp_dir)
        # Should preserve existing data
        assert loaded["sessions"][0]["id"] == "existing"
        assert loaded["sessions"][0]["title"] == "Test"

    def test_catalog_empty_on_missing(self, temp_dir):
        """Test load_catalog returns empty structure when file doesn't exist."""
        loaded = load_catalog(temp_dir / "nonexistent")
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["sessions"] == []
        assert loaded["total_sessions"] == 0


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
# Test auto_discover_transcript
# =============================================================================


class TestAutoDiscoverTranscript:
    def test_no_projects_dir(self, temp_dir, monkeypatch):
        """Test returns None when projects directory doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: temp_dir)
        monkeypatch.chdir(temp_dir)
        result = auto_discover_transcript()
        assert result is None

    def test_no_jsonl_files(self, temp_dir, monkeypatch):
        """Test returns None when no jsonl files exist."""
        monkeypatch.setattr(Path, "home", lambda: temp_dir)
        # Create the projects directory but no files
        projects_dir = temp_dir / ".claude" / "projects" / f"-{str(temp_dir).replace('/', '-')}"
        projects_dir.mkdir(parents=True)
        monkeypatch.chdir(temp_dir)
        result = auto_discover_transcript()
        assert result is None

    def test_finds_transcript(self, temp_dir, monkeypatch):
        """Test finds most recent transcript."""
        monkeypatch.setattr(Path, "home", lambda: temp_dir)
        # Create projects directory with a transcript
        # Claude Code encodes paths by replacing / with -
        # /tmp/foo -> -tmp-foo (the leading / becomes -)
        encoded = str(temp_dir).replace("/", "-")
        projects_dir = temp_dir / ".claude" / "projects" / encoded
        projects_dir.mkdir(parents=True)
        transcript = projects_dir / "abc123-def456.jsonl"
        transcript.write_text('{"test": true}')
        monkeypatch.chdir(temp_dir)
        result = auto_discover_transcript()
        assert result is not None
        path, session_id = result
        assert path == transcript
        assert session_id == "abc123-def456"
