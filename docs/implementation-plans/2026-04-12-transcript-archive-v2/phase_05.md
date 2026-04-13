# Transcript Archive v2 Implementation Plan — Phase 5: Status and Bulk Verbs

**Goal:** `status` reports on session state across worktrees. `bulk` archives all unarchived substantial sessions.

**Architecture:** Two new Typer commands in `cli.py`. `status` uses `discovery.discover_sessions()` and cross-references `catalog.load_manifest()` to report archived/unarchived/trivial. `bulk` iterates unarchived substantial sessions and calls `archive.archive()` for each.

**Tech Stack:** Python >=3.12, Typer, json

**Scope:** Phase 5 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC4: Worktree-aware discovery finds all sessions
- **transcript-archive-v2.AC4.1 Success:** `status` in a repo with 2+ worktrees reports sessions from all worktrees

### transcript-archive-v2.AC5: Hook auto-archives with correct metadata
- **transcript-archive-v2.AC5.1 Success:** Stop hook invocation archives the session with `needs_review: true` when no Three Ps provided
- **transcript-archive-v2.AC5.3 Edge:** Sessions below the trivial threshold are archived with `trivial: true` and `needs_review: true`

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->
<!-- START_TASK_1 -->
### Task 1: Add classify_session() to metadata.py

**Verifies:** transcript-archive-v2.AC5.3 (trivial classification)

**Files:**
- Modify: `src/claude_transcript_archive/metadata.py`
- Modify: `tests/test_metadata.py`

**Implementation:**
Add `classify_session(content: str) -> str` to `metadata.py`:
- Parses JSONL content, counts assistant messages
- Returns `"trivial"` if assistant message count < 5
- Returns `"substantial"` otherwise
- Lightweight — only counts messages, doesn't extract full stats

**Testing:**
- AC5.3: JSONL with 3 assistant messages → returns "trivial"
- JSONL with 10 assistant messages → returns "substantial"
- Empty/malformed JSONL → returns "trivial"

**Verification:**
Run: `uv run pytest tests/test_metadata.py -k "classify" -q`
Expected: All tests pass.

**Commit:** `feat: add classify_session() for trivial/substantial detection`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement status command

**Verifies:** transcript-archive-v2.AC4.1

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `status` command

**Implementation:**
Add `@app.command()` function `status_cmd()`:
- Accepts optional `project_dir` argument (default: current directory)
- Accepts `--json` flag for machine-readable output
- Calls `discovery.discover_sessions()` to find all sessions across worktrees
- Calls `catalog.load_manifest()` from the archive location (`.ai-transcripts/` or configured target)
- Cross-references: which sessions are in manifest (archived) vs not
- For archived sessions, reads sidecar to check `needs_review` (design listed `find_needs_review()` as a catalog function, but direct sidecar reading via `load_catalog()` already provides this; no separate function needed)
- For unarchived sessions, calls `metadata.classify_session()` to determine trivial/substantial
- Human output format:
  ```
  Project: <repo-name> (<N> worktrees)
  
    Archived:      N sessions (M reviewed, K needs_review)
    Unarchived:    N sessions (M substantial, K trivial)
    Total:         N sessions
  ```
- JSON output: `{"archived": [...], "unarchived": [...], "total": N}`

**Testing:**
- AC4.1: Mock discover_sessions to return sessions from multiple worktrees → status reports all
- Status with --json → valid JSON output
- Status in repo with no sessions → shows zeros

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "status" -q`
Expected: All tests pass.

**Commit:** `feat: add status command for session state reporting`
<!-- END_TASK_2 -->
<!-- END_SUBCOMPONENT_A -->

<!-- START_TASK_3 -->
### Task 3: Implement bulk command

**Verifies:** transcript-archive-v2.AC5.1, transcript-archive-v2.AC5.3

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `bulk` command

**Implementation:**
Add `@app.command()` function `bulk_cmd()`:
- Accepts `--local`, `--output`, `--quiet`, `--tags`, `--purpose` flags
- Calls `discovery.discover_sessions()` to find all sessions
- Calls `catalog.load_manifest()` to find already-archived sessions
- Filters to unarchived sessions only
- For each unarchived session:
  - Classifies as trivial/substantial via `metadata.classify_session()`
  - Archives via `archive.archive()` with `needs_review=True` (no Three Ps)
  - Applies project defaults from `discovery.load_project_defaults()` for tags/purpose
  - Marks trivial sessions with `trivial: True` in metadata
- Reports summary: N archived, M skipped (already archived)

**Testing:**
- AC5.1: Bulk archive without Three Ps → all sessions have `needs_review: true` in metadata
- AC5.3: Bulk archive with trivial session → archived with `trivial: true`
- Already-archived sessions → skipped

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "bulk" -q`
Expected: All tests pass.

**Commit:** `feat: add bulk command for batch session archiving`
<!-- END_TASK_3 -->
