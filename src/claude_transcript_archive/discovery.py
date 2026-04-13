"""Path encoding, worktree resolution, session discovery, and project defaults."""

import json
import subprocess
import sys
from pathlib import Path


def resolve_worktrees() -> list[Path]:
    """Discover all git worktrees for the current repository.

    Runs ``git worktree list --porcelain`` and parses the output.
    Returns list of absolute Path objects for each worktree.

    Raises RuntimeError if not in a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        msg = "Not a git repository. Run from a git-tracked project directory."
        raise RuntimeError(msg) from exc

    output = result.stdout.strip()
    if not output:
        return [Path.cwd()]

    paths: list[Path] = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.removeprefix("worktree ")))

    return paths if paths else [Path.cwd()]


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


def discover_sessions() -> list[tuple[Path, str]]:
    """Discover all transcript sessions across git worktrees.

    Calls resolve_worktrees() to find all worktree paths, maps each
    through get_cc_project_path to find ~/.claude/projects/{encoded}/,
    and scans for *.jsonl files.

    Returns list of (transcript_path, session_id) tuples.
    Deduplicates by session_id (same session may appear under multiple worktree paths).
    """
    seen: dict[str, Path] = {}
    for wt_path in resolve_worktrees():
        encoded = get_cc_project_path(wt_path)
        projects_dir = Path.home() / ".claude" / "projects" / encoded
        if not projects_dir.is_dir():
            continue
        for jsonl_file in projects_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            if session_id not in seen:
                seen[session_id] = jsonl_file
    return [(path, sid) for sid, path in seen.items()]


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


_EXPECTED_TYPES: dict[str, type] = {
    "tags": list,
    "purpose": str,
    "three_ps_context": dict,
    "target": str,
}


def _validate_defaults(data: dict, source: Path) -> dict:
    """Validate expected keys have correct types, warn and drop mismatches."""
    if not isinstance(data, dict):
        actual = type(data).__name__
        print(f"Warning: Expected JSON object in {source}, got {actual}", file=sys.stderr)
        return {}
    validated = {}
    for key, value in data.items():
        expected = _EXPECTED_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            print(
                f"Warning: Key '{key}' in {source} should be {expected.__name__}, "
                f"got {type(value).__name__}; ignoring",
                file=sys.stderr,
            )
            continue
        validated[key] = value
    return validated


def load_project_defaults(project_dir: Path | None = None) -> dict:
    """Load project-level transcript defaults from .claude/transcript-defaults.json.

    Searches from project_dir upward to git root for the defaults file.
    Returns parsed dict if found, empty dict otherwise.
    Unknown keys are preserved (forward compatibility).
    Malformed JSON returns empty dict with warning to stderr.
    """
    if project_dir is None:
        return {}

    current = project_dir.resolve()
    while True:
        defaults_file = current / ".claude" / "transcript-defaults.json"
        if defaults_file.is_file():
            try:
                data = json.loads(defaults_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                print(
                    f"Warning: Malformed JSON in {defaults_file}",
                    file=sys.stderr,
                )
                return {}
            return _validate_defaults(data, defaults_file)

        # Stop at git root
        if (current / ".git").exists():
            break

        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return {}
