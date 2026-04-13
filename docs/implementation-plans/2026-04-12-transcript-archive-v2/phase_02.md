# Transcript Archive v2 Implementation Plan — Phase 2: Module Decomposition

**Goal:** Extract functions from `cli.py` monolith into five new modules, rewrite `cli.py` as Typer app with `archive` verb, split tests to match. Pure structural refactor — all existing behaviour preserved.

**Architecture:** `cli.py` functions split by concern into `discovery.py`, `metadata.py`, `output.py`, `catalog.py`, `archive.py`. `cli.py` becomes a thin Typer app that dispatches to `archive.py`. Tests split into per-module files with shared fixtures in `conftest.py`.

**Tech Stack:** Python >=3.12, Typer, pytest

**Scope:** Phase 2 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** functionality

---

## Acceptance Criteria Coverage

This phase implements and tests:

### transcript-archive-v2.AC1: Package decomposes cleanly
- **transcript-archive-v2.AC1.1 Success:** Each module imports independently without circular dependencies
- **transcript-archive-v2.AC1.2 Success:** All v1 tests pass against decomposed modules with no functionality change
- **transcript-archive-v2.AC1.3 Failure:** Importing a function from the wrong module raises `ImportError` (functions are not re-exported from `cli.py`)

---

<!-- START_SUBCOMPONENT_A (tasks 1-2) -->
<!-- START_TASK_1 -->
### Task 1: Extract shared fixtures to conftest.py

**Files:**
- Create: `tests/conftest.py`
- Modify: `tests/test_cli.py` — remove fixture definitions (keep test classes)

**Implementation:**
Extract these fixtures from `test_cli.py` into `tests/conftest.py`:
- `temp_dir` fixture (creates temporary directory with `.claude` subdirectory)
- `sample_transcript_content` fixture (generates realistic JSONL transcript content)
- `sample_transcript_file` fixture (writes transcript content to a temp file)

These fixtures are currently defined at module level in `test_cli.py` and will be needed by multiple test files after the split.

**Verification:**
Run: `uv run pytest tests/test_cli.py -q`
Expected: All existing tests pass (fixtures now provided by conftest.py)

**Commit:** `refactor: extract shared test fixtures to conftest.py`
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Verify conftest works

**Verification:**
Run: `uv run pytest tests/ -q`
Expected: Same number of tests pass as before. No fixture-not-found errors.

<!-- END_TASK_2 -->
<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 3-4) -->
<!-- START_TASK_3 -->
### Task 3: Extract discovery.py

**Files:**
- Modify: `src/claude_transcript_archive/discovery.py` — populate with extracted functions
- Modify: `src/claude_transcript_archive/cli.py` — update imports to use discovery module
- Create: `tests/test_discovery.py`
- Modify: `tests/test_cli.py` — move discovery test classes out

**Implementation:**
Move these functions from `cli.py` to `discovery.py`:
- `_encode_cc_path` (line 188)
- `get_cc_project_path` (line 197)
- `get_archive_dir` (line 210)
- `get_project_dir_from_transcript` (line 228)
- `auto_discover_transcript` (line 1286)

Update `cli.py` to import from `discovery` module. Do NOT re-export from `cli.py`.

Move these test classes from `test_cli.py` to `test_discovery.py`:
- `TestEncodeCCPath`
- `TestGetCCProjectPath`
- `TestGetArchiveDir`
- `TestGetProjectDirFromTranscript`
- `TestAutoDiscoverTranscript`

Update test imports to reference `discovery` module directly.

**Testing:**
Tests must verify AC1.1 (independent import) and AC1.3 (no re-export):
- AC1.1: `from claude_transcript_archive.discovery import get_cc_project_path` works
- AC1.3: `from claude_transcript_archive.cli import get_cc_project_path` raises ImportError

**Verification:**
Run: `uv run pytest tests/test_discovery.py tests/test_cli.py -q`
Expected: All tests pass.

**Commit:** `refactor: extract discovery module from cli.py`
<!-- END_TASK_3 -->

<!-- START_TASK_4 -->
### Task 4: Extract metadata.py

**Files:**
- Modify: `src/claude_transcript_archive/metadata.py` — populate with extracted functions
- Modify: `src/claude_transcript_archive/cli.py` — update imports
- Create: `tests/test_metadata.py`
- Modify: `tests/test_cli.py` — move metadata test classes out

