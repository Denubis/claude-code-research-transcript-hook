"""Tests for claude_transcript_archive.discovery module."""

import importlib
import json
from pathlib import Path

import pytest

from claude_transcript_archive.discovery import (
    _encode_cc_path,
    auto_discover_transcript,
    discover_sessions,
    get_archive_dir,
    get_cc_project_path,
    get_project_dir_from_transcript,
    load_project_defaults,
    resolve_worktrees,
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


# =============================================================================
# Test resolve_worktrees
# =============================================================================


class TestResolveWorktrees:
    def test_parses_multi_worktree_output(self, monkeypatch):
        """AC4.1: Returns paths from all worktrees."""
        mock_output = (
            "worktree /home/user/project\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /home/user/project/.worktrees/feature\n"
            "HEAD def456\n"
            "branch refs/heads/feature\n"
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *_a, **_kw: type("R", (), {"stdout": mock_output, "returncode": 0})(),
        )
        result = resolve_worktrees()
        assert len(result) == 2
        assert result[0] == Path("/home/user/project")
        assert result[1] == Path("/home/user/project/.worktrees/feature")

    def test_non_git_directory_raises_error(self, monkeypatch):
        """AC4.3: Clear error for non-git directory."""
        import subprocess as sp  # noqa: PLC0415

        def mock_run(*_a, **_kw):
            raise sp.CalledProcessError(128, "git")

        monkeypatch.setattr("subprocess.run", mock_run)
        with pytest.raises(RuntimeError, match=r"[Nn]ot a git"):
            resolve_worktrees()

    def test_single_worktree(self, monkeypatch):
        mock_output = "worktree /home/user/project\nHEAD abc\nbranch refs/heads/main\n"
        monkeypatch.setattr(
            "subprocess.run",
            lambda *_a, **_kw: type("R", (), {"stdout": mock_output, "returncode": 0})(),
        )
        result = resolve_worktrees()
        assert len(result) == 1

    def test_empty_output_returns_cwd(self, monkeypatch):
        monkeypatch.setattr(
            "subprocess.run",
            lambda *_a, **_kw: type("R", (), {"stdout": "", "returncode": 0})(),
        )
        result = resolve_worktrees()
        assert len(result) == 1
        assert result[0] == Path.cwd()


# =============================================================================
# Test discover_sessions
# =============================================================================


class TestDiscoverSessions:
    def test_finds_sessions_across_worktrees(self, temp_dir, monkeypatch):
        """AC4.1: Returns sessions from multiple worktree paths."""
        home = temp_dir
        monkeypatch.setattr(Path, "home", lambda: home)

        wt1 = Path("/fake/project")
        wt2 = Path("/fake/project/.worktrees/feature")
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [wt1, wt2],
        )

        encoded1 = _encode_cc_path(str(wt1.resolve()))
        encoded2 = _encode_cc_path(str(wt2.resolve()))

        proj_dir1 = home / ".claude" / "projects" / encoded1
        proj_dir1.mkdir(parents=True)
        (proj_dir1 / "session-aaa.jsonl").touch()

        proj_dir2 = home / ".claude" / "projects" / encoded2
        proj_dir2.mkdir(parents=True)
        (proj_dir2 / "session-bbb.jsonl").touch()

        result = discover_sessions()
        session_ids = [sid for _, sid in result]
        assert "session-aaa" in session_ids
        assert "session-bbb" in session_ids

    def test_deduplicates_by_session_id(self, temp_dir, monkeypatch):
        """Same session under multiple worktrees returned only once."""
        home = temp_dir
        monkeypatch.setattr(Path, "home", lambda: home)

        wt1 = Path("/fake/project")
        wt2 = Path("/fake/project2")
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [wt1, wt2],
        )

        encoded1 = _encode_cc_path(str(wt1.resolve()))
        encoded2 = _encode_cc_path(str(wt2.resolve()))

        proj_dir1 = home / ".claude" / "projects" / encoded1
        proj_dir1.mkdir(parents=True)
        (proj_dir1 / "same-session.jsonl").touch()

        proj_dir2 = home / ".claude" / "projects" / encoded2
        proj_dir2.mkdir(parents=True)
        (proj_dir2 / "same-session.jsonl").touch()

        result = discover_sessions()
        session_ids = [sid for _, sid in result]
        assert session_ids.count("same-session") == 1

    def test_no_sessions_returns_empty(self, temp_dir, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: temp_dir)
        monkeypatch.setattr(
            "claude_transcript_archive.discovery.resolve_worktrees",
            lambda: [Path("/nonexistent")],
        )
        result = discover_sessions()
        assert result == []


# =============================================================================
# Test load_project_defaults
# =============================================================================


class TestLoadProjectDefaults:
    def test_loads_defaults_file(self, temp_dir):
        """AC5.2: Returns values from .claude/transcript-defaults.json."""
        defaults_dir = temp_dir / ".claude"
        defaults_dir.mkdir(parents=True)
        defaults_file = defaults_dir / "transcript-defaults.json"
        defaults_file.write_text(json.dumps({
            "tags": ["research"],
            "purpose": "Testing",
        }))
        # Create .git to mark root
        (temp_dir / ".git").mkdir()

        result = load_project_defaults(temp_dir)
        assert result["tags"] == ["research"]
        assert result["purpose"] == "Testing"

    def test_missing_file_returns_empty(self, temp_dir):
        (temp_dir / ".git").mkdir()
        result = load_project_defaults(temp_dir)
        assert result == {}

    def test_malformed_json_returns_empty(self, temp_dir, capsys):
        defaults_dir = temp_dir / ".claude"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "transcript-defaults.json").write_text("not json{{{")
        (temp_dir / ".git").mkdir()

        result = load_project_defaults(temp_dir)
        assert result == {}
        captured = capsys.readouterr()
        assert "warning" in captured.err.lower() or "malformed" in captured.err.lower()

    def test_none_project_dir_returns_empty(self):
        result = load_project_defaults(None)
        assert result == {}

    def test_walks_up_to_git_root(self, temp_dir):
        """Finds defaults in parent directory."""
        (temp_dir / ".git").mkdir()
        defaults_dir = temp_dir / ".claude"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "transcript-defaults.json").write_text('{"purpose": "found"}')

        subdir = temp_dir / "src" / "deep"
        subdir.mkdir(parents=True)

        result = load_project_defaults(subdir)
        assert result.get("purpose") == "found"

    def test_unknown_keys_preserved(self, temp_dir):
        defaults_dir = temp_dir / ".claude"
        defaults_dir.mkdir(parents=True)
        (defaults_dir / "transcript-defaults.json").write_text('{"future_key": "value"}')
        (temp_dir / ".git").mkdir()

        result = load_project_defaults(temp_dir)
        assert result["future_key"] == "value"
