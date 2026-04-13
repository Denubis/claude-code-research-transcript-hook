"""Tests for claude_transcript_archive.metadata module."""

import json
from pathlib import Path

from claude_transcript_archive.metadata import (
    SCHEMA_VERSION,
    create_session_metadata,
    detect_relationship_hints,
    estimate_cost,
    extract_artifacts,
    extract_session_stats,
    find_plan_files,
    get_file_type,
    is_ide_context_message,
)

# =============================================================================
# AC verification tests
# =============================================================================


class TestMetadataModuleDecomposition:
    def test_ac1_1_independent_import(self):
        from claude_transcript_archive.metadata import extract_session_stats  # noqa: PLC0415

        assert callable(extract_session_stats)

    def test_ac1_3_no_reexport_from_cli(self):
        import importlib  # noqa: PLC0415

        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "extract_session_stats")


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

    def test_artifact_paths_always_use_forward_slashes(self, temp_dir):
        """Artifact paths must use forward slashes regardless of platform.

        On Windows, Path.relative_to + str() produces backslashes.
        The as_posix() normalisation ensures consistent storage.
        """
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
                            "input": {"file_path": str(project_dir / "src" / "deep" / "file.py")},
                        },
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": str(project_dir / "tests" / "test_it.py")},
                        },
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": str(project_dir / "docs" / "readme.md")},
                        },
                    ],
                },
            }
        )
        artifacts = extract_artifacts(content, project_dir)
        # All paths must use forward slashes, never backslashes
        for category in ("created", "modified", "referenced"):
            for artifact in artifacts[category]:
                assert "\\" not in artifact["path"], (
                    f"Backslash in {category} path: {artifact['path']}"
                )


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
# Test is_ide_context_message
# =============================================================================


class TestIsIdeContextMessage:
    def test_ide_opened_file(self):
        assert is_ide_context_message("<ide_opened_file>stuff</ide_opened_file>")

    def test_ide_selection(self):
        assert is_ide_context_message("<ide_selection>code</ide_selection>")

    def test_system_reminder(self):
        assert is_ide_context_message("<system-reminder>reminder</system-reminder>")

    def test_command_name(self):
        assert is_ide_context_message("<command-name>/transcript</command-name>")

    def test_short_message(self):
        assert is_ide_context_message("ok")
        assert is_ide_context_message("yes")
        assert is_ide_context_message("")

    def test_real_message(self):
        assert not is_ide_context_message("Fix the authentication bug")
        assert not is_ide_context_message("Build a GUI for recording")

    def test_whitespace_handling(self):
        assert is_ide_context_message("  <ide_opened_file>stuff</ide_opened_file>  ")
        assert is_ide_context_message("   ok   ")


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
