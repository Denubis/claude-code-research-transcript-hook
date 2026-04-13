# Transcript Archive v2 Design

**GitHub Issue:** None

## Summary

This document specifies version 2 of the `claude-research-transcript-hook` tool — a Python package that archives Claude Code conversation transcripts with research-grade metadata. Where v1 was a single large script (`cli.py`) invoked primarily through a shell hook, v2 decomposes that monolith into six focused modules and introduces a seven-verb CLI built with Typer. The core archiving logic (parsing JSONL transcripts, extracting token counts and tool calls, generating HTML/markdown/PDF output, and maintaining catalog indexes) remains unchanged; v2 reorganises it into a maintainable package structure and extends it with new operational capabilities.

The most significant architectural change is storage strategy. Rather than scattering archive directories across whichever git worktree was active when a session ended, v2 uses a dedicated `transcripts` orphan branch — a branch with no shared history with the project's working branches — mounted as a `.ai-transcripts/` directory via `git worktree add`. A new `init` verb sets this up in one step. Discovery logic reads `git worktree list` to find every worktree for a given repository and maps each through Claude Code's path-encoding scheme, so `status` and `bulk` commands see all sessions regardless of which worktree generated them. Index files (`CATALOG.json`, `.session_manifest.json`) are treated as regenerable caches derived from per-session `session.meta.json` sidecar files, making merge conflict recovery trivial: delete the conflicted index and run `clean`.

## Definition of Done

1. **Package decomposition** — `cli.py` monolith split into six modules: `discovery.py` (path encoding, worktree resolution, project defaults), `metadata.py` (JSONL parsing, token/cost extraction, trivial classification), `output.py` (HTML/markdown/PDF generation), `catalog.py` (manifest, catalog, sidecar management), `archive.py` (orchestration), `cli.py` (Typer app, verb dispatch). Tests split to match. Python >=3.12.

2. **Typer CLI with seven verbs** — `init` (set up orphan branch, hooks, project defaults), `archive` (single session), `bulk` (all unarchived sessions), `status` (report transcribed/untranscribed/trivial), `update` (modify metadata on existing archives), `clean` (deduplicate, migrate old archives, repair indexes), `regenerate` (re-render HTML/PDF/markdown). No backwards compatibility with v1 invocation. `--tags` and `--purpose` arguments on relevant verbs.

3. **Orphan branch storage** — Transcripts stored on a dedicated `transcripts` orphan branch, mounted as a `.ai-transcripts/` worktree. `init` verb creates the branch, mounts it, adds to `.gitignore`, installs hooks, and writes `.claude/transcript-defaults.json` with tags, purpose, Three Ps context, and target preference. `--target` flag as escape hatch for alternative storage.

4. **Worktree-aware discovery** — All verbs that scan sessions (`status`, `bulk`, `clean`, `update`) resolve the current repo's full worktree set via `git worktree list`, map each path through `_encode_cc_path`, and find all matching `~/.claude/projects/` directories. Shared discovery logic in `discovery.py`.

5. **Simplified hook system** — Stop hook only (no UserPromptSubmit). Auto-archives every session. Sessions without Three Ps get `needs_review: true`. Trivial sessions flagged `trivial: true`. Project defaults from `.claude/transcript-defaults.json` applied automatically.

6. **Index repair** — `CATALOG.json` and `.session_manifest.json` are regenerable caches derived from `session.meta.json` sidecars. `clean` verb rebuilds them from disk. Merge conflicts resolved by deleting conflicted file and running `clean`.

## Acceptance Criteria

### transcript-archive-v2.AC1: Package decomposes cleanly
- **transcript-archive-v2.AC1.1 Success:** Each module imports independently without circular dependencies
- **transcript-archive-v2.AC1.2 Success:** All v1 tests pass against decomposed modules with no functionality change
- **transcript-archive-v2.AC1.3 Failure:** Importing a function from the wrong module raises `ImportError` (functions are not re-exported from `cli.py`)
- **transcript-archive-v2.AC1.4 Edge:** Package installs and runs on Python 3.12 (minimum) and 3.13+

