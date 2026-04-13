# Transcript Archive v2 Implementation Plan — Phase 8: Tags, Purpose, and Integration Polish

**Goal:** Wire `--tags` and `--purpose` through all relevant verbs. End-to-end integration test of the full workflow.

**Architecture:** Add Typer `--tags` and `--purpose` parameters to `archive`, `bulk`, `update` commands. Ensure metadata.py flows these into session.meta.json. Integration test validates the full init→archive→status→update→regenerate→clean lifecycle.

**Tech Stack:** Python >=3.12, Typer, pytest, git (for integration test fixtures)

**Scope:** Phase 8 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC1: Package decomposes cleanly
- **transcript-archive-v2.AC1.4 Edge:** Package installs and runs on Python 3.12 (minimum) and 3.13+

### transcript-archive-v2.AC2: CLI verbs work correctly
- **transcript-archive-v2.AC2.1 Success:** Each of the seven verbs (`init`, `archive`, `bulk`, `status`, `update`, `clean`, `regenerate`) is callable and produces `--help` output
- **transcript-archive-v2.AC2.2 Success:** `archive` with `--tags foo bar --purpose "testing"` stores both fields in `session.meta.json`
- **transcript-archive-v2.AC2.3 Success:** `status --json` outputs valid JSON with session counts and per-session details
- **transcript-archive-v2.AC2.4 Failure:** Calling a verb with invalid flags produces a Typer error message, not a traceback
- **transcript-archive-v2.AC2.5 Edge:** `archive` with no arguments reads stdin JSON (hook mode) and produces identical output to v1 for the same input

### transcript-archive-v2.AC3: Orphan branch storage works
- **transcript-archive-v2.AC3.3 Success:** `archive` writes output files into `.ai-transcripts/` when target is `branch`

---

<!-- START_TASK_1 -->
### Task 1: Add --tags and --purpose to archive and bulk commands

**Verifies:** transcript-archive-v2.AC2.2

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add parameters to archive and bulk commands
- Modify: `src/claude_transcript_archive/metadata.py` — ensure tags/purpose flow into session metadata
- Modify: `src/claude_transcript_archive/archive.py` — pass tags/purpose through to metadata creation

**Implementation:**
Add to `archive_cmd()` and `bulk_cmd()` in `cli.py`:
```python
tags: Annotated[list[str], typer.Option(help="Freeform tags")] = [],
purpose: Annotated[str, typer.Option(help="Session purpose")] = "",
target: Annotated[str, typer.Option(help="Storage target: branch, main, or here")] = "",
```

The `--target` flag overrides the `target` field from `.claude/transcript-defaults.json`. Values: `"branch"` (default, uses .ai-transcripts/ worktree), `"main"` (writes to main worktree's ai_transcripts/), `"here"` (writes to current worktree's ai_transcripts/). Empty string means "use project defaults, falling back to branch".

In `archive.py`, pass `tags` and `purpose` through to `metadata.create_session_metadata()`.

In `metadata.py`, ensure `create_session_metadata()` populates the existing `tags` and `purpose` fields in session.meta.json (these fields exist in the schema but are never populated in v1).

Merge with project defaults: CLI flags override `.claude/transcript-defaults.json` values.

**Testing:**
- AC2.2: Archive with `--tags foo --tags bar --purpose "testing"` → session.meta.json has `"tags": ["foo", "bar"]` and `"purpose": "testing"`
- Archive without tags/purpose but with project defaults → defaults applied
- Archive with CLI flags AND defaults → CLI flags win
- Archive with `--target main` → writes to ai_transcripts/ not .ai-transcripts/
- Archive with `--target branch` in uninitialised repo → triggers mount recovery or clear error

**Verification:**
Run: `uv run pytest tests/ -k "tags or purpose" -q`
Expected: All tests pass.

**Commit:** `feat: wire --tags and --purpose through archive and bulk commands`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Full integration test

**Verifies:** transcript-archive-v2.AC2.1, transcript-archive-v2.AC2.3, transcript-archive-v2.AC2.4, transcript-archive-v2.AC2.5, transcript-archive-v2.AC3.3

**Files:**
- Create: `tests/test_integration.py`

**Implementation:**
End-to-end integration test using a real temporary git repository:

1. Create tmp_path git repo with `git init`
2. Run `init --non-interactive` → verify branch, worktree, hooks, defaults created
3. Create a fake session JSONL in the expected `~/.claude/projects/` location (monkeypatched)
4. Run `archive` with stdin JSON → verify output in `.ai-transcripts/`
5. Run `status` → verify reports 1 archived session
6. Run `status --json` → verify valid JSON output (AC2.3)
7. Run `update --session-id <id> --tags test --purpose "integration"` → verify sidecar updated
8. Run `regenerate --session-id <id>` → verify outputs re-rendered
9. Delete CATALOG.json, run `clean --execute` → verify rebuilt
10. Verify each verb responds to `--help` (AC2.1)
11. Verify invalid flag produces Typer error, not traceback (AC2.4)

**Testing:**
- AC2.1: All 7 verbs produce --help output without error
- AC2.3: status --json returns valid JSON
- AC2.4: Invalid flag → exit code != 0, stderr contains "Error" or "Usage", no Python traceback
- AC2.5: stdin JSON archive → same output structure as v1
- AC3.3: Archived files appear in .ai-transcripts/, not ai_transcripts/

**Verification:**
Run: `uv run pytest tests/test_integration.py -v`
Expected: All integration tests pass.

Run: `uv run pytest tests/ -q`
Expected: ALL tests pass across all test files.

Run: `uv run ruff check .`
Expected: No lint errors.

**Commit:** `test: add full lifecycle integration test`
<!-- END_TASK_2 -->
