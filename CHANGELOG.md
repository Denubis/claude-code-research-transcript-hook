# Changelog

## transcript-archive 0.6.0

`status` now lists the work it found, and generated archive files are pre-commit-clean on write.

**Added:**
- `status` plain-text output now lists every unarchived session id (with `substantial` / `trivial` classification) and every archived session id whose `needs_review` is true, each followed by the exact follow-up command. Counts remain the header. `--json` output is unchanged.
- `archive.normalise_text_outputs(dir)` runs at the end of every `archive` and `regenerate` to strip trailing whitespace and collapse trailing newlines on `.md`, `.html`, `.json`, `.jsonl`, `.title`, and `.last_size` files. Matches `pre-commit-hooks` `trailing-whitespace` + `end-of-file-fixer` so in-tree archives (`target: here`) no longer bounce commits on every release.

**Changed:**
- `skills/transcript/SKILL.md` rewritten to describe every verb (what it does, what it prints, what it changes), with explicit "find unarchived" and "iterate needs-review" recipes pointing at `status` and `update`. Worktree coverage and the per-repo limit are now stated up front.

## transcript-archive 0.5.0

Windows portability release. Closes the remaining locale- and line-ending-dependent gaps so Windows contributors (notably @adivea, who has been flagging and fixing these) can pull and run without local tweaks.

**Fixed:**
- `_encode_cc_path` now also normalises `_` to `-`. Claude Code rewrites underscores in project slugs (a `shifted_base` directory becomes `shifted-base` under `~/.claude/projects/`), so without this, auto-discovery silently misses any project folder containing an underscore. ([#3](https://github.com/Denubis/claude-code-research-transcript-hook/pull/3) — Adela Sobotkova)
- Every `subprocess.run(..., text=True, ...)` call in `cli.py`, `archive.py`, `discovery.py`, and `output.py` now also pins `encoding="utf-8"`. On Windows cp1252 locales the previous code raised `UnicodeDecodeError` the moment a repo path contained a non-ASCII character.
- `init()` step 4 now reads and appends to `.gitignore` with `encoding="utf-8"`. The only remaining locale-dependent text I/O in the package; matches the rest of the codebase.

**Added:**
- `.gitattributes` with `* text=auto eol=lf` plus binary marks for common image/PDF extensions, so Windows checkouts no longer introduce CRLF churn that has to be reverted before any commit.
- `TestEncodeCCPath` regression cases for underscore-to-hyphen normalisation (POSIX + Windows literals, plus a "no `_` may leak through" assertion).

## transcript-archive 0.4.1

Patch release restoring the documented `claude-research-transcript` binary name, widening transcript auto-discovery across worktrees and git roots, and refreshing every doc surface that referenced the pre-v2 single-command CLI shape.

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
