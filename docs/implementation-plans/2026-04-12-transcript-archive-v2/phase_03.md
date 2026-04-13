# Transcript Archive v2 Implementation Plan — Phase 3: Discovery — Worktree Resolution and Project Defaults

**Goal:** Add worktree-aware session discovery and project defaults loading to `discovery.py`.

**Architecture:** Three new functions in `discovery.py`: `resolve_worktrees()` parses `git worktree list --porcelain`, `discover_sessions()` maps worktree paths to `~/.claude/projects/` and scans for JONLs, `load_project_defaults()` reads `.claude/transcript-defaults.json`.

**Tech Stack:** Python >=3.12, subprocess (git), json

**Scope:** Phase 3 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC4: Worktree-aware discovery finds all sessions
- **transcript-archive-v2.AC4.1 Success:** `status` in a repo with 2+ worktrees reports sessions from all worktrees
- **transcript-archive-v2.AC4.3 Failure:** `status` in a non-git directory produces a clear error, not a traceback

### transcript-archive-v2.AC5: Hook auto-archives with correct metadata
- **transcript-archive-v2.AC5.2 Success:** Project defaults from `.claude/transcript-defaults.json` are applied when no CLI flags override them

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->
<!-- START_TASK_1 -->
### Task 1: Implement resolve_worktrees()

**Verifies:** transcript-archive-v2.AC4.1 (partial — discovers worktree paths)

**Files:**
- Modify: `src/claude_transcript_archive/discovery.py`
- Modify: `tests/test_discovery.py`

**Implementation:**
Add `resolve_worktrees()` to `discovery.py`:
- Runs `git worktree list --porcelain` via `subprocess.run`
- Parses output: blocks separated by blank lines, each block has `worktree <path>` line
- Returns `list[Path]` of all worktree absolute paths
- If `git` fails (non-git directory), raises a clear error with actionable message (AC4.3)
- If `git worktree list` returns empty, returns list with just current directory

Porcelain format (verified):
```
worktree /path/to/main
HEAD abc123
branch refs/heads/main

worktree /path/to/feature
HEAD def456
branch refs/heads/feature
```

**Testing:**
- AC4.1: Mock `subprocess.run` with multi-worktree porcelain output → returns both paths
- AC4.3: Mock `subprocess.run` raising CalledProcessError → raises clear error message

**Verification:**
Run: `uv run pytest tests/test_discovery.py -q`
Expected: All tests pass.

**Commit:** `feat: add resolve_worktrees() to discovery module`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement discover_sessions()

**Verifies:** transcript-archive-v2.AC4.1, transcript-archive-v2.AC4.2

**Files:**
- Modify: `src/claude_transcript_archive/discovery.py`
- Modify: `tests/test_discovery.py`

**Implementation:**
Add `discover_sessions()` to `discovery.py`:
- Calls `resolve_worktrees()` to get all worktree paths
- Maps each path through `_encode_cc_path` → `~/.claude/projects/{encoded}/`
- Scans each projects directory for `*.jsonl` files
- Returns `list[tuple[Path, str]]` — (transcript_path, session_id) pairs
- Deduplicates by session_id (same session may appear under multiple worktree paths)
- Session ID extracted from filename: `{session_id}.jsonl`

**Testing:**
- AC4.1: Create tmp_path with two worktree-encoded project dirs, each with JSONL files → returns sessions from both
- AC4.2: Put same session_id JSONL under two different encoded paths → returned only once

**Verification:**
Run: `uv run pytest tests/test_discovery.py -q`
Expected: All tests pass.

**Commit:** `feat: add discover_sessions() for worktree-aware session scanning`
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Implement load_project_defaults()

**Verifies:** transcript-archive-v2.AC5.2

**Files:**
- Modify: `src/claude_transcript_archive/discovery.py`
- Modify: `tests/test_discovery.py`

**Implementation:**
Add `load_project_defaults()` to `discovery.py`:
- Searches for `.claude/transcript-defaults.json` starting from given project dir, walking up to git root
- If found, reads and returns as dict
- If not found, returns empty dict (no error, no warning)
- Validates expected keys: `tags` (list[str]), `purpose` (str), `three_ps_context` (dict), `target` (str)
- Unknown keys ignored (forward compatibility)
- Malformed JSON logs a warning and returns empty dict

**Testing:**
- AC5.2: Create tmp_path with `.claude/transcript-defaults.json` containing tags/purpose → returns those values
- Missing file → returns empty dict
- Malformed JSON → returns empty dict (no exception)

**Verification:**
Run: `uv run pytest tests/test_discovery.py -q`
Expected: All tests pass.

**Commit:** `feat: add load_project_defaults() for project-level configuration`
<!-- END_TASK_3 -->
<!-- END_SUBCOMPONENT_A -->
