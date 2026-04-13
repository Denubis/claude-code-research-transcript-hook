#!/usr/bin/env python3
"""Claude Code transcript archive CLI.

Typer-based CLI that dispatches to the archive module.
"""

import json
import subprocess
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


@app.command()
def init(
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Skip interactive prompts"
    ),
):
    """Initialize transcript archiving for this repository.

    Creates an orphan 'transcripts' branch, mounts a worktree at .ai-transcripts/,
    and adds it to .gitignore. Safe to run multiple times (idempotent).
    """
    # Step 1: Verify git repo
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        repo_root = Path(result.stdout.strip())
    except subprocess.CalledProcessError as err:
        typer.echo("Error: not a git repository. Run 'git init' first.", err=True)
        raise typer.Exit(code=1) from err

    # Step 2: Check/create orphan branch
    branch_check = subprocess.run(
        ["git", "branch", "--list", "transcripts"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    if not branch_check.stdout.strip():
        # Save current branch
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        saved_branch = current.stdout.strip()

        try:
            subprocess.run(
                ["git", "switch", "--orphan", "transcripts"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init transcript archive"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            subprocess.run(
                ["git", "switch", saved_branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
        typer.echo("Created orphan branch 'transcripts'")
    else:
        typer.echo("transcripts branch already exists")

    # Step 3: Check/mount worktree
    worktree_dir = repo_root / ".ai-transcripts"
    if not worktree_dir.exists():
        subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), "transcripts"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        typer.echo(f"Mounted worktree at {worktree_dir}")
    else:
        typer.echo("worktree already mounted at .ai-transcripts/")

    # Step 4: Check/update .gitignore
    gitignore_path = repo_root / ".gitignore"
    existing = gitignore_path.read_text() if gitignore_path.exists() else ""
    if not any(line.strip() == ".ai-transcripts/" for line in existing.splitlines()):
        with gitignore_path.open("a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(".ai-transcripts/\n")
        typer.echo("Added .ai-transcripts/ to .gitignore")
    else:
        typer.echo(".ai-transcripts/ already in .gitignore")

    # Step 5: Install Stop hook in settings.local.json
    settings_path = repo_root / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}

    our_hook = {
        "type": "command",
        "command": "claude-transcript-archive archive --quiet",
    }

    hooks = settings.setdefault("hooks", {})
    stop_hooks = hooks.setdefault("Stop", [])

    existing_commands = [h.get("command") for h in stop_hooks]
    if our_hook["command"] not in existing_commands:
        stop_hooks.append(our_hook)
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        typer.echo("Installed Stop hook in .claude/settings.local.json")
    else:
        typer.echo("Stop hook already installed")

    # Step 6: Create project defaults
    defaults_path = repo_root / ".claude" / "transcript-defaults.json"
    if defaults_path.exists():
        typer.echo("defaults already configured")
    else:
        if non_interactive:
            defaults = {
                "tags": [],
                "purpose": "",
                "three_ps_context": {
                    "prompt_template": "",
                    "process_template": "",
                    "provenance_template": "",
                },
                "target": "branch",
            }
        else:
            tags_input = typer.prompt("Tags (comma-separated)", default="")
            tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []
            purpose = typer.prompt("Purpose", default="")
            prompt_tpl = typer.prompt("Three Ps - Prompt template", default="")
            process_tpl = typer.prompt("Three Ps - Process template", default="")
            provenance_tpl = typer.prompt("Three Ps - Provenance template", default="")
            target = typer.prompt("Target (branch/main/here)", default="branch")
            defaults = {
                "tags": tags,
                "purpose": purpose,
                "three_ps_context": {
                    "prompt_template": prompt_tpl,
                    "process_template": process_tpl,
                    "provenance_template": provenance_tpl,
                },
                "target": target,
            }
        defaults_path.parent.mkdir(parents=True, exist_ok=True)
        defaults_path.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
        typer.echo("Created transcript defaults")


if __name__ == "__main__":
    app()
