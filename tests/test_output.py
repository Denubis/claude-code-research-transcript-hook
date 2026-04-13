"""Tests for claude_transcript_archive.output module."""

import importlib
import json

from claude_transcript_archive.output import (
    extract_conversation_messages,
    format_tool_summary,
    generate_conversation_html_for_pdf,
    generate_conversation_markdown,
    sanitize_for_pdf,
    update_html_titles,
)

# =============================================================================
# AC verification tests for module decomposition
# =============================================================================


class TestOutputModuleDecomposition:
    def test_ac1_1_independent_import(self):
        output = importlib.import_module("claude_transcript_archive.output")
        assert callable(output.generate_conversation_markdown)

    def test_ac1_3_no_reexport_from_cli(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "generate_conversation_markdown")

    def test_ac1_3_no_reexport_constants(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "PDF_PREAMBLE")
        assert not hasattr(cli, "SPEAKER_LUA_FILTER")

    def test_ac1_3_no_reexport_functions(self):
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "format_tool_summary")
        assert not hasattr(cli, "extract_conversation_messages")
        assert not hasattr(cli, "sanitize_for_pdf")
        assert not hasattr(cli, "generate_conversation_html_for_pdf")
        assert not hasattr(cli, "generate_conversation_pdf")
        assert not hasattr(cli, "update_html_titles")
        assert not hasattr(cli, "_format_file_path")


# =============================================================================
# Test update_html_titles (moved from test_cli.py)
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
# Test format_tool_summary
# =============================================================================


class TestFormatToolSummary:
    def test_read_tool(self):
        result = format_tool_summary("Read", {"file_path": "/home/user/project/src/cli.py"})
        assert result == "Read: src/cli.py"

    def test_write_tool(self):
        result = format_tool_summary("Write", {"file_path": "/home/user/output.txt"})
        assert result == "Write: output.txt"

    def test_edit_tool(self):
        result = format_tool_summary("Edit", {"file_path": "/home/user/main.py"})
        assert result == "Edit: main.py"

    def test_bash_tool(self):
        result = format_tool_summary("Bash", {"command": "git status"})
        assert result == "Bash: `git status`"

    def test_bash_tool_truncation(self):
        long_cmd = "a" * 100
        result = format_tool_summary("Bash", {"command": long_cmd})
        assert len(result) < 70
        assert result.endswith("...`")

    def test_grep_tool(self):
        result = format_tool_summary("Grep", {"pattern": "TODO", "path": "src/"})
        assert result == "Grep: 'TODO' in src/"

    def test_unknown_tool(self):
        result = format_tool_summary("CustomTool", {"arg": "val"})
        assert result == "CustomTool"


# =============================================================================
# Test extract_conversation_messages
# =============================================================================


class TestExtractConversationMessages:
    def test_basic_extraction(self, sample_transcript_content):
        messages = extract_conversation_messages(sample_transcript_content)
        assert len(messages) >= 1
        assert messages[0]["role"] == "user"

    def test_skips_system_reminders(self):
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "<system-reminder>config</system-reminder>"}],
            },
        })
        messages = extract_conversation_messages(content)
        assert len(messages) == 0

    def test_skips_ide_messages(self):
        content = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<ide_opened_file>something</ide_opened_file>"},
                ],
            },
        })
        messages = extract_conversation_messages(content)
        assert len(messages) == 0

    def test_extracts_tool_calls(self):
        content = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check that file."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.py"}},
                ],
            },
        })
        messages = extract_conversation_messages(content)
        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert len(messages[0]["tool_calls"]) == 1
        assert messages[0]["tool_calls"][0]["name"] == "Read"

    def test_empty_content(self):
        messages = extract_conversation_messages("")
        assert messages == []


# =============================================================================
# Test sanitize_for_pdf
# =============================================================================


class TestSanitizeForPdf:
    def test_preserves_normal_text(self):
        assert sanitize_for_pdf("Hello world") == "Hello world"

    def test_preserves_newlines(self):
        assert sanitize_for_pdf("line1\nline2") == "line1\nline2"

    def test_removes_control_chars(self):
        result = sanitize_for_pdf("hello\x00world")
        assert result == "helloworld"

    def test_empty_string(self):
        assert sanitize_for_pdf("") == ""

    def test_none_passthrough(self):
        assert sanitize_for_pdf("") == ""


# =============================================================================
# Test generate_conversation_markdown
# =============================================================================


class TestGenerateConversationMarkdown:
    def test_basic_generation(self):
        messages = [
            {"role": "user", "text": "Hello", "tool_calls": []},
            {"role": "assistant", "text": "Hi there!", "tool_calls": []},
        ]
        result = generate_conversation_markdown(messages, "Test Title")
        assert "# Test Title" in result
        assert "## User" in result
        assert "## Assistant" in result
        assert "Hello" in result
        assert "Hi there!" in result

    def test_with_metadata(self):
        messages = [{"role": "user", "text": "Hi", "tool_calls": []}]
        metadata = {
            "session": {"started_at": "2026-01-14T10:00:00Z", "duration_minutes": 30},
            "model": {"model_id": "claude-sonnet-4", "claude_code_version": "1.0"},
            "statistics": {"turns": 5, "estimated_cost_usd": 0.50},
            "three_ps": {
                "prompt_summary": "Test prompt",
                "process_summary": "Test process",
                "provenance_summary": "Test provenance",
            },
        }
        result = generate_conversation_markdown(messages, "Test", metadata=metadata)
        assert "**Date**" in result
        assert "**Model**" in result
        assert "Three Ps" in result
        assert "Test prompt" in result

    def test_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "text": "Checking...",
                "tool_calls": [{"name": "Read", "summary": "Read: test.py"}],
            },
        ]
        result = generate_conversation_markdown(messages, "Title")
        assert "**Tools used:**" in result
        assert "- Read: test.py" in result


# =============================================================================
# Test generate_conversation_html_for_pdf
# =============================================================================


class TestGenerateConversationHtmlForPdf:
    def test_basic_html_generation(self):
        messages = [
            {"role": "user", "text": "Hello", "tool_calls": []},
            {"role": "assistant", "text": "Hi!", "tool_calls": []},
        ]
        result = generate_conversation_html_for_pdf(messages, "Test Title")
        assert "<!DOCTYPE html>" in result
        assert "<title>" in result
        assert 'data-speaker="user"' in result
        assert 'data-speaker="assistant"' in result

    def test_escapes_html(self):
        messages = [
            {"role": "user", "text": "<script>alert('xss')</script>", "tool_calls": []},
        ]
        result = generate_conversation_html_for_pdf(messages, "Title")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
