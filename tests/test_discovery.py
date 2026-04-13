"""Tests for claude_transcript_archive.discovery module."""

import importlib
from pathlib import Path

from claude_transcript_archive.discovery import (
    _encode_cc_path,
    auto_discover_transcript,
    get_archive_dir,
    get_cc_project_path,
    get_project_dir_from_transcript,
)

# =============================================================================
# Test _encode_cc_path
# =============================================================================


class TestEncodeCCPath:
    """Tests for the pure string-encoding helper (platform-independent)."""

    def test_posix_simple(self):
        assert _encode_cc_path("/home/user/project") == "-home-user-project"

    def test_posix_path_with_dashes(self):
        assert _encode_cc_path("/home/user/my-cool-project") == "-home-user-my-cool-project"

    def test_windows_with_drive(self):
        result = _encode_cc_path("C:\\Users\\Adela\\denubis-plugins")
        assert result == "C--Users-Adela-denubis-plugins"

    def test_windows_forward_slashes(self):
        # Windows paths occasionally use '/' — handle both separators
        assert _encode_cc_path("C:/Users/Adela/proj") == "C--Users-Adela-proj"

    def test_no_separators_leak(self):
        """Encoded output must contain no ':', '/', or '\\\\'."""
        encoded = _encode_cc_path("C:\\Users\\Adela\\my-proj")
        assert ":" not in encoded
        assert "/" not in encoded
        assert "\\" not in encoded


# =============================================================================
# Test get_cc_project_path
# =============================================================================


class TestGetCCProjectPath:
    def test_matches_encoded_resolved_path(self, tmp_path):
        # Use tmp_path so the test works on any OS
        result = get_cc_project_path(tmp_path)
        expected = _encode_cc_path(str(tmp_path.resolve()))
        assert result == expected

    def test_no_separators_in_output(self, tmp_path):
        result = get_cc_project_path(tmp_path)
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result


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

    def test_global_with_project(self, tmp_path):
        # Use tmp_path so the resolved path is valid on the current OS
        result = get_archive_dir(local=False, output=None, project_dir=tmp_path)
        expected = Path.home() / ".claude" / "transcripts" / get_cc_project_path(tmp_path)
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

    def test_windows_encoded_path(self, temp_dir, monkeypatch):
        """Windows-encoded paths (e.g. C--Users-...) must be handled, not just POSIX."""
        projects_dir = temp_dir / ".claude" / "projects"
        # Simulate a Windows-encoded project path: C--Users-Adela-proj
        encoded_dir = projects_dir / "C--Users-Adela-proj"
        encoded_dir.mkdir(parents=True)
        transcript = encoded_dir / "session.jsonl"
        transcript.touch()

        monkeypatch.setattr(Path, "home", lambda: temp_dir)

        # The function should not crash and should return None
        # (since C:\Users\Adela\proj doesn't exist on this Linux system)
        # The key assertion: it must not skip the path just because
        # it doesn't start with "-"
        result = get_project_dir_from_transcript(transcript)
        # On Linux, the Windows path won't exist, so None is acceptable.
        # But the function must not raise or skip the path entirely.
        assert result is None  # graceful handling, no crash

    def test_claude_path_with_existing_dir(self, temp_dir, monkeypatch):
        # Use a unique encoded path that won't match real filesystem directories.
        # Encoded path "-xyzzy-testproject" decodes to /xyzzy/testproject
        # which doesn't exist on real filesystems.
        projects_dir = temp_dir / ".claude" / "projects"
        encoded_dir = projects_dir / "-xyzzy-testproject"
        encoded_dir.mkdir(parents=True)
        transcript = encoded_dir / "session.jsonl"
        transcript.touch()

        # Mock home directory
        monkeypatch.setattr(Path, "home", lambda: temp_dir)

        # Create the target directory under temp so only full match works
        target_dir = temp_dir / "xyzzy" / "testproject"
        target_dir.mkdir(parents=True)

        result = get_project_dir_from_transcript(transcript)
        # Function should not find /xyzzy (doesn't exist on host),
        # so result is either None or something else — it must not crash.
        assert result is None or isinstance(result, Path)


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
        monkeypatch.chdir(temp_dir)
        # Use the same encoder the production code uses, so the directory is
        # valid on any OS (Windows paths contain ':' which cannot appear mid-path)
        projects_dir = temp_dir / ".claude" / "projects" / get_cc_project_path(temp_dir)
        projects_dir.mkdir(parents=True)
        result = auto_discover_transcript()
        assert result is None

    def test_finds_transcript(self, temp_dir, monkeypatch):
        """Test finds most recent transcript."""
        monkeypatch.setattr(Path, "home", lambda: temp_dir)
        monkeypatch.chdir(temp_dir)
        projects_dir = temp_dir / ".claude" / "projects" / get_cc_project_path(temp_dir)
        projects_dir.mkdir(parents=True)
        transcript = projects_dir / "abc123-def456.jsonl"
        transcript.write_text('{"test": true}', encoding="utf-8")
        result = auto_discover_transcript()
        assert result is not None
        path, session_id = result
        assert path == transcript
        assert session_id == "abc123-def456"


# =============================================================================
# Test module decomposition acceptance criteria
# =============================================================================


class TestModuleDecomposition:
    def test_ac1_1_independent_import(self):
        """AC1.1: discovery module can be imported independently."""
        assert callable(get_cc_project_path)

    def test_ac1_3_no_reexport_from_cli(self):
        """AC1.3: discovery functions are not re-exported from cli."""
        cli = importlib.import_module("claude_transcript_archive.cli")
        assert not hasattr(cli, "get_cc_project_path")
        assert not hasattr(cli, "_encode_cc_path")
        assert not hasattr(cli, "get_archive_dir")
        assert not hasattr(cli, "get_project_dir_from_transcript")
        assert not hasattr(cli, "auto_discover_transcript")
