# claude-code-research-transcript-hook

Archive [Claude Code](https://docs.anthropic.com/en/docs/claude-code) conversations with research-grade metadata using the **IDW2025 reproducibility framework**.

## Features

- **Research-grade metadata**: Captures the Three Ps (Prompt/Process/Provenance) for reproducibility
- **Rich statistics**: Token counts, costs, tool usage, thinking blocks, artifacts
- **Dual archive modes**: Global (`~/.claude/transcripts/`) or per-project (`./ai_transcripts/`)
- **CATALOG.json**: Central index of all sessions with metadata completion status
- **Plan file archiving**: Automatically captures plan files from planning sessions
- **Interactive `/transcript` command**: Asks clarifying questions to capture context
- **Silent auto-archive**: Hook-based archiving with automatic metadata extraction
- **HTML output**: Browsable transcripts via [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)

## Installation

### Global install (recommended for hooks)

```bash
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook
```

### Per-repo with uvx

```bash
uvx --from git+https://github.com/Denubis/claude-code-research-transcript-hook claude-research-transcript --local
```

### Using pipx

```bash
pipx install git+https://github.com/Denubis/claude-code-research-transcript-hook
```

### From source

```bash
git clone https://github.com/Denubis/claude-code-research-transcript-hook
cd claude-code-research-transcript-hook
uv tool install .
```

## Setup

### 1. Install the `/transcript` slash command (recommended)

Copy the slash command to your Claude commands directory:

```bash
cp claude-commands/transcript.md ~/.claude/commands/
```

Now you can run `/transcript` in any Claude Code session to interactively archive the conversation with research metadata.

### 2. Enable auto-archive for a project (optional)

To automatically archive transcripts when Claude stops, copy the example hook to your project:

```bash
cp example-hooks/settings.local.json /path/to/your/project/.claude/
```

This archives to `./ai_transcripts/` in the project directory. Sessions archived via hooks are flagged as `needs_review: true` for later metadata completion.

## Usage

### Interactive archiving with `/transcript`

In any Claude Code session, run:

```text
/transcript
```

Claude will:

1. Analyze the conversation and draft metadata
2. Ask clarifying questions about context that won't be obvious in 6 months
3. Present the Three Ps (Prompt/Process/Provenance) for your confirmation
4. Archive with complete metadata

### Command-line options

```text
claude-research-transcript [OPTIONS]

Options:
  --title TITLE    Title for the transcript
  --retitle        Force regenerate title/rename directory
  --force          Regenerate even if transcript unchanged
  --local          Archive to ./ai_transcripts/ instead of ~/.claude/transcripts/
  --output DIR     Custom output directory
  --quiet          Suppress error messages

Input: JSON payload on stdin with transcript_path and session_id
       (automatically provided by Claude Code hooks)
```

### Archive locations

- **Global archive** (default): `~/.claude/transcripts/{project-path}/`
- **Project archive** (`--local`): `./ai_transcripts/`
- **Custom** (`--output`): Any directory you specify

## Archive Structure

```text
~/.claude/transcripts/                          # Global archive
├── CATALOG.json                                # Central index
└── -home-user-my-project/                      # Project (CC path encoding)
    ├── CATALOG.json                            # Project index
    └── 2026-01-14-implementing-feature/
        ├── index.html                          # Browsable transcript
        ├── session.meta.json                   # Rich metadata
        ├── raw-transcript.jsonl                # Original transcript
        └── plans/                              # Plan files (if any)
            └── plan-file.md
```

## Metadata Schema (session.meta.json)

Each archived session includes:

- **Session info**: ID, timestamps, duration
- **Project info**: Name, directory
- **Model info**: Provider, model ID
- **Statistics**: Turns, messages, tokens, costs, tool calls, thinking blocks
- **Artifacts**: Files created, modified, referenced
- **Relationships**: Session continuations, references
- **Three Ps**: Prompt summary, process summary, provenance summary
- **Archive info**: Timestamp, file hash, needs_review flag

## The IDW2025 Framework

This tool implements the **Three Ps** framework for research reproducibility:

- **Prompt**: What was the user trying to accomplish? What problem were they solving?
- **Process**: How was Claude Code used? What tools and approaches were employed?
- **Provenance**: What is the role of this work in the broader research context?

Sessions archived via hooks are marked `needs_review: true`. Run `/transcript` to complete the metadata with human-verified context.

## Requirements

- Python 3.10+
- [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) (installed automatically)

## License

MIT
