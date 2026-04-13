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
from claude_transcript_archive import catalog as _catalog
from claude_transcript_archive import discovery as _discovery
from claude_transcript_archive import metadata as _metadata

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

    # Load project defaults and determine target
    defaults = _discovery.load_project_defaults(project_dir)
    target = defaults.get("target")

    # CLI flags override defaults: --local or --output override branch target
    if local or output:
        target = None

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
        target=target,
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
        # Save current branch/commit for restore
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        saved_ref = current.stdout.strip()
        if saved_ref == "HEAD":
            # Detached HEAD — save the full SHA for checkout
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            saved_ref = sha.stdout.strip()

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
            # Restore previous branch/commit — don't mask the original error
            restore = subprocess.run(
                ["git", "checkout", saved_ref],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if restore.returncode != 0:
                typer.echo(
                    f"Warning: failed to restore branch '{saved_ref}': {restore.stderr.strip()}",
                    err=True,
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


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Report session state across worktrees."""
    try:
        worktrees = _discovery.resolve_worktrees()
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    sessions = _discovery.discover_sessions()

    # Determine archive location
    project_dir = (
        _discovery.get_project_dir_from_transcript(sessions[0][0]) if sessions else None
    )
    defaults = _discovery.load_project_defaults(project_dir)
    target = defaults.get("target", "branch")

    if target == "branch":
        # Use .ai-transcripts/ worktree
        try:
            repo_root = Path(
                subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            )
        except subprocess.CalledProcessError:
            repo_root = Path.cwd()
        archive_dir = repo_root / ".ai-transcripts"
    else:
        archive_dir = _discovery.get_archive_dir(
            local=(target == "here"),
            output=None,
            project_dir=project_dir,
        )

    manifest = _catalog.load_manifest(archive_dir) if archive_dir.exists() else {}
    catalog = _catalog.load_catalog(archive_dir) if archive_dir.exists() else {"sessions": []}

    # Cross-reference sessions with manifest
    archived = []
    unarchived = []

    for transcript_path, session_id in sessions:
        if session_id in manifest:
            # Check needs_review from catalog
            catalog_entry = next(
                (
                    s
                    for s in catalog.get("sessions", [])
                    if s.get("session_id") == session_id
                ),
                {},
            )
            archived.append({
                "session_id": session_id,
                "transcript_path": str(transcript_path),
                "needs_review": catalog_entry.get("needs_review", True),
            })
        else:
            # Classify unarchived session
            content = (
                transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
            )
            classification = _metadata.classify_session(content)
            unarchived.append({
                "session_id": session_id,
                "transcript_path": str(transcript_path),
                "classification": classification,
            })

    if json_output:
        typer.echo(json.dumps({
            "worktrees": len(worktrees),
            "archived": archived,
            "unarchived": unarchived,
            "total": len(archived) + len(unarchived),
        }, indent=2))
    else:
        reviewed = sum(1 for s in archived if not s["needs_review"])
        needs_review = sum(1 for s in archived if s["needs_review"])
        substantial = sum(1 for s in unarchived if s["classification"] == "substantial")
        trivial = sum(1 for s in unarchived if s["classification"] == "trivial")

        repo_name = Path.cwd().name
        typer.echo(
            f"Project: {repo_name}"
            f" ({len(worktrees)} worktree{'s' if len(worktrees) != 1 else ''})"
        )
        typer.echo("")
        typer.echo(
            f"  Archived:    {len(archived)} sessions"
            f" ({reviewed} reviewed, {needs_review} needs_review)"
        )
        typer.echo(
            f"  Unarchived:  {len(unarchived)} sessions"
            f" ({substantial} substantial, {trivial} trivial)"
        )
        typer.echo(f"  Total:       {len(archived) + len(unarchived)} sessions")


@app.command()
def bulk(
    local: bool = typer.Option(False, help="Archive to ./ai_transcripts/"),
    output: str | None = typer.Option(None, help="Custom output directory"),
    quiet: bool = typer.Option(False, help="Suppress output"),
):
    """Archive all unarchived sessions in bulk."""
    try:
        _discovery.resolve_worktrees()
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    sessions = _discovery.discover_sessions()
    if not sessions:
        if not quiet:
            typer.echo("No sessions found.")
        return

    # Determine archive location
    project_dir = _discovery.get_project_dir_from_transcript(sessions[0][0])
    defaults = _discovery.load_project_defaults(project_dir)
    target = defaults.get("target")

    # CLI flags override defaults
    if local or output:
        target = None

    if target == "branch":
        try:
            repo_root = Path(
                subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            )
        except subprocess.CalledProcessError:
            repo_root = Path.cwd()
        archive_dir = repo_root / ".ai-transcripts"
    else:
        archive_dir = _discovery.get_archive_dir(
            local=local,
            output=output,
            project_dir=project_dir if not local else None,
        )

    manifest = _catalog.load_manifest(archive_dir) if archive_dir.exists() else {}

    # Filter to unarchived sessions
    unarchived = [(tp, sid) for tp, sid in sessions if sid not in manifest]

    if not unarchived:
        if not quiet:
            typer.echo("All sessions already archived.")
        return

    archived_count = 0
    trivial_count = 0

    for transcript_path, session_id in unarchived:
        content = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
        classification = _metadata.classify_session(content)

        if classification == "trivial":
            trivial_count += 1

        result = _archive.archive(
            session_id,
            transcript_path,
            archive_dir,
            quiet=quiet,
            target=target,
            trivial=(classification == "trivial"),
        )
        if result:
            archived_count += 1

    if not quiet:
        typer.echo(
            f"Bulk archive complete: {archived_count} archived"
            f" ({trivial_count} trivial), {len(sessions) - len(unarchived)} already archived"
        )


if __name__ == "__main__":
    app()