**Implementation:**
Move these functions and constants from `cli.py` to `metadata.py`:
- `SCHEMA_VERSION` constant (line 22)
- `FILE_TYPE_MAPPINGS` dict (lines 25-64)
- `INPUT_PRICE_PER_M`, `OUTPUT_PRICE_PER_M`, `CACHE_PRICE_PER_M` (lines 67-69)
- `extract_session_stats` (line 367)
- `estimate_cost` (line 463)
- `get_file_type` (line 477)
- `extract_artifacts` (line 483)
- `detect_relationship_hints` (line 560)
- `find_plan_files` (line 624)
- `_is_ide_context_message` (line 639)
- `create_session_metadata` (line 741)

Move these test classes to `test_metadata.py`:
- `TestExtractSessionStats`
- `TestEstimateCost`
- `TestGetFileType`
- `TestExtractArtifacts`
- `TestDetectRelationshipHints`
- `TestCreateSessionMetadata`
- `TestIsIdeContextMessage`
- `TestFindPlanFiles`

**Testing:**
- AC1.1: `from claude_transcript_archive.metadata import extract_session_stats` works
- AC1.3: Direct import from cli raises ImportError

**Verification:**
Run: `uv run pytest tests/test_metadata.py tests/test_cli.py -q`
Expected: All tests pass.

**Commit:** `refactor: extract metadata module from cli.py`
<!-- END_TASK_4 -->
<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 5-6) -->
<!-- START_TASK_5 -->
### Task 5: Extract output.py

**Files:**
- Modify: `src/claude_transcript_archive/output.py` — populate with extracted functions
- Modify: `src/claude_transcript_archive/cli.py` — update imports
- Create: `tests/test_output.py`
- Modify: `tests/test_cli.py` — move output test classes out

**Implementation:**
Move these functions and constants from `cli.py` to `output.py`:
- `PDF_PREAMBLE` constant (line 73+)
- `SPEAKER_LUA_FILTER` constant
- `extract_conversation_messages` (line 908)
- `format_tool_summary` (line 846)
- `generate_conversation_markdown` (line 1012)
- `sanitize_for_pdf` (line 1096)
- `generate_conversation_html_for_pdf` (line 1119)
- `generate_conversation_pdf` (line 1215)
- `update_html_titles` (line 711)

Move these test classes to `test_output.py`:
- `TestUpdateHtmlTitles`
- `TestSanitizeFilename` (if output-related; otherwise stays in test_archive.py)
- Any test classes for markdown/PDF generation

**Testing:**
- AC1.1: `from claude_transcript_archive.output import generate_conversation_markdown` works

**Verification:**
Run: `uv run pytest tests/test_output.py tests/test_cli.py -q`
Expected: All tests pass.

**Commit:** `refactor: extract output module from cli.py`
<!-- END_TASK_5 -->

<!-- START_TASK_6 -->
### Task 6: Extract catalog.py

**Files:**
- Modify: `src/claude_transcript_archive/catalog.py` — populate with extracted functions
- Modify: `src/claude_transcript_archive/cli.py` — update imports
- Create: `tests/test_catalog.py`
- Modify: `tests/test_cli.py` — move catalog test classes out

**Implementation:**
Move these functions from `cli.py` to `catalog.py`:
- `get_manifest_path` (line 283)
- `load_manifest` (line 288)
- `save_manifest` (line 296)
- `get_catalog_path` (line 302)
- `load_catalog` (line 307)
- `save_catalog` (line 325)
- `update_catalog` (line 336)
- `write_metadata_sidecar` (line 818)

Import `SCHEMA_VERSION` from `metadata.py` (used by `write_metadata_sidecar`).

Move these test classes to `test_catalog.py`:
- `TestManifestFunctions`
- `TestCatalogFunctions`
- `TestWriteMetadataSidecar`

**Testing:**
- AC1.1: `from claude_transcript_archive.catalog import load_catalog` works

**Verification:**
Run: `uv run pytest tests/test_catalog.py tests/test_cli.py -q`
Expected: All tests pass.

**Commit:** `refactor: extract catalog module from cli.py`
<!-- END_TASK_6 -->
<!-- END_SUBCOMPONENT_C -->

<!-- START_SUBCOMPONENT_D (tasks 7-8) -->
<!-- START_TASK_7 -->
### Task 7: Extract archive.py and rewrite cli.py as Typer app

