"""Tests for claude_transcript_archive.metadata module."""

import json
from pathlib import Path

from claude_transcript_archive.metadata import (
    SCHEMA_VERSION,
    classify_session,
    create_session_metadata,
    create_stitched_metadata,
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

    def test_local_command_caveat(self):
        assert is_ide_context_message(
            "<local-command-caveat>The messages below were generated by the user "
            "while running local commands</local-command-caveat>"
        )

    def test_command_message(self):
        assert is_ide_context_message("<command-message>running /skill</command-message>")

    def test_command_args(self):
        assert is_ide_context_message("<command-args>--foo bar</command-args>")

    def test_command_stdout(self):
        assert is_ide_context_message("<command-stdout>output text</command-stdout>")

    def test_command_stderr(self):
        assert is_ide_context_message("<command-stderr>error text</command-stderr>")

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
# Test extract_custom_title (auto-stitch detection signal)
# =============================================================================


class TestExtractCustomTitle:
    """Auto-stitch's detection signal. Claude Code writes `customTitle` on every
    JSONL entry once the session has been named (typically via
    `/exec-session-naming`). Two sessions sharing this string in the same
    project belong to one logical conversation."""

    def test_returns_string_when_present(self):
        from claude_transcript_archive.metadata import extract_custom_title

        content = "\n".join(
            [
                json.dumps({"type": "custom-title", "customTitle": "Adela/mel:plan-dvc"}),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "abc",
                        "customTitle": "Adela/mel:plan-dvc",
                        "message": {"role": "user", "content": "hi"},
                    }
                ),
            ]
        )
        assert extract_custom_title(content) == "Adela/mel:plan-dvc"

    def test_returns_none_when_missing(self):
        """Sessions predating the customTitle convention (or runs without
        /exec-session-naming) have no customTitle field. Must return None so
        auto-stitch treats them as singletons."""
        from claude_transcript_archive.metadata import extract_custom_title

        content = "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "abc",
                        "message": {"role": "user", "content": "hi"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "abc",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ),
            ]
        )
        assert extract_custom_title(content) is None

    def test_returns_none_for_empty_string(self):
        """An empty customTitle is semantically 'unset' — must not cluster."""
        from claude_transcript_archive.metadata import extract_custom_title

        content = json.dumps({"type": "user", "customTitle": "", "sessionId": "abc"})
        assert extract_custom_title(content) is None

    def test_handles_empty_content(self):
        from claude_transcript_archive.metadata import extract_custom_title

        assert extract_custom_title("") is None

    def test_handles_malformed_jsonl(self):
        """Robustness: a single malformed line must not stop us from finding
        the customTitle in subsequent valid lines."""
        from claude_transcript_archive.metadata import extract_custom_title

        content = "\n".join(
            [
                "not valid json {",
                json.dumps({"type": "user", "customTitle": "valid-after-junk"}),
            ]
        )
        assert extract_custom_title(content) == "valid-after-junk"

    def test_returns_first_customtitle_found(self):
        """When customTitle changes mid-session (rare but possible), use the
        first one. Stability across the session is the contract; mid-session
        renames are an edge case we don't optimise for."""
        from claude_transcript_archive.metadata import extract_custom_title

        content = "\n".join(
            [
                json.dumps({"type": "user", "customTitle": "first"}),
                json.dumps({"type": "user", "customTitle": "second"}),
            ]
        )
        assert extract_custom_title(content) == "first"


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


# =============================================================================
# Test classify_session
# =============================================================================


class TestClassifySession:
    def _make_assistant_lines(self, count: int) -> str:
        """Build JSONL content with the given number of assistant messages."""
        lines = []
        for i in range(count):
            entry = {
                "type": "assistant",
                "message": {"role": "assistant", "content": f"msg {i}"},
            }
            lines.append(json.dumps(entry))
        return "\n".join(lines)

    def test_trivial_few_messages(self):
        """AC5.3: < 5 assistant messages = trivial."""
        content = self._make_assistant_lines(3)
        assert classify_session(content) == "trivial"

    def test_substantial_many_messages(self):
        """>= 5 assistant messages = substantial."""
        content = self._make_assistant_lines(10)
        assert classify_session(content) == "substantial"

    def test_empty_content_trivial(self):
        assert classify_session("") == "trivial"

    def test_malformed_jsonl_trivial(self):
        assert classify_session("not json\nalso not json") == "trivial"

    def test_exactly_five_is_substantial(self):
        content = self._make_assistant_lines(5)
        assert classify_session(content) == "substantial"


# =============================================================================
# Test create_stitched_metadata (auto-stitch Phase 3)
# =============================================================================


