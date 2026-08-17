"""Resolve the contributor-facing Gitleaks executable.

# pattern: Imperative Shell
"""

import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from .redaction import RedactionError, SecretFinding

if TYPE_CHECKING:
    from collections.abc import Mapping

GITLEAKS_REPOSITORY = "https://github.com/gitleaks/gitleaks"
GITLEAKS_REVISION = "v8.30.1"
_CACHE_EXECUTABLE = Path("golangenv-system/bin/gitleaks")


@dataclass(frozen=True, slots=True)
class GitleaksResolution:
    """One executable lookup result and its audit trail."""

    executable: str | None
    attempts: tuple[str, ...]
    failure_message: str


@dataclass(frozen=True, slots=True)
class UnavailableGitleaksScanner:
    """Fail every candidate when no secret scanner can be resolved."""

    reason: str

    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        """Refuse publication without secret scanning."""
        del candidate
        raise RedactionError(self.reason)


def resolve_gitleaks(
    explicit: str | None,
    *,
    environment: Mapping[str, str],
    home: Path,
) -> GitleaksResolution:
    """Resolve Gitleaks without ever weakening fail-closed publication."""
    attempts: list[str] = []
    path_value = environment.get("PATH", "")
    resolved = _resolve_override(
        explicit,
        label="--gitleaks",
        path_value=path_value,
        attempts=attempts,
    )
    if resolved is not None:
        return _successful_resolution(resolved, attempts)

    resolved = _resolve_override(
        environment.get("GITLEAKS"),
        label="GITLEAKS",
        path_value=path_value,
        attempts=attempts,
    )
    if resolved is not None:
        return _successful_resolution(resolved, attempts)

    resolved = shutil.which("gitleaks", path=path_value)
    if resolved is not None:
        attempts.append(f"PATH: {resolved}")
        return _successful_resolution(resolved, attempts)
    attempts.append(f"PATH: {path_value or '<unset>'} (gitleaks not found)")

    cache_root = _pre_commit_cache_root(environment, home)
    cached, cache_attempt = _cached_gitleaks(cache_root)
    attempts.append(cache_attempt)
    if cached is not None:
        return _successful_resolution(cached, attempts)

    failure_message = (
        f"Gitleaks executable not found; tried {'; '.join(attempts)}. "
        "Install Gitleaks or pass --gitleaks PATH."
    )
    return GitleaksResolution(
        executable=None,
        attempts=tuple(attempts),
        failure_message=failure_message,
    )


def _resolve_override(
    value: str | None,
    *,
    label: str,
    path_value: str,
    attempts: list[str],
) -> str | None:
    if not value:
        missing = "not provided" if label == "--gitleaks" else "not set"
        attempts.append(f"{label}: {missing}")
        return None
    resolved = shutil.which(value, path=path_value)
    if resolved is None:
        attempts.append(f"{label}: {value} (not executable)")
        return None
    attempts.append(f"{label}: {resolved}")
    return resolved


def _successful_resolution(
    executable: str,
    attempts: list[str],
) -> GitleaksResolution:
    return GitleaksResolution(
        executable=executable,
        attempts=tuple(attempts),
        failure_message="",
    )


def _pre_commit_cache_root(
    environment: Mapping[str, str],
    home: Path,
) -> Path:
    configured = environment.get("PRE_COMMIT_HOME")
    if configured:
        return Path(configured).expanduser()
    xdg_cache = environment.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache).expanduser() / "pre-commit"
    return home / ".cache" / "pre-commit"


def _cached_gitleaks(cache_root: Path) -> tuple[str | None, str]:
    database_path = cache_root / "db.db"
    if not database_path.is_file():
        return (
            None,
            f"pre-commit cache database: {database_path} (not found)",
        )
    try:
        repository_root = _cached_repository_path(database_path)
    except (OSError, sqlite3.Error) as error:
        return (
            None,
            f"pre-commit cache database: {database_path} ({type(error).__name__}: {error})",
        )
    if repository_root is None:
        return (
            None,
            f"pre-commit cache database: {database_path} "
            f"({GITLEAKS_REPOSITORY}@{GITLEAKS_REVISION} not mapped)",
        )
    candidate = repository_root / _CACHE_EXECUTABLE
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None, f"pre-commit cache: {candidate} (not executable)"
    return str(candidate), f"pre-commit cache: {candidate}"


def _cached_repository_path(database_path: Path) -> Path | None:
    uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        row = database.execute(
            "SELECT path FROM repos WHERE repo = ? AND ref = ?",
            (GITLEAKS_REPOSITORY, GITLEAKS_REVISION),
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    return Path(row[0])
