# claude-code-research-transcript-hook

Archive Claude Code conversations with research-grade metadata using the IDW2025 reproducibility framework (Three Ps: Prompt/Process/Provenance).

## Project Structure

```text
.claude-plugin/marketplace.json           # Marketplace catalog (self-referencing)
.claude-plugin/plugin.json                # Plugin manifest
commands/transcript.md                    # /transcript slash command
skills/transcript/SKILL.md                # Transcript archive skill
example-hooks/settings.local.json         # Auto-archive hook config
src/claude_transcript_archive/
    __main__.py                           # `python -m` entry point
    cli.py                                # Typer app, subcommand dispatch
    archive.py                            # Archive orchestration, skip detection
    discovery.py                          # Worktree-aware transcript discovery
    catalog.py                            # Manifest and CATALOG.json management
    metadata.py                           # JSONL parsing, token/cost extraction
    output.py                             # HTML, markdown, PDF generation
```

## How It Works

1. Claude Code hooks invoke `claude-research-transcript archive` and pipe `{"transcript_path": "...", "session_id": "..."}` via JSON on stdin
2. The `archive` subcommand extracts rich metadata (tokens, costs, tool calls, artifacts, relationships)
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
claude-research-transcript <subcommand> [OPTIONS]

# Subcommands: archive (single-session archive, what hooks call),
#              init, status, bulk, update, regenerate, clean.
# See skills/transcript/SKILL.md for the full reference.

# Flags below apply to `archive`:
--title TITLE          # Title for the transcript
--retitle              # Force regenerate title/rename directory
--force                # Regenerate even if unchanged (see below)
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

### Auto-discovery

When `archive` runs with no stdin JSON and no `--transcript`/`--session-id`, it searches `~/.claude/projects/<slug>/` for cwd, the enclosing git root, and every `git worktree list` path, then picks the most-recent `.jsonl` by mtime. If nothing is found, the error lists every slug scanned and names `--transcript PATH --session-id UUID` as the explicit escape hatch.

## Archive Locations

- **Default (global):** `~/.claude/transcripts/{project-path}/`
- **Local (`--local`):** `./ai_transcripts/`
- **Custom (`--output`):** Any directory

## Silent-Clobber Protection

Two distinct mechanisms guard against the data-loss bug where multiple sessions landed in the same archive directory:

- **Directory-collision auto-suffix.** When two distinct session UUIDs would sanitise to the same directory name (e.g. their first user messages are identical Claude Code boilerplate envelopes), the second session gets `-<first-8-uuid-chars>` appended. A `Warning:` line on stderr names both sides. Same-UUID re-archive still reuses its directory.
- **Manifest-pointer protection.** When the manifest already points at an archive directory whose `session.meta.json` has non-empty Three Ps, and the incoming run supplies none, `archive()` refuses to repoint the manifest, ignores `--force`/`--retitle`, and preserves the curated Three Ps in the regenerated metadata. Pass `--prompt`/`--process`/`--provenance` to overwrite intentionally.

## IDW2025 Three Ps Framework

- **Prompt**: What was asked/needed
- **Process**: How the tool was used
- **Provenance**: Role in research workflow

Sessions from hooks are marked `needs_review: true`. Run `/transcript` to complete metadata interactively, which passes `--prompt`, `--process`, and `--provenance` to mark the session as fully reviewed.

## Installation

```bash
# As a Claude Code plugin (includes /transcript command and skill)
/plugin marketplace add Denubis/claude-code-research-transcript-hook
/plugin install transcript-archive@transcript-archive-marketplace

# CLI tool only (global)
uv tool install git+https://github.com/Denubis/claude-code-research-transcript-hook

# CLI tool only (per-repo, no install)
uvx --from git+https://github.com/Denubis/claude-code-research-transcript-hook claude-research-transcript archive --local
```

## Dependencies

- Python 3.12+
- `claude-code-transcripts` (installed automatically)
- `pandoc` + `lualatex` (optional, for PDF generation)

## Commands

```bash
# Build/install locally
uv tool install . --force

# Test with a transcript
echo '{"transcript_path": "/path/to/transcript.jsonl", "session_id": "abc123"}' | claude-research-transcript archive --title "Test"
```