### transcript-archive-v2.AC2: CLI verbs work correctly
- **transcript-archive-v2.AC2.1 Success:** Each of the seven verbs (`init`, `archive`, `bulk`, `status`, `update`, `clean`, `regenerate`) is callable and produces `--help` output
- **transcript-archive-v2.AC2.2 Success:** `archive` with `--tags foo bar --purpose "testing"` stores both fields in `session.meta.json`
- **transcript-archive-v2.AC2.3 Success:** `status --json` outputs valid JSON with session counts and per-session details
- **transcript-archive-v2.AC2.4 Failure:** Calling a verb with invalid flags produces a Typer error message, not a traceback
- **transcript-archive-v2.AC2.5 Edge:** `archive` with no arguments reads stdin JSON (hook mode) and produces identical output to v1 for the same input

### transcript-archive-v2.AC3: Orphan branch storage works
- **transcript-archive-v2.AC3.1 Success:** `init` creates an orphan `transcripts` branch with no common ancestor to `main`
- **transcript-archive-v2.AC3.2 Success:** After `init`, `.ai-transcripts/` is a mounted git worktree on the `transcripts` branch, and `.ai-transcripts/` appears in `.gitignore`
- **transcript-archive-v2.AC3.3 Success:** `archive` writes output files into `.ai-transcripts/` when target is `branch`
- **transcript-archive-v2.AC3.4 Edge:** `init` run twice is idempotent — no error, no duplicate entries in `.gitignore`, no orphan branch recreation

### transcript-archive-v2.AC4: Worktree-aware discovery finds all sessions
- **transcript-archive-v2.AC4.1 Success:** `status` in a repo with 2+ worktrees reports sessions from all worktrees
- **transcript-archive-v2.AC4.2 Success:** Sessions discovered under different worktree paths but with the same session ID are recognised as the same session
- **transcript-archive-v2.AC4.3 Failure:** `status` in a non-git directory produces a clear error, not a traceback

### transcript-archive-v2.AC5: Hook auto-archives with correct metadata
- **transcript-archive-v2.AC5.1 Success:** Stop hook invocation archives the session with `needs_review: true` when no Three Ps provided
- **transcript-archive-v2.AC5.2 Success:** Project defaults from `.claude/transcript-defaults.json` are applied when no CLI flags override them
- **transcript-archive-v2.AC5.3 Edge:** Sessions below the trivial threshold are archived with `trivial: true` and `needs_review: true`

### transcript-archive-v2.AC6: Index repair rebuilds from sidecars
- **transcript-archive-v2.AC6.1 Success:** Deleting `CATALOG.json` and running `clean` regenerates it with correct session count and `needs_review_count`
- **transcript-archive-v2.AC6.2 Success:** Deleting `.session_manifest.json` and running `clean` regenerates it with correct session-to-directory mappings
- **transcript-archive-v2.AC6.3 Failure:** `clean --dry-run` reports what would be repaired but modifies no files

## Glossary

- **Three Ps (Prompt / Process / Provenance)**: The IDW2025 reproducibility metadata framework. Each archived session can carry three descriptive fields: what was asked (*Prompt*), how the tool was used (*Process*), and the session's role in the research workflow (*Provenance*).
- **IDW2025**: A reproducibility framework for AI-assisted research. Defines the Three Ps metadata schema.
- **JSONL**: JSON Lines format — one JSON object per line. Claude Code writes conversation transcripts as `.jsonl` files; this package reads them to extract session statistics and generate output.
- **session.meta.json**: Per-session metadata sidecar file stored alongside the archive outputs. The authoritative record for a session; all index files are derived from it.
- **CATALOG.json**: Aggregate index file listing all archived sessions with summary counts (e.g. `needs_review_count`). A regenerable cache — not the source of truth.
- **.session_manifest.json**: Index mapping session IDs to their archive directories. Also a regenerable cache derived from sidecars.
- **orphan branch**: A git branch with no commit history in common with any other branch. Used here so transcript storage never appears in feature branch history or diffs.
- **git worktree**: A git feature that mounts a second branch as a directory within (or adjacent to) an existing repository checkout, without cloning. Used to mount the `transcripts` orphan branch at `.ai-transcripts/`.
- **`_encode_cc_path`**: Claude Code's path-encoding scheme for mapping absolute project paths to directory names under `~/.claude/projects/`. Handles cross-platform path separators and Windows drive letters.
- **`~/.claude/projects/`**: Directory where Claude Code stores per-project session JSONL transcripts, keyed by encoded project path.
- **Stop hook**: A Claude Code hook that fires when a session ends. v2 uses only this hook for auto-archiving.
- **`needs_review`**: Boolean flag in `session.meta.json`. Set `true` when a session is auto-archived without Three Ps metadata, indicating manual curation is needed.
- **`trivial`**: Boolean flag for sessions below a significance threshold (low token count or assistant message count). Archived but flagged to distinguish from substantive sessions.
- **`transcript-defaults.json`**: Per-project configuration file at `.claude/transcript-defaults.json`. Provides default tags, purpose, and Three Ps context applied to all sessions archived from that project.
- **Typer**: Python CLI framework built on Click. Used for the seven-verb CLI in v2.
- **`claude-code-transcripts`**: External Python package that handles the conversion of JSONL transcripts to HTML. `output.py` delegates to it.
- **`tmp_path`**: pytest fixture that provides a temporary directory unique to each test invocation. Used throughout the test suite to avoid hardcoded paths.

