# Transcript Archive v2 Implementation Plan — Phase 4: Init Verb

**Goal:** `init` command sets up orphan branch, worktree mount, hooks, and project defaults in a single idempotent operation.

**Architecture:** Init logic lives in `cli.py` as a Typer command. It orchestrates git commands (orphan branch, worktree add), file writes (.gitignore, settings.local.json, transcript-defaults.json), and interactive prompting via Typer.

**Tech Stack:** Python >=3.12, Typer, subprocess (git), json

**Scope:** Phase 4 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC3: Orphan branch storage works
- **transcript-archive-v2.AC3.1 Success:** `init` creates an orphan `transcripts` branch with no common ancestor to `main`
- **transcript-archive-v2.AC3.2 Success:** After `init`, `.ai-transcripts/` is a mounted git worktree on the `transcripts` branch, and `.ai-transcripts/` appears in `.gitignore`
- **transcript-archive-v2.AC3.4 Edge:** `init` run twice is idempotent — no error, no duplicate entries in `.gitignore`, no orphan branch recreation

---

<!-- START_TASK_1 -->
### Task 1: Implement init command — branch and worktree setup

**Verifies:** transcript-archive-v2.AC3.1, transcript-archive-v2.AC3.2

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `init` command

**Implementation:**
Add `@app.command()` function `init_cmd()` to `cli.py`:

Step-by-step init logic:
1. Verify we're in a git repository (`git rev-parse --show-toplevel`). If not, error and exit.
2. Check if `transcripts` branch exists (`git branch --list transcripts`).
   - If not: create orphan branch with `git switch --orphan transcripts`, `git commit --allow-empty -m "init transcript archive"`, switch back to previous branch with `git switch -`.
   - If yes: report "transcripts branch already exists"
3. Check if `.ai-transcripts/` directory exists.
   - If not: mount with `git worktree add .ai-transcripts transcripts`
   - If yes: report "worktree already mounted"
4. Check if `.ai-transcripts/` is in `.gitignore`.
   - If not: append `.ai-transcripts/` to `.gitignore`
   - If already present: skip (idempotent)

Each step checks state before acting — running twice is a no-op.

**Testing:**
- AC3.1: In a tmp_path git repo, run init → verify `transcripts` branch exists with `git branch --list`, verify it has no common ancestor with main via `git merge-base`
- AC3.2: After init → verify `.ai-transcripts/` is a directory, verify `git worktree list` includes it, verify `.gitignore` contains `.ai-transcripts/`
- AC3.4: Run init twice → no error, `.gitignore` has exactly one `.ai-transcripts/` entry

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "init" -q`
Expected: All init tests pass.

**Commit:** `feat: add init command — orphan branch and worktree setup`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement init command — hook installation

**Verifies:** transcript-archive-v2.AC3.2 (partial — hooks are part of full setup)

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — extend init command

**Implementation:**
Extend `init_cmd()` to install Stop hook:
1. Check if `.claude/settings.local.json` exists.
   - If not: create with hook config
   - If yes: read, check if Stop hook already present
2. Hook config to write/merge:
   ```json
   {
     "hooks": {
       "Stop": [
         {
           "type": "command",
           "command": "claude-transcript-archive archive --quiet"
         }
       ]
     }
   }
   ```
3. If Stop hook array exists but doesn't contain our command, append to it.
4. If Stop hook array already contains our command, skip.
5. Write back with `encoding="utf-8"`.

**Testing:**
- No `.claude/settings.local.json` → creates file with hook
- Existing file without hooks → adds hooks section
- Existing file with other hooks → appends our hook
- Our hook already present → no change (idempotent)

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "init" -q`
Expected: All init tests pass.

**Commit:** `feat: init installs Stop hook in settings.local.json`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Implement init command — project defaults prompting

**Verifies:** transcript-archive-v2.AC5.2 (setup for defaults application)

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — extend init command

**Implementation:**
Extend `init_cmd()` to create `.claude/transcript-defaults.json`:
1. Check if `.claude/transcript-defaults.json` exists.
   - If yes: report "defaults already configured" and skip
   - If not: prompt user interactively
2. Interactive prompts (using `typer.prompt()` with defaults):
   - Tags: comma-separated list (default: empty)
   - Purpose: free text (default: empty)
   - Three Ps context — prompt: free text (default: empty)
   - Three Ps context — process: free text (default: empty)
   - Three Ps context — provenance: free text (default: empty)
   - Target: "branch" (default) / "main" / "here"
3. Write to `.claude/transcript-defaults.json` with `encoding="utf-8"`
4. Accept `--non-interactive` flag to skip prompting and write empty defaults

**Testing:**
- No defaults file + non-interactive → creates skeleton with empty values
- Existing defaults file → skips prompting
- Idempotent: init twice with existing defaults → no change

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "init" -q`
Expected: All init tests pass.

**Commit:** `feat: init creates project defaults with interactive prompting`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Init integration test

**Verifies:** transcript-archive-v2.AC3.1, transcript-archive-v2.AC3.2, transcript-archive-v2.AC3.4

**Files:**
- Modify: `tests/test_cli.py` — add init integration test

**Testing:**
End-to-end test in a tmp_path git repo:
1. Run `init --non-interactive`
2. Verify: `transcripts` branch exists, `.ai-transcripts/` mounted, `.gitignore` updated, `.claude/settings.local.json` has hook, `.claude/transcript-defaults.json` exists
3. Run `init --non-interactive` again
4. Verify: no errors, no duplicate entries, same state

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "init" -v`
Expected: All tests pass.

Run: `uv run ruff check .`
Expected: No lint errors.

**Commit:** `test: add init integration test for full setup + idempotency`
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Add mount recovery logic to archive command

**Files:**
- Modify: `src/claude_transcript_archive/archive.py` — add mount recovery check
- Modify: `tests/test_archive.py` — add mount recovery tests

**Implementation:**
At the start of `archive.archive()`, before writing any files, add mount recovery. The `target` value is already resolved in `cli.py::archive_cmd()` (from CLI flag or project defaults via `merged_opts`) and passed into `archive.archive()` — no direct `discovery` import needed in `archive.py`:
1. Read `target` from the passed-in options (already resolved by cli.py)
2. If target is `"branch"` (default):
   - Check if `.ai-transcripts/` exists
   - If not: check if `transcripts` branch exists (`git branch --list transcripts`)
     - If branch exists: re-mount with `git worktree add .ai-transcripts transcripts`, log info message
     - If branch doesn't exist: raise error with clear message ("No transcripts branch found. Run `claude-transcript-archive init` first."), exit non-zero
   - The Stop hook must NEVER silently discard a transcript
3. If target is `"main"` or `"here"`: write to `ai_transcripts/` in the appropriate location (no branch check needed)

**Testing:**
- .ai-transcripts/ missing + branch exists → auto-remounts, archive succeeds
- .ai-transcripts/ missing + branch missing → clear error, non-zero exit
- .ai-transcripts/ present → normal archive (no recovery needed)
- target="main" + .ai-transcripts/ missing → writes to ai_transcripts/ without error

**Verification:**
Run: `uv run pytest tests/test_archive.py -k "mount_recovery" -q`
Expected: All tests pass.

**Commit:** `feat: add mount recovery to archive command — never silently discard`
<!-- END_TASK_5 -->
