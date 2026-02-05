---
name: transcript
description: Use when archiving Claude Code conversations with research metadata - generates HTML, markdown, and PDF exports using IDW2025 Three Ps framework (Prompt/Process/Provenance)
---

# Transcript Archive Skill

Archive Claude Code conversations with research-grade metadata using the IDW2025 reproducibility framework.

## When to Use

- End of significant coding sessions
- When you want to preserve conversation context for future reference
- When documenting AI-assisted development for research
- When you need readable exports (markdown, PDF) of a conversation

## Quick Reference

**Command:** `/transcript`

**Outputs generated:**
| File | Description |
|------|-------------|
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

## CLI Usage

```bash
# Interactive (recommended)
/transcript

# Direct CLI with metadata
claude-research-transcript --local --retitle \
  --title "Session Title" \
  --prompt "What was accomplished" \
  --process "How Claude was used" \
  --provenance "Research context"
```

## Installation

```bash
# Install CLI tool
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook

# Install plugin (includes /transcript command)
/plugin install file:///path/to/claude-code-research-transcript-hook
# Or from marketplace when published
```

## Dependencies

- Python 3.10+
- `claude-code-transcripts` (auto-installed)
- `pandoc` + `lualatex` (optional, for PDF generation)