## Architecture

### Module Structure

Six modules under `src/claude_transcript_archive/`, each with a matching test file:

| Module | Responsibility | Key functions migrated from v1 `cli.py` |
|--------|---------------|----------------------------------------|
| `discovery.py` | Path encoding, worktree resolution, session JSONL location, project defaults loading | `_encode_cc_path`, `get_cc_project_path`, `get_archive_dir`, `get_project_dir_from_transcript`, `auto_discover_transcript` |
| `metadata.py` | JSONL parsing, token/cost/tool extraction, artifact categorisation, Three Ps structure, trivial classification | `extract_session_stats`, `estimate_cost`, `extract_artifacts`, `get_file_type`, `detect_relationship_hints`, `find_plan_files`, `create_session_metadata`, `_is_ide_context_message` |
| `output.py` | HTML generation (via `claude-code-transcripts`), markdown conversion, PDF via pandoc+lualatex, HTML title patching | `generate_conversation_markdown`, `generate_conversation_html_for_pdf`, `generate_conversation_pdf`, `update_html_titles`, `extract_conversation_messages`, `sanitize_for_pdf`, `format_tool_summary` |
| `catalog.py` | `.session_manifest.json`, `CATALOG.json`, `session.meta.json` sidecars, needs_review tracking | `load_manifest`, `save_manifest`, `load_catalog`, `save_catalog`, `update_catalog`, `write_metadata_sidecar`, `get_manifest_path`, `get_catalog_path` |
| `archive.py` | Orchestrates a single archive operation: hash-based skip detection, directory naming, calls metadata/output/catalog | `archive`, `sanitize_filename`, `compute_file_hash`, `generate_title_from_content` |
| `cli.py` | Typer app definition, seven verb commands, stdin JSON handling, project defaults merging | `main` (rewritten as Typer app) |

### Data Flow: Single Archive

```
cli.py::archive_cmd()
  │
  ├─ discovery.resolve_transcript(stdin_json | --transcript/--session-id | auto)
  │    → (transcript_path, session_id)
  │
  ├─ discovery.load_project_defaults(project_root)
  │    → defaults dict (tags, purpose, three_ps_context, target)
  │
  ├─ archive.run(transcript_path, session_id, merged_opts)
  │    │
  │    ├─ metadata.parse_transcript(transcript_path)
  │    │    → stats, artifacts, relationship_hints
  │    │
  │    ├─ metadata.create_session_metadata(stats, three_ps, tags, purpose)
  │    │    → session_metadata dict
  │    │
  │    ├─ output.generate_all(transcript_path, output_dir, title)
  │    │    → index.html, conversation.md, conversation.pdf
  │    │
  │    └─ catalog.write_all(archive_dir, output_dir, session_metadata, transcript_path)
  │         → session.meta.json, .session_manifest.json, CATALOG.json
  │
  └─ print result
```

### Storage: Orphan Branch Strategy

Transcripts live on a `transcripts` orphan branch, mounted as `.ai-transcripts/` via `git worktree add`. This branch has no common history with `main` and never merges into working branches.

