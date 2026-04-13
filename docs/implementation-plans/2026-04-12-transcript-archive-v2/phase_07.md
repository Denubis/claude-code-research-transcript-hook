# Transcript Archive v2 Implementation Plan — Phase 7: Clean Verb

**Goal:** `clean` deduplicates archives, migrates old `ai_transcripts/` directories into the branch, and repairs index files.

**Architecture:** New Typer command in `cli.py`. Uses `discovery.discover_sessions()` and `catalog.rebuild_indexes()` for repair. Dry-run is default (deliberate deviation from unix convention — this command touches indexes and migrates archives, so the safe default is report-only).

**Tech Stack:** Python >=3.12, Typer, json, shutil

**Scope:** Phase 7 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC6: Index repair rebuilds from sidecars
- **transcript-archive-v2.AC6.1 Success:** Deleting `CATALOG.json` and running `clean` regenerates it with correct session count and `needs_review_count`
- **transcript-archive-v2.AC6.2 Success:** Deleting `.session_manifest.json` and running `clean` regenerates it with correct session-to-directory mappings
- **transcript-archive-v2.AC6.3 Failure:** `clean --dry-run` reports what would be repaired but modifies no files

---

<!-- START_TASK_1 -->
### Task 1: Implement clean — duplicate detection and legacy migration

**Files:**
- Modify: `src/claude_transcript_archive/cli.py` — add `clean` command
- Modify: `src/claude_transcript_archive/archive.py` — add `find_duplicates()` and `migrate_legacy()`

**Implementation:**
Add to `archive.py`:
- `find_duplicates(archive_dir: Path) -> list[tuple[str, list[Path]]]`: scans archive dirs, groups by session_id (from sidecar), returns session_ids with multiple archive directories
- `migrate_legacy(legacy_dir: Path, target_dir: Path, dry_run: bool) -> list[str]`: moves archive directories from old `ai_transcripts/` to `.ai-transcripts/`, returns list of migrated session names

Add `@app.command()` function `clean_cmd()` to `cli.py`:
- Accepts `--dry-run` (default: True) and `--execute` flags
- Step 1: Find duplicates via `archive.find_duplicates()`
- Step 2: Check for legacy `ai_transcripts/` directory, offer migration
- Step 3: Rebuild indexes via `catalog.rebuild_indexes()`
- In dry-run mode: report all findings without modifying files
- In execute mode: merge duplicates (keep newest), migrate legacy, rebuild indexes

**Testing:**
- Two archive dirs with same session_id → detected as duplicate
- Legacy ai_transcripts/ with archives → detected for migration
- Dry-run mode → no file changes (verify with before/after comparison)
- Execute mode → duplicates merged, legacy migrated

**Verification:**
Run: `uv run pytest tests/test_archive.py -k "clean or duplicate or migrate" -q`
Expected: All tests pass.

**Commit:** `feat: add clean command — dedup, migrate, repair`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Clean — index repair integration

**Verifies:** transcript-archive-v2.AC6.1, transcript-archive-v2.AC6.2, transcript-archive-v2.AC6.3

**Files:**
- Modify: `tests/test_cli.py` — add clean integration tests

**Testing:**
Integration tests for index repair:
- AC6.1: Create 3 archives with sidecars, delete CATALOG.json, run `clean --execute` → CATALOG.json regenerated with 3 sessions and correct needs_review_count
- AC6.2: Delete .session_manifest.json, run `clean --execute` → manifest regenerated with correct session→directory mappings
- AC6.3: Delete both index files, run `clean` (no flags, dry-run default) → report shows "would rebuild indexes" but files remain deleted

**Verification:**
Run: `uv run pytest tests/test_cli.py -k "clean" -v`
Expected: All tests pass.

Run: `uv run ruff check .`
Expected: No lint errors.

**Commit:** `test: add clean integration tests for index repair`
<!-- END_TASK_2 -->
