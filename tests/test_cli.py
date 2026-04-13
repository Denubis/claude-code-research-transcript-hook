"""Integration tests for claude_transcript_archive CLI entry point."""

import json
import subprocess
import sys

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
