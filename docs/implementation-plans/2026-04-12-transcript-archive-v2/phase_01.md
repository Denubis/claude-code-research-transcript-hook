# Transcript Archive v2 Implementation Plan — Phase 1: Project Setup and Typer Migration

**Goal:** Update project configuration for Python >=3.12, add Typer dependency, create module skeleton with empty files.

**Architecture:** Infrastructure-only phase. Updates pyproject.toml (Python version, dependencies, entry point, classifiers, ruff config), creates empty module files as placeholders for Phase 2 decomposition.

**Tech Stack:** Python >=3.12, Typer, uv, ruff

**Scope:** Phase 1 of 8 from original design

**Codebase verified:** 2026-04-13

**Phase Type:** infrastructure

---

## Acceptance Criteria Coverage

This phase is infrastructure setup. No acceptance criteria are implemented or tested.

**Verifies: None** — success = `uv sync` succeeds, `uv run ruff check .` clean, `claude-transcript-archive --help` shows Typer output.

---

<!-- START_TASK_1 -->
### Task 1: Update pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Update pyproject.toml**

Apply these changes to `pyproject.toml`:

1. Bump `requires-python` from `">=3.10"` to `">=3.12"`
2. Add `"typer>=0.12"` to `dependencies` list
3. Remove classifiers for Python 3.10 and 3.11
4. Replace both entry points with single entry point:
   ```toml
   [project.scripts]
   claude-transcript-archive = "claude_transcript_archive.cli:app"
   ```
   Note: entry point target changes from `cli:main` (function) to `cli:app` (Typer instance). The `app` object will be created in Phase 2 when cli.py is rewritten.
5. Update ruff `target-version` from `"py310"` to `"py312"`
6. Bump version to `"0.4.0"` (major feature release)

**Step 2: Verify**

Run: `uv sync`
Expected: Dependencies install without errors, Typer is available.

Run: `uv run ruff check .`
Expected: No new lint errors from config change.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump Python to >=3.12, add Typer dependency, single entry point"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Create module skeleton

**Files:**
- Create: `src/claude_transcript_archive/discovery.py`
- Create: `src/claude_transcript_archive/metadata.py`
- Create: `src/claude_transcript_archive/output.py`
- Create: `src/claude_transcript_archive/catalog.py`
- Create: `src/claude_transcript_archive/archive.py`

**Step 1: Create empty module files**

Create each file with a single docstring describing its purpose:

- `discovery.py`: `"""Path encoding, worktree resolution, session discovery, and project defaults."""`
- `metadata.py`: `"""JSONL parsing, token/cost extraction, artifact categorisation, and trivial classification."""`
- `output.py`: `"""HTML, markdown, and PDF output generation."""`
- `catalog.py`: `"""Session manifest, catalog index, and metadata sidecar management."""`
- `archive.py`: `"""Archive orchestration: hash-based skip detection, directory naming, session archiving."""`

Each file should contain only the docstring — no imports, no code. These are placeholders for Phase 2.

**Step 2: Verify**

Run: `uv run python -c "import claude_transcript_archive.discovery; import claude_transcript_archive.metadata; import claude_transcript_archive.output; import claude_transcript_archive.catalog; import claude_transcript_archive.archive; print('All modules importable')"`
Expected: `All modules importable`

**Step 3: Commit**

```bash
git add src/claude_transcript_archive/discovery.py src/claude_transcript_archive/metadata.py src/claude_transcript_archive/output.py src/claude_transcript_archive/catalog.py src/claude_transcript_archive/archive.py
git commit -m "chore: create empty module skeleton for v2 decomposition"
```
<!-- END_TASK_2 -->