```
repo/
├── .ai-transcripts/          ← git worktree (transcripts branch)
│   ├── 2026-04-12-session-title/
│   │   ├── index.html
│   │   ├── conversation.md
│   │   ├── conversation.pdf
│   │   ├── session.meta.json
│   │   └── raw-transcript.jsonl
│   ├── .session_manifest.json
│   └── CATALOG.json
├── .claude/
│   └── transcript-defaults.json
├── .gitignore                 ← includes .ai-transcripts/
└── (working tree)
```

When `--target` is set to `main` or `here`, archives write to `ai_transcripts/` in the specified worktree instead. The `--target` flag and `transcript-defaults.json` `target` field control this.

### Worktree-Aware Discovery

`discovery.py` resolves all project paths for a given repo:

1. `git worktree list --porcelain` → all worktree absolute paths
2. Each path mapped through `_encode_cc_path` → `~/.claude/projects/{encoded}/` directories
3. Each projects directory scanned for `*.jsonl` session files
4. Results aggregated — a session discovered under any worktree path belongs to this repo

This shared pipeline feeds `status`, `bulk`, `clean`, and `update`.

### Project Defaults Contract

`.claude/transcript-defaults.json`:

```json
{
  "tags": ["idw2025", "fieldwork"],
  "purpose": "Archaeological survey data processing",
  "three_ps_context": {
    "prompt": "Part of the Adelaide archaeological digitisation project",
    "process": "Claude Code used for data pipeline development",
    "provenance": "Sessions feed into methods chapter of PhD thesis"
  },
  "target": "branch"
}
```

All fields optional. CLI flags override any default. `three_ps_context` provides project-level framing for per-session Three Ps — used by `/transcript` skill for pre-filling and by auto-archive for `needs_review` sessions.

## Decision Record

### DR1: Typer over argparse
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** If Typer's click dependency causes conflicts with other project dependencies.

**Decision:** We chose Typer over stdlib argparse for the CLI framework.

**Consequences:**
- **Enables:** Clean subcommand definition with type annotations, automatic help generation, less boilerplate for seven distinct verbs.
- **Prevents:** Zero-dependency CLI. Adds `typer` (and transitive `click`) to the dependency tree.

**Alternatives considered:**
- **argparse with subparsers:** Rejected because seven subcommands with distinct argument sets produce verbose, hard-to-maintain parser configuration. Was the initial v2 proposal before verb count grew.

### DR2: Seven distinct verbs over flag-modal single command
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** If users find the verb surface too large for discoverability.

**Decision:** We chose seven single-purpose verbs (`init`, `archive`, `bulk`, `status`, `update`, `clean`, `regenerate`) over a single `archive` command with modal flags.

**Consequences:**
- **Enables:** Each verb does one thing. No `--retitle` / `--force` flag soup. Clear mental model.
- **Prevents:** Single-command invocation for combined operations (e.g. update metadata AND regenerate in one call).

**Alternatives considered:**
- **Single `archive` verb with flags:** Rejected because five distinct use cases (archive, bulk, status, update, clean) were being overloaded onto one command. The flag combinations were becoming incoherent.
- **Four verbs (archive, status, update, regenerate):** Rejected because `bulk` and `clean` have distinct enough semantics to warrant their own verbs rather than being modes of `archive` or `update`.

### DR3: Orphan branch storage over directory-based storage
**Status:** Accepted
**Confidence:** Medium
**Reevaluation triggers:** If managing the orphan branch worktree proves too fragile (accidental `git worktree prune`, user confusion about the mount). If Windows git worktree support is unreliable.

**Decision:** We chose a dedicated `transcripts` orphan branch mounted at `.ai-transcripts/` over storing transcripts in `ai_transcripts/` within working branches.

**Consequences:**
- **Enables:** Single canonical storage location across all worktrees. No transcript scatter. Transcripts versioned in git but never polluting feature branches. Branch can be pushed for backup/sharing.
- **Prevents:** Simple `ls ai_transcripts/` discovery from within any branch. Requires `init` setup step. Adds git worktree management complexity.

**Alternatives considered:**
- **Always write to main worktree:** Rejected because archiving from a worktree writes to a directory the user isn't looking at. Confusing.
- **Per-worktree directories:** Rejected because this is the current behaviour and causes the scatter/duplication problem that motivated v2.

