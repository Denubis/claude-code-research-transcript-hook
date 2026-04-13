# Dependency Rationale

Falsifiable justifications for every direct dependency. Each entry records why the package was added, what evidence supports its use, and who it serves.

Maintained by design plans (when adding deps) and controlled-dependency-upgrade (when auditing). Reviewed by restate-our-assumptions (periodic philosophical audit).

## claude-code-transcripts
**Added:** 2024 (v1)
**Design plan:** Pre-dates formal design plans
**Claim:** We use claude-code-transcripts to convert JSONL transcript files to styled HTML. No equivalent functionality exists in this package.
**Evidence:** `src/claude_transcript_archive/cli.py` — called via subprocess to generate `index.html`
**Serves:** Runtime users (HTML output generation)

## typer
**Added:** 2026-04-13
**Design plan:** docs/design-plans/2026-04-12-transcript-archive-v2.md
**Claim:** We use Typer to define seven CLI subcommands (`init`, `archive`, `bulk`, `status`, `update`, `clean`, `regenerate`) with type-annotated parameters and automatic help generation. Replacing stdlib argparse because seven subcommands with distinct argument sets produce verbose, hard-to-maintain parser configuration.
**Evidence:** `src/claude_transcript_archive/cli.py` — Typer app definition with all verb commands (v2)
**Serves:** Runtime users (CLI interface), developers (reduced boilerplate)