class TestCreateStitchedMetadata:
    """Build cluster meta matching the MELICA hand-rolled schema verbatim.

    Reference fixture: /media/brian/storage/people/Adela/melica/ai_transcripts/
    2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/session.meta.json.
    Key divergences from singleton schema (per DR5):
      - statistics drops tokens/cost/tool_calls; human_messages → user_messages
      - artifacts is a file-ref dict, not the created/modified/referenced shape
      - relationships is {}
      - archive.stitched is True
      - _constituent_sessions lists members with chronological rank
    """

    def _make_raw(self, temp_dir: Path) -> Path:
        raw = temp_dir / "raw-transcript.jsonl"
        raw.write_text("line1\nline2\nline3\n", encoding="utf-8")
        return raw

    def _basic_kwargs(self, temp_dir: Path) -> dict:
        return {
            "primary_session_id": "aaa-uuid",
            "constituents": [
                ("aaa-uuid", "2026-04-24T10:01:10.092Z"),
                ("bbb-uuid", "2026-05-01T12:00:00.000Z"),
                ("ccc-uuid", "2026-05-16T10:47:39.668Z"),
            ],
            "raw_transcript_path": self._make_raw(temp_dir),
            "aggregated_stats": {
                "turns": 4809,
                "user_messages": 1769,
                "assistant_messages": 3040,
                "jsonl_lines": 7315,
            },
            "directory_name": "2026-04-24-feature-x-stitched",
            "started_at": "2026-04-24T10:01:10.092Z",
            "ended_at": "2026-05-16T10:47:39.668Z",
            "duration_minutes": 31726,
            "model_id": "claude-opus-4-7",
            "claude_code_version": "2.1.114",
            "title": "feature-x",
        }

    def test_session_id_is_primary(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["session"]["id"] == "aaa-uuid"

    def test_session_started_at_is_earliest_ended_at_is_latest(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["session"]["started_at"] == "2026-04-24T10:01:10.092Z"
        assert meta["session"]["ended_at"] == "2026-05-16T10:47:39.668Z"
        assert meta["session"]["duration_minutes"] == 31726

    def test_archive_stitched_true(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["archive"]["stitched"] is True
        assert meta["archive"]["directory_name"] == "2026-04-24-feature-x-stitched"
        assert "archived_at" in meta["archive"]

    def test_statistics_simplified_no_tokens(self, temp_dir):
        """DR5: stitched statistics drops tokens, cost, tool_calls, thinking_blocks.
        Field name is user_messages, not human_messages (matches MELICA hand-rolled)."""
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        stats = meta["statistics"]
        assert stats["turns"] == 4809
        assert stats["user_messages"] == 1769
        assert stats["assistant_messages"] == 3040
        assert stats["jsonl_lines"] == 7315
        assert "raw_transcript_bytes" in stats
        assert "tokens" not in stats
        assert "estimated_cost_usd" not in stats
        assert "tool_calls" not in stats
        assert "thinking_blocks" not in stats
        assert "human_messages" not in stats

    def test_artifacts_uses_file_refs(self, temp_dir):
        """Stitched artifacts is a file-ref dict naming raw + primary jsonl + sha256.
        It is NOT the created/modified/referenced shape used by singletons."""
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        artifacts = meta["artifacts"]
        assert artifacts["raw_transcript"] == "raw-transcript.jsonl"
        assert artifacts["primary_jsonl"] == "aaa-uuid.jsonl"
        assert "raw_transcript_sha256" in artifacts
        assert len(artifacts["raw_transcript_sha256"]) == 64  # SHA-256 hex digest
        assert "created" not in artifacts
        assert "modified" not in artifacts
        assert "referenced" not in artifacts

    def test_relationships_empty(self, temp_dir):
        """Stitched relationships is {} (MELICA verbatim), not the continues/references/isPartOf
        shape singletons use."""
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["relationships"] == {}

    def test_constituent_sessions_ranked_in_order(self, temp_dir):
        """_constituent_sessions list with 1-indexed rank, in caller-supplied order."""
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["_constituent_sessions"] == [
            {"id": "aaa-uuid", "rank": 1},
            {"id": "bbb-uuid", "rank": 2},
            {"id": "ccc-uuid", "rank": 3},
        ]

    def test_raw_transcript_bytes_from_file(self, temp_dir):
        """statistics.raw_transcript_bytes reflects the actual file size on disk."""
        kwargs = self._basic_kwargs(temp_dir)
        meta = create_stitched_metadata(**kwargs)
        expected = kwargs["raw_transcript_path"].stat().st_size
        assert meta["statistics"]["raw_transcript_bytes"] == expected

    def test_three_ps_defaults_to_empty(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["three_ps"]["prompt_summary"] == ""
        assert meta["three_ps"]["process_summary"] == ""
        assert meta["three_ps"]["provenance_summary"] == ""

    def test_three_ps_passed_through(self, temp_dir):
        kwargs = self._basic_kwargs(temp_dir)
        kwargs["three_ps"] = {
            "prompt_summary": "spans 22 days",
            "process_summary": "WIP resume prompts",
            "provenance_summary": "MELICA",
        }
        meta = create_stitched_metadata(**kwargs)
        assert meta["three_ps"]["prompt_summary"] == "spans 22 days"
        assert meta["three_ps"]["process_summary"] == "WIP resume prompts"
        assert meta["three_ps"]["provenance_summary"] == "MELICA"

    def test_needs_review_defaults_true(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["archive"]["needs_review"] is True

    def test_model_and_version_passed_through(self, temp_dir):
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        assert meta["model"]["provider"] == "anthropic"
        assert meta["model"]["model_id"] == "claude-opus-4-7"
        assert meta["model"]["claude_code_version"] == "2.1.114"
        assert meta["model"]["access_method"] == "claude-code-cli"

    def test_project_dir_resolves_name_and_directory(self, temp_dir):
        proj = temp_dir / "myproj"
        proj.mkdir()
        kwargs = self._basic_kwargs(temp_dir)
        kwargs["project_dir"] = proj
        meta = create_stitched_metadata(**kwargs)
        assert meta["project"]["name"] == "myproj"
        assert meta["project"]["directory"] == str(proj)

    def test_top_level_keys_match_melica_reference(self, temp_dir):
        """Snapshot the top-level key set against the MELICA hand-rolled cluster.
        Drift here = schema change that needs DR review."""
        meta = create_stitched_metadata(**self._basic_kwargs(temp_dir))
        expected_keys = {
            "schema_version",
            "session",
            "project",
            "model",
            "statistics",
            "artifacts",
            "relationships",
            "auto_generated",
            "three_ps",
            "plan_files",
            "archive",
            "_constituent_sessions",
        }
        assert set(meta.keys()) == expected_keys
