"""Tests for the init command."""

import subprocess
import sys
from pathlib import Path


def _git_init(temp_dir: Path) -> None:
    """Initialize a git repo with user config and initial commit."""
    subprocess.run(["git", "init"], cwd=temp_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=temp_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=temp_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=temp_dir, check=True, capture_output=True,
    )


def _run_init(temp_dir: Path) -> subprocess.CompletedProcess:
    """Run the init command in the given directory."""
    return subprocess.run(
        [sys.executable, "-m", "claude_transcript_archive.cli", "init", "--non-interactive"],
        cwd=temp_dir, capture_output=True, text=True, check=False,
    )


class TestInitBranchAndWorktree:
    def test_creates_orphan_branch(self, temp_dir):
        """AC3.1: init creates orphan transcripts branch."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        # Verify transcripts branch exists
        branches = subprocess.run(
            ["git", "branch", "--list", "transcripts"],
            cwd=temp_dir, capture_output=True, text=True, check=True,
        )
        assert "transcripts" in branches.stdout

        # Verify no common ancestor with default branch
        default_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=temp_dir, capture_output=True, text=True, check=True,
        )
        merge_base = subprocess.run(
            ["git", "merge-base", default_branch.stdout.strip(), "transcripts"],
            cwd=temp_dir, capture_output=True, text=True, check=False,
        )
        assert merge_base.returncode != 0  # No common ancestor

    def test_mounts_worktree(self, temp_dir):
        """AC3.2: After init, .ai-transcripts/ is mounted."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        assert (temp_dir / ".ai-transcripts").is_dir()

        # Verify in git worktree list
        wt_list = subprocess.run(
            ["git", "worktree", "list"],
            cwd=temp_dir, capture_output=True, text=True, check=True,
        )
        assert ".ai-transcripts" in wt_list.stdout

    def test_updates_gitignore(self, temp_dir):
        """AC3.2: .ai-transcripts/ appears in .gitignore."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        gitignore = (temp_dir / ".gitignore").read_text()
        assert ".ai-transcripts/" in gitignore

    def test_idempotent(self, temp_dir):
        """AC3.4: Running init twice is a no-op."""
        _git_init(temp_dir)

        # Run init twice
        for i in range(2):
            result = _run_init(temp_dir)
            assert result.returncode == 0, f"init failed on run {i + 1}: {result.stderr}"

        # Verify only one .ai-transcripts/ entry in .gitignore
        gitignore = (temp_dir / ".gitignore").read_text()
        assert gitignore.count(".ai-transcripts/") == 1

    def test_non_git_directory_errors(self, temp_dir):
        """AC4.3: Clear error for non-git directory."""
        result = _run_init(temp_dir)
        assert result.returncode != 0