### DR4: Stop hook only over UserPromptSubmit + Stop
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** If users need to capture metadata during a session rather than after.

**Decision:** We chose a Stop-hook-only approach (auto-archive everything, curate later) over a UserPromptSubmit hook that prompts for metadata mid-session.

**Consequences:**
- **Enables:** Simpler hook system. No mid-session interruptions. Every session captured automatically. Curation is a separate, intentional act via `update` or `/transcript`.
- **Prevents:** Capturing metadata while context is freshest (during the session). Three Ps must be filled in after the fact.

**Alternatives considered:**
- **UserPromptSubmit hook with threshold:** Rejected because injecting metadata prompts mid-session is intrusive, the implementation is complex (turn counting, stashing), and the simplified approach captures everything anyway.

### DR5: Sidecars as source of truth, indexes as regenerable caches
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** If index regeneration from sidecars becomes too slow for large archive collections (hundreds of sessions).

**Decision:** We chose `session.meta.json` sidecars as the authoritative record, with `CATALOG.json` and `.session_manifest.json` as regenerable indexes.

**Consequences:**
- **Enables:** Merge conflicts in index files become trivially resolvable (delete + `clean`). Each archive directory is self-contained. No single point of failure.
- **Prevents:** Fast lookups without reading individual sidecars. Must rebuild index to get aggregate view.

**Alternatives considered:**
- **Index files as source of truth:** Rejected because index merge conflicts in auto-generated JSON are painful and error-prone (the direct motivation for this decision).

## Existing Patterns

Investigation of the current codebase and Adela's PR (#2, now merged) identified:

