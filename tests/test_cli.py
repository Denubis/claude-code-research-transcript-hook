"""Integration tests for claude_transcript_archive CLI entry point."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

from claude_transcript_archive import archive, catalog, discovery, metadata, output

# =============================================================================
# AC1 Module Decomposition Verification
# =============================================================================


class TestAC1ModuleDecomposition:
    """Verify AC1: Package decomposes cleanly into 5 modules."""

    def test_ac1_1_all_modules_import_independently(self):
        """AC1.1: Each module imports without circular dependencies."""
        assert discovery is not None
        assert metadata is not None
        assert output is not None
        assert catalog is not None
        assert archive is not None

    def test_ac1_2_test_count_exceeds_v1(self, request):
        """AC1.2: Total tests >= 93 (v1 count)."""
        session = request.session
        assert session.testscollected >= 93, (
            f"Expected >= 93 tests (v1 baseline), got {session.testscollected}"
        )

    def test_ac1_3_moved_functions_not_in_cli(self):
        """AC1.3: Functions moved to submodules are not re-exported from cli."""
        cli = importlib.import_module("claude_transcript_archive.cli")
        # Representative function from each extracted module:
        assert not hasattr(cli, "get_cc_project_path")  # discovery
        assert not hasattr(cli, "extract_session_stats")  # metadata
        assert not hasattr(cli, "generate_conversation_markdown")  # output
        assert not hasattr(cli, "load_catalog")  # catalog
        assert not hasattr(cli, "generate_title_from_content")  # archive


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
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "archive", "--transcript", "/tmp/x.jsonl",
            ],
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
                "archive", "--transcript", "/nonexistent/file.jsonl",
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
                "archive", "--transcript", str(transcript),
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
            [sys.executable, "-m", "claude_transcript_archive.cli", "archive", "--local"],
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
                "archive", "--transcript", "/nonexistent/file.jsonl",
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
                "archive", "--transcript", str(transcript),
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
                "archive", "--transcript", str(transcript),
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
                "archive", "--transcript", str(transcript),
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


class TestCLIStitch:
    """Integration tests for the manual `stitch` verb (Phase 5)."""

    def _archive_singleton(self, temp_dir, session_id: str, marker: str) -> Path:
        """Archive a single session via the CLI in --local mode, return its dir."""
        transcript = temp_dir / f"{session_id}.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-04-24T10:00:00Z",
                    "message": {"role": "user", "content": f"prompt: {marker}"},
                }
            )
            + "\n"
        )
        subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "archive", "--transcript", str(transcript),
                "--session-id", session_id, "--local", "--quiet",
            ],
            capture_output=True, text=True, check=True, cwd=str(temp_dir),
        )
        archive_dir = temp_dir / "ai_transcripts"
        manifest = json.loads(
            (archive_dir / ".session_manifest.json").read_text(encoding="utf-8")
        )
        return Path(manifest[session_id])

    def test_stitch_help(self):
        """`stitch --help` runs and documents the verb."""
        result = subprocess.run(
            [sys.executable, "-m", "claude_transcript_archive.cli", "stitch", "--help"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert "--into" in result.stdout

    def test_stitch_folds_singleton_into_singleton(self, temp_dir):
        """End-to-end: archive two singletons (no shared customTitle so
        auto-stitch does NOT fire), then stitch second into first via CLI.
        Result: one cluster, both UUIDs as constituents, old second dir gone."""
        dir_a = self._archive_singleton(temp_dir, "uuid-aaa", "alpha")
        dir_b = self._archive_singleton(temp_dir, "uuid-bbb", "beta")
        assert dir_a.exists() and dir_b.exists()

        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "stitch", "--into", "uuid-aaa", "uuid-bbb", "--local",
            ],
            capture_output=True, text=True, check=False, cwd=str(temp_dir),
        )
        assert result.returncode == 0, (
            f"stitch failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        archive_dir = temp_dir / "ai_transcripts"
        manifest = json.loads(
            (archive_dir / ".session_manifest.json").read_text(encoding="utf-8")
        )
        cluster_dir = Path(manifest["uuid-aaa"])
        assert cluster_dir.name.endswith("-stitched")
        assert manifest["uuid-bbb"] == str(cluster_dir)
        # uuid-bbb's old singleton dir is gone — content folded into the cluster.
        assert not dir_b.exists()

    def test_stitch_missing_target_exits_nonzero(self, temp_dir):
        """AC5.3: target_uuid not in manifest → exit 1 with named error."""
        # Seed a known UUID so archive_dir exists.
        self._archive_singleton(temp_dir, "uuid-known", "x")

        result = subprocess.run(
            [
                sys.executable, "-m", "claude_transcript_archive.cli",
                "stitch", "--into", "no-such-uuid", "uuid-known", "--local",
            ],
            capture_output=True, text=True, check=False, cwd=str(temp_dir),
        )
        assert result.returncode != 0
        assert "no-such-uuid" in result.stderr
        assert "no archive" in result.stderr.lower()
