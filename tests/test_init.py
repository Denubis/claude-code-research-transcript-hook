"""Tests for the init command."""

import json
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


class TestInitHookInstallation:
    def test_creates_settings_with_hook(self, temp_dir):
        """No settings.local.json -> creates file with hook."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        settings_path = temp_dir / ".claude" / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "Stop" in settings["hooks"]
        commands = [h["command"] for h in settings["hooks"]["Stop"]]
        assert "claude-transcript-archive archive --quiet" in commands

    def test_preserves_existing_hooks(self, temp_dir):
        """Existing file with other hooks -> appends our hook."""
        _git_init(temp_dir)
        claude_dir = temp_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        existing = {
            "hooks": {
                "Stop": [{"type": "command", "command": "other-tool --run"}]
            }
        }
        (claude_dir / "settings.local.json").write_text(json.dumps(existing))

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        settings = json.loads((claude_dir / "settings.local.json").read_text())
        commands = [h["command"] for h in settings["hooks"]["Stop"]]
        assert "other-tool --run" in commands
        assert "claude-transcript-archive archive --quiet" in commands

    def test_hook_idempotent(self, temp_dir):
        """Our hook already present -> no change."""
        _git_init(temp_dir)

        # Run init twice
        for _ in range(2):
            result = _run_init(temp_dir)
            assert result.returncode == 0, f"init failed: {result.stderr}"

        settings = json.loads((temp_dir / ".claude" / "settings.local.json").read_text())
        # Should only have our hook once
        commands = [h["command"] for h in settings["hooks"]["Stop"]]
        assert commands.count("claude-transcript-archive archive --quiet") == 1


class TestInitProjectDefaults:
    def test_non_interactive_creates_skeleton(self, temp_dir):
        """Non-interactive mode creates skeleton defaults."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        defaults_path = temp_dir / ".claude" / "transcript-defaults.json"
        assert defaults_path.exists()
        defaults = json.loads(defaults_path.read_text())
        assert defaults["tags"] == []
        assert defaults["purpose"] == ""
        assert defaults["target"] == "branch"
        assert "three_ps_context" in defaults
        assert defaults["three_ps_context"]["prompt_template"] == ""
        assert defaults["three_ps_context"]["process_template"] == ""
        assert defaults["three_ps_context"]["provenance_template"] == ""

    def test_existing_defaults_skipped(self, temp_dir):
        """Existing defaults file is not overwritten."""
        _git_init(temp_dir)
        claude_dir = temp_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        existing = {"tags": ["important"], "purpose": "keep me"}
        (claude_dir / "transcript-defaults.json").write_text(json.dumps(existing))

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        defaults = json.loads((claude_dir / "transcript-defaults.json").read_text())
        assert defaults["tags"] == ["important"]  # Preserved
        assert defaults["purpose"] == "keep me"  # Preserved

    def test_idempotent_defaults(self, temp_dir):
        """Running init twice doesn't overwrite defaults."""
        _git_init(temp_dir)

        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        # Modify defaults
        defaults_path = temp_dir / ".claude" / "transcript-defaults.json"
        modified = json.loads(defaults_path.read_text())
        modified["purpose"] = "modified"
        defaults_path.write_text(json.dumps(modified))

        # Run again
        result = _run_init(temp_dir)
        assert result.returncode == 0, f"init failed: {result.stderr}"

        result_data = json.loads(defaults_path.read_text())
        assert result_data["purpose"] == "modified"  # Not overwritten


class TestInitIntegration:
    def test_full_init_and_idempotency(self, temp_dir):
        """AC3.1 + AC3.2 + AC3.4: Full init produces all artifacts, second run is idempotent."""
        # Setup git repo
        _git_init(temp_dir)

        # First run
        result = _run_init(temp_dir)
        assert result.returncode == 0, f"First init failed: {result.stderr}"

        # Verify all artifacts
        # AC3.1: transcripts branch exists
        branches = subprocess.run(
            ["git", "branch", "--list", "transcripts"],
            cwd=temp_dir, capture_output=True, text=True, check=True,
        )
        assert "transcripts" in branches.stdout

        # AC3.2: worktree mounted
        assert (temp_dir / ".ai-transcripts").is_dir()
        wt_list = subprocess.run(
            ["git", "worktree", "list"],
            cwd=temp_dir, capture_output=True, text=True, check=True,
        )
        assert ".ai-transcripts" in wt_list.stdout

        # AC3.2: .gitignore updated
        gitignore = (temp_dir / ".gitignore").read_text()
        assert ".ai-transcripts/" in gitignore

        # Hook installed
        settings = json.loads((temp_dir / ".claude" / "settings.local.json").read_text())
        assert any(
            h.get("command") == "claude-transcript-archive archive --quiet"
            for h in settings.get("hooks", {}).get("Stop", [])
        )

        # Defaults created
        defaults = json.loads((temp_dir / ".claude" / "transcript-defaults.json").read_text())
        assert "target" in defaults

        # Second run — idempotent
        result2 = _run_init(temp_dir)
        assert result2.returncode == 0, f"Second init failed: {result2.stderr}"

        # Verify no duplicates
        gitignore2 = (temp_dir / ".gitignore").read_text()
        assert gitignore2.count(".ai-transcripts/") == 1

        settings2 = json.loads((temp_dir / ".claude" / "settings.local.json").read_text())
        stop_hooks = settings2.get("hooks", {}).get("Stop", [])
        our_hooks = [
            h for h in stop_hooks
            if h.get("command") == "claude-transcript-archive archive --quiet"
        ]
        assert len(our_hooks) == 1
