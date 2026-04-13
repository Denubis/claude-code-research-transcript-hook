# Test Requirements: transcript-archive-v2

## Automated Tests

### AC1.1: Each module imports independently without circular dependencies
- **Type:** unit
- **File:** tests/test_cli.py
- **Verifies:** Each of the 5 new modules (discovery, metadata, output, catalog, archive) can be imported independently without ImportError or circular dependency failures
- **Phase:** Phase 2 (Task 8)

### AC1.2: All v1 tests pass against decomposed modules with no functionality change
- **Type:** integration
- **File:** tests/test_discovery.py, tests/test_metadata.py, tests/test_output.py, tests/test_catalog.py, tests/test_archive.py, tests/test_cli.py
- **Verifies:** Total test count >= 93 (v1 count) after splitting tests across per-module files; all existing test logic passes unchanged against decomposed modules
- **Phase:** Phase 2 (Task 7, Task 8)

### AC1.3: Importing a function from the wrong module raises ImportError
- **Type:** unit
- **File:** tests/test_cli.py
- **Verifies:** At least 3 representative functions (e.g., get_cc_project_path, extract_session_stats, load_catalog) are not re-exported from cli.py; importing them from cli raises ImportError
- **Phase:** Phase 2 (Task 8)

### AC2.1: Each of the seven verbs is callable and produces --help output
- **Type:** e2e
- **File:** tests/test_integration.py
- **Verifies:** Running each verb (init, archive, bulk, status, update, clean, regenerate) with --help exits 0 and produces non-empty output
- **Phase:** Phase 8 (Task 2)

### AC2.2: archive with --tags and --purpose stores both fields in session.meta.json
- **Type:** integration
- **File:** tests/test_integration.py
- **Verifies:** Archiving with `--tags foo --tags bar --purpose "testing"` produces a session.meta.json containing `"tags": ["foo", "bar"]` and `"purpose": "testing"`; CLI flags override project defaults
- **Phase:** Phase 8 (Task 1)

### AC2.3: status --json outputs valid JSON with session counts and per-session details
- **Type:** integration
- **File:** tests/test_integration.py
- **Verifies:** `status --json` output parses as valid JSON and contains archived/unarchived arrays and total count
- **Phase:** Phase 8 (Task 2)

### AC2.4: Calling a verb with invalid flags produces a Typer error message, not a traceback
- **Type:** e2e
- **File:** tests/test_integration.py
- **Verifies:** Invalid flag invocation exits non-zero, stderr contains "Error" or "Usage", and stderr does not contain "Traceback (most recent call last)"
- **Phase:** Phase 8 (Task 2)

### AC2.5: archive with no arguments reads stdin JSON and produces identical output to v1
- **Type:** integration
- **File:** tests/test_integration.py
- **Verifies:** Piping JSON with transcript_path and session_id via stdin to `archive` produces the same output file set (index.html, conversation.md, session.meta.json, raw-transcript.jsonl) as v1 main() for the same input
- **Phase:** Phase 2 (Task 7), Phase 8 (Task 2)

### AC3.1: init creates an orphan transcripts branch with no common ancestor to main
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** After init in a tmp_path git repo, `git branch --list transcripts` returns the branch, and `git merge-base main transcripts` fails (no common ancestor)
- **Phase:** Phase 4 (Task 1, Task 4)

### AC3.2: After init, .ai-transcripts/ is a mounted worktree and appears in .gitignore
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** After init, `.ai-transcripts/` is a directory, `git worktree list` includes it, and `.gitignore` contains the `.ai-transcripts/` entry
- **Phase:** Phase 4 (Task 1, Task 4)

### AC3.3: archive writes output files into .ai-transcripts/ when target is branch
- **Type:** integration
- **File:** tests/test_integration.py
- **Verifies:** After init + archive, output files (index.html, conversation.md, session.meta.json) exist under `.ai-transcripts/`, not `ai_transcripts/`
- **Phase:** Phase 8 (Task 2)

### AC3.4: init run twice is idempotent
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** Running init twice produces no error, .gitignore has exactly one `.ai-transcripts/` entry, orphan branch is not recreated, worktree is not re-added
- **Phase:** Phase 4 (Task 4)

### AC4.1: status in a repo with 2+ worktrees reports sessions from all worktrees
- **Type:** integration
- **File:** tests/test_discovery.py, tests/test_cli.py
- **Verifies:** discover_sessions() with mocked multi-worktree git output returns sessions from all worktree paths; status command reports all discovered sessions
- **Phase:** Phase 3 (Task 1, Task 2), Phase 5 (Task 2)

