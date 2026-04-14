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

## Quick Reference

**Command:** `/transcript` or `/transcript <session-uuid>`

**Outputs generated:**
| File | Description |
|------|-------------|
| `SUMMARY.md` | Human-readable summary with Three Ps and session stats |
| `index.html` | Full HTML transcript with expandable tool details |
| `conversation.md` | Readable markdown showing user/assistant exchange |
| `conversation.pdf` | Styled PDF with colored speaker turn borders |
| `session.meta.json` | Complete metadata including Three Ps |

## Three Ps Framework (IDW2025)

| P | Question | Example |
|---|----------|---------|
| **Prompt** | What was asked/needed? | "Add PDF export to transcript tool" |
| **Process** | How was the tool used? | "Feature-dev skill with code exploration and TDD" |
| **Provenance** | Role in broader context? | "Part of research reproducibility toolkit" |

## CLI Commands

### `claude-research-transcript archive` (default)

Archive a single session with full metadata.

```bash
# Interactive (recommended)
/transcript

# Archive a prior session
/transcript <session-uuid>

# Direct CLI with metadata
claude-research-transcript archive --local --retitle \
  --title "Session Title" \
  --prompt "What was accomplished" \
  --process "How Claude was used" \
  --provenance "Research context" \
  --tags "tag1,tag2" \
  --purpose "Why this session matters"
```

Options: `--title`, `--retitle`, `--force`, `--local`, `--output`, `--quiet`, `--transcript`, `--session-id`, `--prompt`, `--process`, `--provenance`, `--tags`, `--purpose`, `--target (branch|main|here)`

### `claude-research-transcript init`

Set up transcript archiving for a repository. Creates an orphan `transcripts` branch, mounts a worktree at `.ai-transcripts/`, adds it to `.gitignore`, installs a Stop hook, and creates project defaults. Idempotent.

```bash
claude-research-transcript init
claude-research-transcript init --non-interactive
```

### `claude-research-transcript status`

Report session state across worktrees — how many archived, how many need review, how many unarchived (substantial vs trivial).

```bash
claude-research-transcript status
claude-research-transcript status --json
```

### `claude-research-transcript bulk`

Archive all unarchived sessions in one pass. Automatically classifies sessions as substantial or trivial.

```bash
claude-research-transcript bulk
claude-research-transcript bulk --local --tags "sprint-12" --purpose "Sprint 12 work"
```

### `claude-research-transcript update`

Update metadata on existing archived sessions. Use to add Three Ps after bulk archival, change titles, or mark sessions as reviewed.

```bash
# Update a specific session
claude-research-transcript update --session-id UUID \
  --prompt "..." --process "..." --provenance "..."

# Update all sessions needing review
claude-research-transcript update --all-needs-review \
  --tags "project-x" --purpose "Research project"
```

### `claude-research-transcript regenerate`

Re-render output files (HTML, markdown, PDF) from raw transcript backups. Useful after template updates.

```bash
claude-research-transcript regenerate --session-id UUID
claude-research-transcript regenerate --all
```

### `claude-research-transcript clean`

Deduplicate archives, migrate from legacy `ai_transcripts/` directory, and repair indexes. Dry-run by default.

```bash
claude-research-transcript clean              # dry run (report only)
claude-research-transcript clean --execute    # actually apply changes
```

## Installation

```bash
# Install CLI tool
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook

# Install plugin (includes /transcript command and skill)
/plugin marketplace add Denubis/claude-code-research-transcript-hook
/plugin install transcript-archive@transcript-archive-marketplace
```

## Dependencies

- Python 3.12+
- `claude-code-transcripts` (auto-installed)
- `pandoc` + `lualatex` (optional, for PDF generation)
