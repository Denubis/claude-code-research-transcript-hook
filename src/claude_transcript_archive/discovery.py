"""Path encoding, worktree resolution, session discovery, and project defaults."""

from pathlib import Path


def _encode_cc_path(resolved: str) -> str:
    """Apply Claude Code's path-to-directory-name encoding to a resolved path string.

    Replaces the Windows drive-letter colon and both separator styles with '-'.
    Example: ``'C:\\\\Users\\\\a\\\\proj'`` -> ``'C--Users-a-proj'``.
    """
    return resolved.replace(":", "-").replace("\\", "-").replace("/", "-")


def get_cc_project_path(project_dir: Path) -> str:
    """Get CC's path-encoded project ID.

    Claude Code encodes an absolute project path for use as a directory name
    under ~/.claude/projects/.

    Examples:
        PosixPath('/home/user/project')         -> '-home-user-project'
        WindowsPath('C:\\\\Users\\\\a\\\\proj') -> 'C--Users-a-proj'
    """
    return _encode_cc_path(str(project_dir.resolve()))


def get_archive_dir(local: bool, output: str | None, project_dir: Path | None = None) -> Path:
    """Determine the archive directory based on options.

    For global archives, organizes by project using CC's path encoding.
    """
    if output:
        return Path(output).expanduser().resolve()
    if local:
        return Path.cwd() / "ai_transcripts"

    # Global archive - organize by project
    base_dir = Path.home() / ".claude" / "transcripts"
    if project_dir:
        cc_path = get_cc_project_path(project_dir)
        return base_dir / cc_path
    return base_dir


def get_project_dir_from_transcript(transcript_path: Path) -> Path | None:
    """Extract project directory from transcript path.

    Transcript paths are typically like:
    ~/.claude/projects/-home-user-myproject/session-id.jsonl

    Claude Code encodes paths by replacing '/' with '-'. We try to decode
    by checking if the resulting path exists on the filesystem.
    """
    # Check if this is in the standard CC projects location
    projects_dir = Path.home() / ".claude" / "projects"
    try:
        rel_path = transcript_path.resolve().relative_to(projects_dir)
        # First component is the encoded project path
        encoded_path = rel_path.parts[0]

        # The encoding replaces / with - (and on Windows, : with - and \ with -).
        # Detect POSIX vs Windows encoded paths:
        #   POSIX:   "-home-user-project"  (leading dash from root /)
        #   Windows: "C--Users-Adela-proj" (double dash from drive colon C:)
        is_posix_encoded = encoded_path.startswith("-")
        # Windows drive pattern: single letter followed by double dash
        is_windows_encoded = (
            len(encoded_path) >= 3
            and encoded_path[0].isalpha()
            and encoded_path[1:3] == "--"
        )

        if is_posix_encoded:
            # Simple approach: replace all - with / and check if it exists
            decoded = encoded_path.replace("-", "/")
            candidate = Path(decoded)
            if candidate.exists():
                return candidate

            # If that doesn't work, try to be smarter by checking
            # progressively which segments exist
            parts = encoded_path[1:].split("-")  # Remove leading dash
            current = Path("/")
            for i, part in enumerate(parts):
                test_path = current / part
                if test_path.exists():
                    current = test_path
                else:
                    # Maybe this dash was part of the directory name
                    # Try combining with next parts
                    combined = part
                    for j in range(i + 1, len(parts)):
                        combined = f"{combined}-{parts[j]}"
                        test_path = current / combined
                        if test_path.exists():
                            current = test_path
                            break
                    else:
                        # Give up and return what we have
                        break

            if current != Path("/") and current.exists():
                return current

        elif is_windows_encoded:
            # Reconstruct drive letter: "C--Users-..." -> "C:\Users\..."
            drive_letter = encoded_path[0]
            rest = encoded_path[3:]  # Skip "C--"
            decoded = f"{drive_letter}:\\{rest.replace('-', chr(92))}"
            candidate = Path(decoded)
            if candidate.exists():
                return candidate

            # Progressive decode for Windows paths
            parts = rest.split("-")
            current = Path(f"{drive_letter}:\\")
            for i, part in enumerate(parts):
                if not part:
                    continue
                test_path = current / part
                if test_path.exists():
                    current = test_path
                else:
                    combined = part
                    for j in range(i + 1, len(parts)):
                        combined = f"{combined}-{parts[j]}"
                        test_path = current / combined
                        if test_path.exists():
                            current = test_path
                            break
                    else:
                        break

            if current != Path(f"{drive_letter}:\\") and current.exists():
                return current

    except ValueError:
        pass
    return None


def auto_discover_transcript() -> tuple[Path, str] | None:
    """Auto-discover the most recent transcript for the current project.

    Returns (transcript_path, session_id) or None if not found.
    """
    encoded_path = get_cc_project_path(Path.cwd())
    projects_dir = Path.home() / ".claude" / "projects" / encoded_path

    if not projects_dir.exists():
        return None

    # Find most recent .jsonl file
    jsonl_files = list(projects_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None

    # Sort by modification time, most recent first
    jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    transcript_path = jsonl_files[0]

    # Session ID is the filename without extension
    session_id = transcript_path.stem

    return transcript_path, session_id