### AC4.2: Same session ID under different worktree paths is recognised as one session
- **Type:** unit
- **File:** tests/test_discovery.py
- **Verifies:** discover_sessions() with the same session_id JSONL placed under two different encoded project paths returns the session only once
- **Phase:** Phase 3 (Task 2)

### AC4.3: status in a non-git directory produces a clear error, not a traceback
- **Type:** unit
- **File:** tests/test_discovery.py
- **Verifies:** resolve_worktrees() called in a non-git directory raises a descriptive error (not CalledProcessError or bare traceback)
- **Phase:** Phase 3 (Task 1)

### AC5.1: Stop hook invocation archives with needs_review: true when no Three Ps provided
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** Archiving via bulk (simulating hook behaviour) without --prompt/--process/--provenance produces session.meta.json with `needs_review: true`
- **Phase:** Phase 5 (Task 3)

### AC5.2: Project defaults from transcript-defaults.json are applied when no CLI flags override
- **Type:** unit
- **File:** tests/test_discovery.py
- **Verifies:** load_project_defaults() reads tags, purpose, three_ps_context, and target from `.claude/transcript-defaults.json`; returns empty dict when file is missing; returns empty dict on malformed JSON
- **Phase:** Phase 3 (Task 3)

### AC5.3: Sessions below trivial threshold are archived with trivial: true and needs_review: true
- **Type:** unit + integration
- **File:** tests/test_metadata.py (classify_session unit tests), tests/test_cli.py (bulk integration)
- **Verifies:** classify_session() returns "trivial" for sessions with < 5 assistant messages; bulk archive marks trivial sessions with both `trivial: true` and `needs_review: true` in metadata
- **Phase:** Phase 5 (Task 1, Task 3)

### AC6.1: Deleting CATALOG.json and running clean regenerates it correctly
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** After creating 3 archive directories with sidecars, deleting CATALOG.json, and running `clean --execute`, CATALOG.json is regenerated with 3 sessions and correct needs_review_count
- **Phase:** Phase 7 (Task 2)

### AC6.2: Deleting .session_manifest.json and running clean regenerates it correctly
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** After deleting .session_manifest.json and running `clean --execute`, manifest is regenerated with correct session-to-directory mappings
- **Phase:** Phase 7 (Task 2)

### AC6.3: clean --dry-run reports but modifies no files
- **Type:** integration
- **File:** tests/test_cli.py
- **Verifies:** After deleting index files, running `clean` (dry-run default) reports what would be repaired but the deleted files remain absent; no file modification timestamps change
- **Phase:** Phase 7 (Task 2)

## Human Verification

### AC1.4: Package installs and runs on Python 3.12 (minimum) and 3.13+
- **Why not automated:** Requires testing across multiple Python runtime versions. No CI infrastructure currently exists; local runs use a single interpreter.
- **Verification approach:** Manually install and run `claude-transcript-archive --help` and `uv run pytest tests/ -q` under both Python 3.12 and 3.13. Convert to CI matrix job if CI is added later.

### AC2.5 (partial): Output is identical to v1 for the same input
- **Why not automated:** "Identical output" for HTML depends on the external `claude-code-transcripts` package and pandoc/lualatex for PDF, which may vary with version or system configuration. Automated tests cover structural equivalence (same files, same metadata schema).
- **Verification approach:** Run v1 and v2 against the same sample transcript. Diff session.meta.json (structurally identical except new empty fields). Visually inspect HTML and PDF for regressions.

### AC3.2 (partial): Hook installation is correct at runtime
- **Why not automated:** The hook command `claude-transcript-archive archive --quiet` is invoked by the Claude Code runtime, not by this project's test suite. File-content assertion (hook JSON present) is automated; runtime invocation is not.
- **Verification approach:** After `init`, start and stop a Claude Code session. Verify a new archive appears in `.ai-transcripts/`.

### AC5.1 (partial): Stop hook invocation triggers archive at runtime
- **Why not automated:** The Stop hook is fired by the Claude Code runtime when a session ends. The test suite simulates the behaviour (piping JSON to archive) but cannot verify Claude Code correctly invokes the hook.
- **Verification approach:** After `init`, run a real Claude Code session. On session stop, verify `.ai-transcripts/` contains a new archive with `needs_review: true`.
