# claude-code-research-transcript-hook

Archive Claude Code conversations with research-grade metadata using the IDW2025 reproducibility framework (Three Ps: Prompt/Process/Provenance).

## Project Structure

```text
src/claude_transcript_archive/cli.py  # Main CLI implementation
claude-commands/transcript.md         # Interactive /transcript command
example-hooks/settings.local.json     # Auto-archive hook config
```

## How It Works

1. Claude Code hooks provide `{"transcript_path": "...", "session_id": "..."}` via JSON on stdin
2. Script extracts rich metadata (tokens, costs, tool calls, artifacts, relationships)
3. Generates HTML using `claude-code-transcripts`
4. Archives organized as `YYYY-MM-DD-title-slug/` directories
5. Metadata sidecar files (`session.meta.json`) stored in archive AND next to original
6. CATALOG.json indexes all sessions with completion status

### Output Files (when using /transcript)

When archiving via the interactive `/transcript` command:

- `index.html` - Full HTML transcript with expandable tool details
- `conversation.md` - Readable markdown showing user/assistant exchange
- `conversation.pdf` - Styled PDF with colored speaker turn borders (requires pandoc + lualatex)
- `session.meta.json` - Complete metadata including Three Ps
- `raw-transcript.jsonl` - Original transcript backup

## CLI Usage

```bash
claude-research-transcript [OPTIONS]

--title TITLE          # Title for the transcript
--retitle              # Force regenerate title/rename directory
--force                # Regenerate even if unchanged
--local                # Archive to ./ai_transcripts/
--output DIR           # Custom output directory
--quiet                # Suppress error messages
--transcript PATH      # Path to transcript file (alternative to stdin)
--session-id ID        # Session ID (alternative to stdin)

# Three Ps metadata (when all provided, sets needs_review=false)
--prompt TEXT          # Prompt summary (what was asked)
--process TEXT         # Process summary (how tool was used)
--provenance TEXT      # Provenance summary (role in research)
```

**Input modes:**

- **Hook mode (default):** Receives JSON via stdin from Claude Code hooks
- **Manual mode:** Use `--transcript` and `--session-id` together for direct invocation
- **Interactive mode:** The `/transcript` command gathers Three Ps interactively

## Archive Locations

- **Default (global):** `~/.claude/transcripts/{project-path}/`
- **Local (`--local`):** `./ai_transcripts/`
- **Custom (`--output`):** Any directory

## IDW2025 Three Ps Framework

- **Prompt**: What was asked/needed
- **Process**: How the tool was used
- **Provenance**: Role in research workflow

Sessions from hooks are marked `needs_review: true`. Run `/transcript` to complete metadata interactively, which passes `--prompt`, `--process`, and `--provenance` to mark the session as fully reviewed.

## Installation

```bash
# Global install
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook

# Per-repo
uvx --from git+https://github.com/Denubis/claude-code-research-transcript-hook claude-research-transcript --local
```

Copy slash command:

```bash
cp claude-commands/transcript.md ~/.claude/commands/
```

## Dependencies

- Python 3.10+
- `claude-code-transcripts` (installed automatically)
- `pandoc` + `lualatex` (optional, for PDF generation)

## Commands

```bash
# Build/install locally
uv tool install . --force

# Test with a transcript
echo '{"transcript_path": "/path/to/transcript.jsonl", "session_id": "abc123"}' | claude-research-transcript --title "Test"
```