- **`_encode_cc_path` helper** (from PR #2): Cross-platform path encoding that handles Windows drive letters, backslashes, and forward slashes. Moves to `discovery.py` as a foundational utility.
- **`encoding="utf-8"` discipline** (from PR #2): All `read_text()`/`write_text()` calls require explicit encoding. v2 carries this forward in all modules.
- **Test patterns using `tmp_path`** (from PR #2): Tests use pytest's `tmp_path` fixture instead of hardcoded POSIX paths. v2 tests follow this pattern from day one.
- **Session metadata schema** (v1): `session.meta.json` structure with `archive`, `session`, `three_ps`, `artifacts`, `relationships` top-level keys. v2 preserves this schema, adding `tags` and `purpose` fields that exist in the schema but are never populated in v1.
- **CATALOG.json structure** (v1): Aggregate index with `sessions` array and `needs_review_count`. v2 preserves this structure.
- **HTML generation via `claude-code-transcripts`** (v1): External package handles transcript-to-HTML conversion. v2 continues to delegate to this package in `output.py`.

No divergence from existing patterns. v2 decomposes and extends; it does not change established conventions.

## Implementation Phases

<!-- START_PHASE_1 -->
### Phase 1: Project Setup and Typer Migration
**Goal:** Update project configuration for Python >=3.12, add Typer dependency, create module skeleton with empty files.

**Components:**
- `pyproject.toml` — bump `requires-python` to `>=3.12`, add `typer` dependency, update classifiers, update entry point to new Typer app
- Module skeleton: create empty `discovery.py`, `metadata.py`, `output.py`, `catalog.py`, `archive.py` under `src/claude_transcript_archive/`
- Ruff config: update `target-version` to `py312`

**Dependencies:** None (first phase)

**Done when:** `uv sync` succeeds, `uv run ruff check .` clean, package installs with Typer available
<!-- END_PHASE_1 -->

<!-- START_PHASE_2 -->
### Phase 2: Module Decomposition
**Goal:** Extract functions from `cli.py` monolith into the five new modules. Preserve all existing behaviour. Migrate tests to match.

**Components:**
- `discovery.py` — `_encode_cc_path`, `get_cc_project_path`, `get_archive_dir`, `get_project_dir_from_transcript`, `auto_discover_transcript`
- `metadata.py` — `extract_session_stats`, `estimate_cost`, `extract_artifacts`, `get_file_type`, `detect_relationship_hints`, `find_plan_files`, `create_session_metadata`, `_is_ide_context_message`
- `output.py` — `generate_conversation_markdown`, `generate_conversation_html_for_pdf`, `generate_conversation_pdf`, `update_html_titles`, `extract_conversation_messages`, `sanitize_for_pdf`, `format_tool_summary`, constants (`PDF_PREAMBLE`, `SPEAKER_LUA_FILTER`)
- `catalog.py` — `load_manifest`, `save_manifest`, `load_catalog`, `save_catalog`, `update_catalog`, `write_metadata_sidecar`, path helpers
- `archive.py` — `archive`, `sanitize_filename`, `compute_file_hash`, `generate_title_from_content`, `log_error`, `log_info`
- `cli.py` — reduced to Typer app with `archive` verb only (replicating current `main()` behaviour through new modules)
- `tests/` — split `test_cli.py` into `test_discovery.py`, `test_metadata.py`, `test_output.py`, `test_catalog.py`, `test_archive.py`, `test_cli.py`

**Dependencies:** Phase 1 (module skeleton exists)

**Done when:** All existing tests pass against decomposed modules. No functionality change — pure structural refactor. `archive` verb works identically to v1 `main()`.
<!-- END_PHASE_2 -->

<!-- START_PHASE_3 -->
### Phase 3: Discovery — Worktree Resolution and Project Defaults
**Goal:** Add worktree-aware session discovery and project defaults loading to `discovery.py`.

**Components:**
- `discovery.py` — `resolve_worktrees()` (calls `git worktree list --porcelain`, returns list of worktree paths), `discover_sessions()` (maps worktree paths through `_encode_cc_path`, scans `~/.claude/projects/` directories for `*.jsonl`), `load_project_defaults()` (reads `.claude/transcript-defaults.json`)
- `tests/test_discovery.py` — tests for worktree resolution (mocked git output), session discovery across multiple project paths, project defaults loading/merging

**Dependencies:** Phase 2 (discovery module populated)

**Done when:** `discover_sessions()` returns all session JONLs across all worktrees for a repo. `load_project_defaults()` reads and validates the defaults file. Tests cover cross-platform paths, missing defaults, malformed defaults.
<!-- END_PHASE_3 -->

<!-- START_PHASE_4 -->
### Phase 4: Init Verb
**Goal:** `init` command sets up orphan branch, worktree mount, hooks, and project defaults in a single idempotent operation.

**Components:**
- `cli.py` — `init` Typer command
- `cli.py` init command body — orchestration for branch creation (`git switch --orphan transcripts`), worktree mount (`git worktree add .ai-transcripts transcripts`), `.gitignore` update, Stop hook installation in `.claude/settings.local.json`, interactive prompting for `.claude/transcript-defaults.json` content. Init logic lives in `cli.py` because it is setup commands (git, file writes), not business logic warranting a separate module.
- `tests/test_cli.py` — tests for init idempotency (run twice, no error), each setup step individually, partial state recovery (branch exists but not mounted)

**Dependencies:** Phase 3 (project defaults loading)

**Done when:** Running `init` in a git repo creates the orphan branch, mounts it, installs the hook, and writes defaults. Running it again is a no-op. Tests verify each step and idempotency.
<!-- END_PHASE_4 -->

<!-- START_PHASE_5 -->
### Phase 5: Status and Bulk Verbs
**Goal:** `status` reports on session state across worktrees. `bulk` archives all unarchived substantial sessions.

**Components:**
- `cli.py` — `status` and `bulk` Typer commands
- `metadata.py` — `classify_session()` (trivial vs substantial based on assistant message count and token threshold)
- `catalog.py` — `find_needs_review()` (scan sidecars for `needs_review: true`)
- `tests/test_cli.py` — integration tests for `status` human and `--json` output, `bulk` archiving multiple sessions

**Dependencies:** Phase 3 (worktree-aware discovery), Phase 4 (init for branch storage)

**Done when:** `status` correctly reports transcribed/untranscribed/trivial counts across worktrees with both human and JSON output. `bulk` archives all unarchived substantial sessions, skips trivial ones. Tests cover multi-worktree scenarios.
<!-- END_PHASE_5 -->

<!-- START_PHASE_6 -->
### Phase 6: Update and Regenerate Verbs
**Goal:** `update` modifies metadata on existing archives. `regenerate` re-renders output files.

**Components:**
- `cli.py` — `update` and `regenerate` Typer commands
- `archive.py` — `update_metadata()` (modify Three Ps, tags, purpose, title on an existing archive's sidecar, then refresh indexes), `regenerate_outputs()` (re-run output generation from raw transcript)
- `catalog.py` — `rebuild_indexes()` (regenerate `.session_manifest.json` and `CATALOG.json` from sidecars — also used by `clean`)
- `tests/test_archive.py` — tests for metadata update, index refresh, regeneration from existing archive

**Dependencies:** Phase 2 (archive/catalog modules), Phase 5 (needs_review tracking)

**Done when:** `update` can modify metadata on a single session or batch-update all `needs_review` sessions. `regenerate` re-renders HTML/PDF/markdown. Index files stay consistent. Tests cover single and batch operations.
<!-- END_PHASE_6 -->

<!-- START_PHASE_7 -->
### Phase 7: Clean Verb
**Goal:** `clean` deduplicates archives, migrates old `ai_transcripts/` directories into the branch, and repairs index files.

**Components:**
- `cli.py` — `clean` Typer command with `--dry-run` (default) and `--execute` flags. Dry-run default is a deliberate deviation from unix convention: `clean` touches indexes and migrates archives across directories, so the safe default is to report without mutating. Users must opt in to changes with `--execute`.
- `archive.py` — `find_duplicates()` (detect same session ID archived under different paths), `migrate_legacy()` (move archives from `ai_transcripts/` to `.ai-transcripts/`)
- `catalog.py` — `rebuild_indexes()` (shared with `regenerate`, reconstructs CATALOG.json and .session_manifest.json from sidecars)
- `tests/test_archive.py` — tests for duplicate detection, legacy migration, index repair, dry-run vs execute modes

**Dependencies:** Phase 6 (rebuild_indexes shared)

**Done when:** `clean --dry-run` reports duplicates, legacy archives, and index staleness without changing anything. `clean --execute` fixes all reported issues. Tests cover each repair type and the dry-run safety.
<!-- END_PHASE_7 -->

<!-- START_PHASE_8 -->
### Phase 8: Tags, Purpose, and Integration Polish
**Goal:** Wire `--tags` and `--purpose` through all relevant verbs. End-to-end integration test of the full workflow.

**Components:**
- `cli.py` — add `--tags` and `--purpose` parameters to `archive`, `bulk`, `update` commands
- `metadata.py` — ensure tags and purpose flow into `session.meta.json` fields
- `tests/test_integration.py` — end-to-end test: `init` → `archive` → `status` → `update` → `regenerate` → `clean` using a temp git repo with worktrees

**Dependencies:** All prior phases

**Done when:** Tags and purpose populate in `session.meta.json`. Full `init` → `archive` → `status` → `update` → `regenerate` → `clean` workflow passes in integration test. All existing and new tests pass.
<!-- END_PHASE_8 -->

## Additional Considerations

**Mount recovery:** If `.ai-transcripts/` is absent when `archive` runs (e.g. after `git worktree prune`), the archive command reads `.claude/transcript-defaults.json` from the main worktree to determine target. If target is `branch` and the `transcripts` branch exists, it re-mounts automatically. If the branch doesn't exist, it fails loudly (non-zero exit, error to stderr). The Stop hook must never silently discard a transcript.

**Claude Code path encoding stability:** `_encode_cc_path` is reverse-engineered from observed behaviour (PR #2), not from a documented API. If Claude Code changes its encoding scheme, `discover_sessions()` will silently find zero sessions. This is a known risk with no mitigation beyond monitoring `status` output for unexpected drops.

**Cross-platform support:** Adela's PR (#2) established the pattern — `_encode_cc_path` handles Windows paths, all file I/O uses `encoding="utf-8"`. v2 inherits this discipline. Windows git worktree support should be validated during Phase 3, as `git worktree list --porcelain` output format may differ.

**Scope note — two remaining Windows bugs from PR #2:** `extract_artifacts`/`make_relative` produces backslash paths where tests expect forward slash (needs `.as_posix()` normalisation). `get_project_dir_from_transcript` assumes POSIX-encoded paths. Both should be fixed during Phase 2 decomposition.

**Implementation scoping:** This design has exactly 8 phases, at the hard limit. If scope grows during implementation, consider splitting into two implementation plans rather than exceeding 8.
