# Changelog

## Unreleased

**Fixed:**
- Restored documented binary name `claude-research-transcript` as the sole entry point. v0.4.0 had accidentally shipped the binary as `claude-transcript-archive`, drifting from the skill, README, CLAUDE.md, and every documented example. `init` now writes the canonical name into the Stop hook; `archive.py` error messages point at the canonical name.
- Moved `pytest` from `[project].dependencies` to `[project.optional-dependencies].dev` so it no longer ships to end users of the CLI.
- Skill, README, CLAUDE.md, `commands/transcript.md`, and `example-hooks/settings.local.json` no longer emit examples that drop the required `archive` subcommand (which fails under the v0.4.0 Typer CLI).

**Added:**
- `discovery.get_candidate_project_dirs()` — returns the cwd + git repository root + every `git worktree list` path, used by auto-discovery.
- `discovery.get_searched_project_slugs()` — surfaces exactly which `~/.claude/projects/<slug>/` paths were scanned; rendered in the CLI's "No transcript found" error so the user can see what was tried.
- Skill Quick Reference now calls out the raw-CLI `archive` subcommand pattern and documents the worktree-aware auto-discovery behaviour.

**Changed:**
- `discovery.auto_discover_transcript()` now searches the union of all candidate project-id slugs (cwd + git root + worktrees) and returns the most-recent JSONL by mtime. Previously it only searched the cwd slug, which failed silently when a session lived under the parent repo slug while the CLI was invoked from a worktree subdirectory.
- CLI "No transcript found" error now lists the exact project-slug paths searched before giving up, and points at `--transcript PATH --session-id UUID` as the explicit escape hatch.

## transcript-archive 0.4.0

Standalone plugin with full CLI and enriched skill content.

**New:**
- Marketplace and plugin configuration (`.claude-plugin/marketplace.json` + `plugin.json`) for plugin discovery and installation
- UUID support for archiving prior sessions (`/transcript <session-uuid>`)
- SUMMARY.md generation with session statistics after archiving
- Full CLI reference in skill documentation covering all 7 commands: `archive`, `init`, `status`, `bulk`, `update`, `regenerate`, `clean`

**Changed:**
- `/transcript` command now includes `Write` in allowed-tools for SUMMARY.md generation
- Skill description updated to cover bulk archival, status reporting, and metadata updates
- Installation instructions use two-step marketplace add + plugin install
- Minimum Python version raised to 3.12+