**Files:**
- Modify: `src/claude_transcript_archive/archive.py` — populate with extracted functions
- Modify: `src/claude_transcript_archive/cli.py` — rewrite as Typer app with `archive` command
- Create: `tests/test_archive.py`
- Modify: `tests/test_cli.py` — move archive test classes, keep CLI integration tests

**Implementation:**
Move these functions from `cli.py` to `archive.py`:
- `archive` (line 1312) — the main orchestration function
- `sanitize_filename` (line 704)
- `compute_file_hash` (line 732) — used by archive() for change detection
- `generate_title_from_content` (line 659)
- `log_error` (line 834)
- `log_info` (line 840)

`archive.py` imports from: `discovery`, `metadata`, `output`, `catalog`.

Rewrite `cli.py` as a Typer app:
- Create `app = typer.Typer()` at module level
- Create `@app.command()` function `archive_cmd()` that:
  - Handles stdin JSON input (check `sys.stdin.isatty()`)
  - Accepts `--transcript`, `--session-id`, `--title`, `--retitle`, `--force`, `--local`, `--output`, `--quiet`, `--prompt`, `--process`, `--provenance` parameters
  - Calls `archive.archive()` with resolved arguments
- Entry point is `app` (the Typer instance)

Move these test classes to `test_archive.py`:
- `TestArchiveFunction`
- `TestGenerateTitleFromContent`
- `TestSanitizeFilename` (if not moved to test_output.py)
- `TestComputeFileHash`
- `TestEdgeCases`

Keep in `test_cli.py`:
- `TestCLIIntegration`
- `TestLogError` / `TestLogFunctions`
- Any new tests for Typer CLI behaviour

**Testing:**
- AC1.2: All v1 tests pass against decomposed modules
- AC2.5: `archive` with no args reads stdin JSON and produces identical output to v1

**Verification:**
Run: `uv run pytest tests/ -q`
Expected: All tests pass across all test files.

**Commit:** `refactor: extract archive module, rewrite cli.py as Typer app`
<!-- END_TASK_7 -->

<!-- START_TASK_8 -->
### Task 8: Verify full decomposition and AC1 coverage

**Verifies:** transcript-archive-v2.AC1.1, transcript-archive-v2.AC1.2, transcript-archive-v2.AC1.3

**Files:**
- Modify: `tests/test_cli.py` — add AC verification tests

**Testing:**
Add explicit tests for each AC:
- AC1.1: Test that each of the 5 new modules imports independently (no circular deps, no ImportError)
- AC1.2: Run full test suite — count must match or exceed v1 test count (93 tests)
- AC1.3: Test that importing a moved function from `cli` raises ImportError (pick 3 representative functions)

**Verification:**
Run: `uv run pytest tests/ -v`
Expected: All tests pass. Total test count >= 93 (v1 count) + 3 (new AC verification tests).

Run: `uv run ruff check .`
Expected: No lint errors.

**Commit:** `test: add AC1 verification tests for module decomposition`
<!-- END_TASK_8 -->

<!-- START_TASK_9 -->
### Task 9: Fix remaining Windows bugs from PR #2

**Files:**
- Modify: `src/claude_transcript_archive/metadata.py` — fix `extract_artifacts` path normalisation
- Modify: `src/claude_transcript_archive/discovery.py` — fix `get_project_dir_from_transcript` POSIX assumption
- Modify: `tests/test_metadata.py` — fix `TestExtractArtifacts::test_relative_paths_with_project`
- Modify: `tests/test_discovery.py` — fix POSIX path assumption tests

**Implementation:**
Two bugs identified in PR #2's scope note:
1. `extract_artifacts`/`make_relative` produces backslash paths where tests expect forward slash. Fix: apply `.as_posix()` normalisation to artifact paths before storing.
2. `get_project_dir_from_transcript` has `encoded_path.startswith("-")` check that assumes POSIX-encoded paths. Fix: use `_encode_cc_path` consistently for path matching.

**Testing:**
- Existing failing test `TestExtractArtifacts::test_relative_paths_with_project` should pass after fix
- Existing failing test `TestCLIIntegration::test_cli_with_three_ps_args` should pass after fix

**Verification:**
Run: `uv run pytest tests/ -q`
Expected: 0 failures (previously 1 pre-existing failure).

**Commit:** `fix: resolve remaining Windows path bugs from PR #2`
<!-- END_TASK_9 -->
<!-- END_SUBCOMPONENT_D -->
