#!/usr/bin/env python3
"""Claude Code transcript archive CLI.

Typer-based CLI that dispatches to the archive module.
"""

import json
import sys
from pathlib import Path

import typer

from claude_transcript_archive import archive as _archive
from claude_transcript_archive import discovery as _discovery

app = typer.Typer(
    help="Archive Claude Code transcripts with research-grade metadata",
    add_completion=False,
)


@app.command()
def archive(
    title: str | None = typer.Option(None, help="Title to use"),
    retitle: bool = typer.Option(False, help="Force regenerate title/rename directory"),
    force: bool = typer.Option(False, help="Regenerate even if transcript unchanged"),
    local: bool = typer.Option(False, help="Archive to ./ai_transcripts/"),
    output: str | None = typer.Option(None, help="Custom output directory"),
    quiet: bool = typer.Option(False, help="Suppress error messages"),
    transcript: str | None = typer.Option(None, help="Path to transcript file"),
    session_id: str | None = typer.Option(None, "--session-id", help="Session ID"),
    prompt: str | None = typer.Option(None, help="Three Ps: Prompt summary"),
    process: str | None = typer.Option(None, help="Three Ps: Process summary"),
    provenance: str | None = typer.Option(None, help="Three Ps: Provenance summary"),
):
    """Archive a Claude Code transcript with research-grade metadata."""
    # Determine input source: CLI arguments, stdin, or auto-discovery
    transcript_path = None
    sid = None

    if transcript and session_id:
        transcript_path = Path(transcript)
        sid = session_id
    elif transcript or session_id:
        _archive.log_error("Both --transcript and --session-id must be provided together", quiet)
        raise typer.Exit(code=1)
    else:
        if not sys.stdin.isatty():
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                try:
                    payload = json.loads(stdin_content)
                    transcript_path = Path(payload.get("transcript_path", ""))
                    sid = payload.get("session_id", "")
                except json.JSONDecodeError:
                    pass

        if not transcript_path or not sid:
            discovered = _discovery.auto_discover_transcript()
            if discovered:
                transcript_path, sid = discovered
                _archive.log_info(f"Auto-discovered: {transcript_path}", quiet)
            else:
                _archive.log_error(
                    "No transcript found. Run from a project directory or use "
                    "--transcript and --session-id arguments.",
                    quiet,
                )
                raise typer.Exit(code=1)

    if not transcript_path or not sid:
        _archive.log_error("Missing transcript_path or session_id in input", quiet)
        raise typer.Exit(code=1)

    project_dir = _discovery.get_project_dir_from_transcript(transcript_path)
    archive_dir = _discovery.get_archive_dir(
        local=local,
        output=output,
        project_dir=project_dir if not local else None,
    )

    three_ps = None
    if prompt or process or provenance:
        three_ps = {
            "prompt_summary": prompt or "",
            "process_summary": process or "",
            "provenance_summary": provenance or "",
        }

    output_dir = _archive.archive(
        sid,
        transcript_path,
        archive_dir,
        force=force,
        force_retitle=retitle,
        provided_title=title,
        quiet=quiet,
        three_ps=three_ps,
    )

    if output_dir:
        _archive.log_info(f"Archived to: {output_dir}", quiet)
        _archive.log_info(f"View transcript: {output_dir / 'index.html'}", quiet)


if __name__ == "__main__":
    app()
