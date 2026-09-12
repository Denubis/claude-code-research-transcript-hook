# Transcript archive contributor guide

This package converts Claude Code and Codex parent sessions into one deterministic,
searchable Markdown transcript per session.

## Current architecture

- `discovery.py` proves repository membership and finds parent/subagent sources.
- `claude.py` and `codex.py` adapt provider records into the neutral model.
- `model.py` owns the provider-neutral session and evidence types.
- `redaction.py` and `scanner_resolution.py` apply overlays and fail-closed Gitleaks checks.
- `render.py` renders one `transcript.md` with Three-Ps frontmatter and omission evidence.
- `annotations.py` validates Agy output, source locators, sidecars, and compact annotation blocks.
- `cli.py` owns incremental generation, atomic publication, and Three-Ps updates.

The archive does not copy raw JSONL, HTML, PDFs, private reasoning, or subagent
dialogue. An explicit post-generation annotation may add `annotations.json` beside
the transcript. Agy receives only the rendered and Gitleaks-scanned Markdown through
stdin while running from an isolated temporary directory; the archive and repository
are not added to its workspace.

## Commands

```bash
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff format --check .
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ty check src tests
uv run --frozen claude-research-transcript --help
```

Use the configured uv cache unchanged. Do not add cache overrides or fallback
locations.

## Change discipline

Behavior changes use red-green-refactor. A negative discovery result is not proof
unless the searched stores and exclusions are explicit. Keep source JSONL
read-only, publish archives atomically, and preserve existing reviewed Three-Ps
metadata when regenerating a transcript.
