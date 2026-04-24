---
name: transcript
description: Use when archiving Claude Code conversations with research metadata - generates HTML, markdown, PDF exports and SUMMARY.md using IDW2025 Three Ps framework (Prompt/Process/Provenance). Supports current session, prior sessions by UUID, bulk archival, status reporting, and metadata updates.
---

# Transcript Archive Skill

Archive Claude Code conversations with research-grade metadata using the IDW2025 reproducibility framework.

## When to Use

- End of significant coding sessions
- When you want to preserve conversation context for future reference
- When documenting AI-assisted development for research
- When you need readable exports (markdown, PDF) of a conversation
- When checking archive status across worktrees
- When bulk-archiving unprocessed sessions
- When finding sessions that still need Three Ps review

## Quick Reference

**Slash command:** `/transcript` or `/transcript <session-uuid>` — interactive Three Ps gathering, then archive.

**Raw CLI:** `claude-research-transcript <verb> [OPTIONS]`. Every invocation requires a verb; `claude-research-transcript --local` with no verb fails.

**Worktree coverage:** `status`, `bulk`, and auto-discovery walk `git worktree list` for the current repo, mapping each worktree to its `~/.claude/projects/<slug>/` directory. Sessions started in any worktree of this repo are seen by the same verb call. Sessions from *other* repos are not — run the verb from inside that repo.

**Archive location resolution (per repo):**

| `target` in `.claude/transcript-defaults.json` | Archive directory |
|------------------------------------------------|-------------------|
| `branch` (default) | `<repo>/.ai-transcripts/` (orphan worktree) |
| `here` | `<repo>/ai_transcripts/` (in-tree) |
| `main` | `~/.claude/transcripts/<project>/` (global) |

CLI flags `--local` (== `here`) and `--output DIR` override the default per call.

## Outputs Generated

| File | Description |
|------|-------------|
| `SUMMARY.md` | Human-readable summary with Three Ps and session stats |
| `index.html` | Full HTML transcript with expandable tool details |
| `conversation.md` | Readable markdown showing user/assistant exchange |
| `conversation.pdf` | Styled PDF with colored speaker turn borders (needs pandoc + lualatex) |
| `session.meta.json` | Complete metadata including Three Ps |
| `raw-transcript.jsonl` | Original JSONL backup (used by `regenerate`) |

## Three Ps Framework (IDW2025)

| P | Question | Example |
|---|----------|---------|
| **Prompt** | What was asked/needed? | "Add PDF export to transcript tool" |
| **Process** | How was the tool used? | "Feature-dev skill with code exploration and TDD" |
| **Provenance** | Role in broader context? | "Part of research reproducibility toolkit" |

Sessions archived without Three Ps are marked `needs_review: true`. Use `update` (below) to fill them in later.

## Common Recipes

### Find sessions that have not yet been archived

```bash
claude-research-transcript status
```

When the unarchived count is non-zero the plain-text output lists each session id and its classification (`substantial` / `trivial`) and prints the exact follow-up commands. Add `--json` for a machine-readable form that includes full transcript paths.

### Iterate over sessions that need Three Ps review

```bash
claude-research-transcript status              # lists every needs_review session id
claude-research-transcript update --session-id <UUID> \
    --prompt "..." --process "..." --provenance "..."
```

`status` prints a `Needs review:` block whenever any archived session in this repo's archive has `needs_review: true`. Walk the list one id at a time, or batch-set tags/purpose across all of them at once with `update --all-needs-review`.

### Archive everything in one pass

```bash
claude-research-transcript bulk
```

Walks every worktree, archives every unarchived session, classifies trivial sessions automatically (still archived but flagged).

## CLI Verb Reference

### `archive` — archive a single session

Default verb that the Stop hook calls. Without `--transcript`/`--session-id` and without stdin JSON, it auto-discovers the most recent JSONL across cwd, the git root, and every worktree slug under `~/.claude/projects/`.

```bash
# Interactive Three Ps via slash command (recommended)
/transcript

# Archive a prior session by UUID
/transcript <session-uuid>

# Direct CLI with full metadata
claude-research-transcript archive --retitle \
  --title "Session Title" \
  --prompt "What was accomplished" \
  --process "How Claude was used" \
  --provenance "Research context" \
  --tags "tag1,tag2" \
  --purpose "Why this session matters"
```

Options: `--title`, `--retitle`, `--force`, `--local`, `--output DIR`, `--quiet`, `--transcript PATH`, `--session-id UUID`, `--prompt`, `--process`, `--provenance`, `--tags`, `--purpose`, `--target {branch|main|here}`.

### `init` — set up a repo for archiving

Creates an orphan `transcripts` branch, mounts it as a worktree at `.ai-transcripts/`, adds that path to `.gitignore`, installs a `Stop` hook in `.claude/settings.local.json`, and writes `.claude/transcript-defaults.json`. Idempotent.

```bash
claude-research-transcript init
claude-research-transcript init --non-interactive
```

### `status` — list session state across worktrees

Walks every git worktree of the current repo, reports counts (archived / unarchived / needs_review / substantial / trivial), and prints **a list of unarchived session ids with classifications** plus **a list of needs-review session ids** when either is non-empty. Each list is followed by the exact verb invocation to act on it.

```bash
claude-research-transcript status              # human-readable, with lists
claude-research-transcript status --json       # full lists incl. transcript paths
```

### `bulk` — archive every unarchived session at once

Runs `archive` against every session `status` would mark as unarchived. Trivial sessions are still archived but flagged so you can filter them later.

```bash
claude-research-transcript bulk
claude-research-transcript bulk --local --tags "sprint-12" --purpose "Sprint 12 work"
```

### `update` — change metadata on an existing archived session

Use to add Three Ps after a hook-driven archive, retitle, or batch-tag every needs-review session. Rebuilds `CATALOG.json` after.

```bash
# Single session
claude-research-transcript update --session-id <UUID> \
    --prompt "..." --process "..." --provenance "..."

# Every session still flagged needs_review
claude-research-transcript update --all-needs-review \
    --tags "project-x" --purpose "Research project"
```

Options: `--session-id`, `--all-needs-review`, `--title`, `--tags`, `--purpose`, `--prompt`, `--process`, `--provenance`, `--quiet`.

### `regenerate` — re-render outputs from raw JSONL

Re-runs HTML/markdown/PDF generation against `raw-transcript.jsonl` already in the archive. Use after template or generator updates; does not change metadata.

```bash
claude-research-transcript regenerate --session-id <UUID>
claude-research-transcript regenerate --all
```

### `clean` — deduplicate, migrate legacy, repair indexes

Detects duplicate session directories (keeping the newest), migrates from the legacy `ai_transcripts/` location to the configured archive, and rebuilds `.session_manifest.json` + `CATALOG.json`. Dry-run by default; pass `--execute` to apply.

```bash
claude-research-transcript clean              # report only
claude-research-transcript clean --execute    # apply changes
```

## Installation

```bash
# Install plugin (includes /transcript command and skill)
/plugin marketplace add Denubis/claude-code-research-transcript-hook
/plugin install transcript-archive@transcript-archive-marketplace

# CLI tool only
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook
```

## Dependencies

- Python 3.12+
- `claude-code-transcripts` (auto-installed)
- `pandoc` + `lualatex` (optional, for PDF generation)
