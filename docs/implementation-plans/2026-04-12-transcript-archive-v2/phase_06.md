# Transcript Archive v2 Implementation Plan — Phase 6: Update and Regenerate Verbs

**Goal:** `update` modifies metadata on existing archives. `regenerate` re-renders output files from raw transcript backups.

**Architecture:** Two new Typer commands in `cli.py`. `update` modifies `session.meta.json` sidecars then calls `catalog.rebuild_indexes()`. `regenerate` re-runs `output.generate_*` from `raw-transcript.jsonl` stored in each archive directory.

**Tech Stack:** Python >=3.12, Typer, json

**Scope:** Phase 6 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements `rebuild_indexes()` (shared infrastructure for `update`, `regenerate`, and `clean`) and the `update` and `regenerate` verbs. AC2.2 (tags/purpose in archive) is covered in Phase 8 when `--tags`/`--purpose` parameters are wired.

**Verifies: None directly** — this phase creates capabilities used by other phases' AC verification.

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->
<!-- START_TASK_1 -->
### Task 1: Add rebuild_indexes() to catalog.py

**Files:**
- Modify: `src/claude_transcript_archive/catalog.py`
- Modify: `tests/test_catalog.py`

**Implementation:**
Add `rebuild_indexes(archive_dir: Path)` to `catalog.py`:
- Glob for all `*/session.meta.json` files under archive_dir
- Read each sidecar, extract session entry for CATALOG.json and manifest mapping
- Rebuild `.session_manifest.json` from session_id → directory mappings
- Rebuild `CATALOG.json` with sessions array and computed `needs_review_count`
- Write both files with `encoding="utf-8"`

This function is also used by `clean` in Phase 7.

**Testing:**
- Create 3 archive dirs with session.meta.json sidecars → rebuild produces correct CATALOG.json with 3 sessions
- needs_review_count computed correctly from sidecar flags
- Missing sidecars skipped gracefully

**Verification:**
Run: `uv run pytest tests/test_catalog.py -k "rebuild" -q`
Expected: All tests pass.

**Commit:** `feat: add rebuild_indexes() for catalog/manifest regeneration from sidecars`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Implement update command

**Verifies:** transcript-archive-v2.AC2.2 (partial — tags/purpose in update)

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `update` command

**Implementation:**
Add `@app.command()` function `update_cmd()`:
- Accepts `--session-id` (single session) or `--all-needs-review` (batch)
- Accepts `--title`, `--tags`, `--purpose`, `--prompt`, `--process`, `--provenance`
- For single session: find archive dir via manifest, read sidecar, apply changes, write sidecar
- For batch: scan sidecars for `needs_review: true`, apply changes to each
- After modifying sidecars: call `catalog.rebuild_indexes()` to update indexes
- If Three Ps all provided: set `needs_review: false`

**Testing:**
- Update single session tags/purpose → sidecar reflects changes
- Update with all Three Ps → needs_review becomes false
- Batch update → all needs_review sessions get updated
- Update non-existent session → clear error message

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "update" -q`
Expected: All tests pass.

**Commit:** `feat: add update command for metadata modification`
<!-- END_TASK_2 -->
<!-- END_SUBCOMPONENT_A -->

<!-- START_TASK_3 -->
### Task 3: Implement regenerate command

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `regenerate` command

**Implementation:**
Add `@app.command()` function `regenerate_cmd()`:
- Accepts `--session-id` (single) or `--all` (every archived session)
- For each target session:
  - Read `raw-transcript.jsonl` from archive directory
  - Re-run `output.generate_conversation_markdown()` and `output.generate_conversation_pdf()`
  - Update `index.html` via `claude-code-transcripts` subprocess
  - Read title from sidecar or `.title` file
- Report: N sessions regenerated

**Testing:**
- Regenerate single session → new conversation.md written with current date
- Regenerate with missing raw-transcript.jsonl → clear error
- Regenerate all → processes every archived session

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "regenerate" -q`
Expected: All tests pass.

**Commit:** `feat: add regenerate command for re-rendering outputs`
<!-- END_TASK_3 -->
