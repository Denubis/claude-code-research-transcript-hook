"""Gitleaks executable resolution tests."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from claude_transcript_archive.cli import _parser
from claude_transcript_archive.redaction import RedactionError
from claude_transcript_archive.scanner_resolution import (
    GITLEAKS_REPOSITORY,
    GITLEAKS_REVISION,
    UnavailableGitleaksScanner,
    resolve_gitleaks,
)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _pre_commit_cache(
    cache_root: Path,
    *,
    repository: str = GITLEAKS_REPOSITORY,
    revision: str = GITLEAKS_REVISION,
    executable: bool = True,
) -> Path:
    cache_root.mkdir(parents=True)
    repository_root = cache_root / "repo-pinned-gitleaks"
    with closing(sqlite3.connect(cache_root / "db.db")) as database:
        database.execute(
            """
            CREATE TABLE repos (
                repo TEXT NOT NULL,
                ref TEXT NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (repo, ref)
            )
            """
        )
        database.execute(
            "INSERT INTO repos(repo, ref, path) VALUES (?, ?, ?)",
            (repository, revision, str(repository_root)),
        )
        database.commit()
    candidate = repository_root / "golangenv-system" / "bin" / "gitleaks"
    if executable:
        _executable(candidate)
    return candidate


def test_explicit_gitleaks_precedes_every_automatic_source(tmp_path: Path) -> None:
    explicit = _executable(tmp_path / "explicit" / "scanner")
    environment = _executable(tmp_path / "environment" / "scanner")
    path_scanner = _executable(tmp_path / "path" / "gitleaks")
    cache_scanner = _pre_commit_cache(tmp_path / "cache")

    resolution = resolve_gitleaks(
        str(explicit),
        environment={
            "GITLEAKS": str(environment),
            "PATH": str(path_scanner.parent),
            "PRE_COMMIT_HOME": str(cache_scanner.parents[3]),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(explicit)
    assert resolution.attempts == (f"--gitleaks: {explicit}",)


def test_missing_explicit_gitleaks_falls_through_to_environment(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-explicit"
    environment = _executable(tmp_path / "environment" / "scanner")

    resolution = resolve_gitleaks(
        str(missing),
        environment={
            "GITLEAKS": str(environment),
            "PATH": str(tmp_path / "empty-path"),
            "PRE_COMMIT_HOME": str(tmp_path / "empty-cache"),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(environment)
    assert resolution.attempts == (
        f"--gitleaks: {missing} (not executable)",
        f"GITLEAKS: {environment}",
    )


def test_environment_gitleaks_precedes_path_and_cache(tmp_path: Path) -> None:
    environment = _executable(tmp_path / "environment" / "scanner")
    path_scanner = _executable(tmp_path / "path" / "gitleaks")
    cache_scanner = _pre_commit_cache(tmp_path / "cache")

    resolution = resolve_gitleaks(
        None,
        environment={
            "GITLEAKS": str(environment),
            "PATH": str(path_scanner.parent),
            "PRE_COMMIT_HOME": str(cache_scanner.parents[3]),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(environment)
    assert resolution.attempts == (
        "--gitleaks: not provided",
        f"GITLEAKS: {environment}",
    )


def test_path_gitleaks_precedes_pre_commit_cache(tmp_path: Path) -> None:
    path_scanner = _executable(tmp_path / "path" / "gitleaks")
    cache_scanner = _pre_commit_cache(tmp_path / "cache")

    resolution = resolve_gitleaks(
        None,
        environment={
            "PATH": str(path_scanner.parent),
            "PRE_COMMIT_HOME": str(cache_scanner.parents[3]),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(path_scanner)
    assert resolution.attempts == (
        "--gitleaks: not provided",
        "GITLEAKS: not set",
        f"PATH: {path_scanner}",
    )


def test_pre_commit_cache_matches_pinned_repository_and_revision(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    cache_scanner = _pre_commit_cache(cache_root)

    resolution = resolve_gitleaks(
        None,
        environment={
            "PATH": str(tmp_path / "empty-path"),
            "PRE_COMMIT_HOME": str(cache_root),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(cache_scanner)
    assert resolution.attempts[-1] == f"pre-commit cache: {cache_scanner}"


def test_xdg_cache_home_selects_pre_commit_store(tmp_path: Path) -> None:
    xdg_root = tmp_path / "xdg"
    cache_scanner = _pre_commit_cache(xdg_root / "pre-commit")

    resolution = resolve_gitleaks(
        None,
        environment={
            "PATH": str(tmp_path / "empty-path"),
            "XDG_CACHE_HOME": str(xdg_root),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable == str(cache_scanner)


def test_missing_scanner_names_every_resolution_location(tmp_path: Path) -> None:
    explicit = tmp_path / "missing-explicit"
    environment = tmp_path / "missing-environment"
    cache_root = tmp_path / "missing-cache"

    resolution = resolve_gitleaks(
        str(explicit),
        environment={
            "GITLEAKS": str(environment),
            "PATH": str(tmp_path / "missing-path"),
            "PRE_COMMIT_HOME": str(cache_root),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable is None
    assert resolution.failure_message == (
        "Gitleaks executable not found; tried "
        f"--gitleaks: {explicit} (not executable); "
        f"GITLEAKS: {environment} (not executable); "
        f"PATH: {tmp_path / 'missing-path'} (gitleaks not found); "
        f"pre-commit cache database: {cache_root / 'db.db'} (not found). "
        "Install Gitleaks or pass --gitleaks PATH."
    )
    with pytest.raises(RedactionError, match="Gitleaks executable not found"):
        UnavailableGitleaksScanner(resolution.failure_message).scan("candidate")


def test_cache_layout_change_degrades_to_loud_failure(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    missing = _pre_commit_cache(cache_root, executable=False)

    resolution = resolve_gitleaks(
        None,
        environment={
            "PATH": str(tmp_path / "missing-path"),
            "PRE_COMMIT_HOME": str(cache_root),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable is None
    assert f"pre-commit cache: {missing} (not executable)" in resolution.failure_message


def test_cache_does_not_accept_a_different_revision(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    _pre_commit_cache(cache_root, revision="v8.30.0")

    resolution = resolve_gitleaks(
        None,
        environment={
            "PATH": str(tmp_path / "missing-path"),
            "PRE_COMMIT_HOME": str(cache_root),
        },
        home=tmp_path / "home",
    )

    assert resolution.executable is None
    assert f"{GITLEAKS_REPOSITORY}@{GITLEAKS_REVISION} not mapped" in resolution.failure_message


def test_cli_without_gitleaks_override_requests_automatic_resolution() -> None:
    options = _parser().parse_args(["generate"])

    assert options.gitleaks is None
