"""Hermetic tests for repository-wide transcript discovery."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from claude_transcript_archive.discovery import (
    DiscoveryError,
    RepositoryIdentity,
    discover_sessions,
    discover_sessions_cached,
    normalize_repository_url,
)

if TYPE_CHECKING:
    from pathlib import Path


class _Resolver:
    def __init__(self, mappings: dict[Path, Path]) -> None:
        self.mappings = mappings

    def common_dir_for(self, path: Path) -> Path | None:
        existing = path
        while existing not in self.mappings and existing != existing.parent:
            existing = existing.parent
        return self.mappings.get(existing)


def _write_jsonl(path: Path, *records: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )


def _identity(
    repository: Path,
    common_dir: Path,
    worktree: Path,
) -> RepositoryIdentity:
    return RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository, worktree),
        normalized_remotes=("github.com/example/google-live",),
    )


def test_discovery_includes_nested_directories_and_every_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    worktree = repository / ".worktrees" / "feature"
    nested = repository / "planning" / "issues"
    for directory in (repository, worktree, nested):
        directory.mkdir(parents=True)
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _write_jsonl(
        claude_root / "project-a" / "claude-main.jsonl",
        {
            "type": "user",
            "sessionId": "claude-main",
            "cwd": str(unrelated),
            "message": {"content": "not yet"},
        },
        {
            "type": "assistant",
            "sessionId": "claude-main",
            "session_id": "vendor-internal-id",
            "cwd": str(nested),
            "message": {"content": [{"type": "text", "text": "answer"}]},
        },
        {
            "type": "user",
            "sessionId": "claude-main",
            "cwd": str(worktree),
            "message": {"content": "later"},
        },
    )
    _write_jsonl(
        codex_root / "2026" / "rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "codex-main",
                "session_id": "codex-main",
                "cwd": str(worktree),
                "git": {"repository_url": ("git@github.com:example/google-live.git")},
            },
        },
        {
            "type": "turn_context",
            "payload": {"cwd": str(nested)},
        },
    )
    resolver = _Resolver(
        {
            repository: common_dir,
            worktree: common_dir,
            unrelated: tmp_path / "other.git",
        }
    )

    result = discover_sessions(
        _identity(repository, common_dir, worktree),
        resolver=resolver,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    assert [(source.tool, source.session_id) for source in result.sources] == [
        ("claude", "claude-main"),
        ("codex", "codex-main"),
    ]
    assert result.sources[0].working_directories == (
        str(unrelated),
        str(nested),
        str(worktree),
    )
    assert result.sources[1].working_directories == (
        str(worktree),
        str(nested),
    )


def test_discovery_marks_only_vanished_member_paths_as_recovered(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    vanished = repository / ".worktrees" / "deleted-feature"
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        claude_root / "project" / "recovered-session.jsonl",
        {
            "type": "user",
            "sessionId": "recovered-session",
            "cwd": str(vanished),
            "message": {"content": "human"},
        },
    )
    _write_jsonl(
        codex_root / "live-session.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "live-session",
                "session_id": "live-session",
                "cwd": str(repository),
            },
        },
    )

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=(),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=claude_root,
        codex_root=codex_root,
    )

    recovered, live = result.sources
    assert recovered.session_id == "recovered-session"
    assert recovered.recovered_working_directories == (str(vanished),)
    assert live.session_id == "live-session"
    assert live.recovered_working_directories == ()


def test_cached_discovery_preserves_recovered_working_directory(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    vanished = repository / ".worktrees" / "deleted-feature"
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    _write_jsonl(
        claude_root / "project" / "recovered-session.jsonl",
        {
            "type": "user",
            "sessionId": "recovered-session",
            "cwd": str(vanished),
            "message": {"content": "human"},
        },
    )
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=(),
    )
    cache = tmp_path / "archive" / ".discovery.json"

    first = discover_sessions_cached(
        identity,
        resolver=_Resolver({repository: common_dir}),
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )
    second = discover_sessions_cached(
        identity,
        resolver=_Resolver({repository: common_dir}),
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )

    assert first == second
    assert first.sources[0].recovered_working_directories == (str(vanished),)


def test_codex_subagent_rollout_attaches_to_direct_parent(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    codex_root = tmp_path / "codex"
    parent = codex_root / "2026" / "07" / "parent.jsonl"
    child = codex_root / "2026" / "07" / "child.jsonl"
    _write_jsonl(
        parent,
        {
            "type": "session_meta",
            "payload": {
                "id": "parent-session",
                "session_id": "parent-session",
                "cwd": str(repository),
                "source": "cli",
            },
        },
    )
    _write_jsonl(
        child,
        {
            "type": "session_meta",
            "payload": {
                "id": "child-session",
                "session_id": "parent-session",
                "parent_thread_id": "parent-session",
                "cwd": str(repository),
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "parent-session",
                            "depth": 1,
                        }
                    }
                },
            },
        },
    )
    with child.open("a", encoding="utf-8") as target:
        target.write("{not parsed during discovery\n")

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=(),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=tmp_path / "claude",
        codex_root=codex_root,
    )

    assert [(source.tool, source.session_id) for source in result.sources] == [
        ("codex", "parent-session")
    ]
    assert result.sources[0].sidechain_shards == (child,)


def test_unrelated_codex_subagent_schema_does_not_abort_discovery(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    unrelated = tmp_path / "other"
    repository.mkdir()
    unrelated.mkdir()
    common_dir = tmp_path / "common.git"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        codex_root / "child.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "unrelated-child",
                "cwd": str(unrelated),
                "source": {"subagent": {}},
            },
        },
    )

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=(),
        ),
        resolver=_Resolver(
            {
                repository: common_dir,
                unrelated: tmp_path / "other.git",
            }
        ),
        claude_root=tmp_path / "claude",
        codex_root=codex_root,
    )

    assert result.sources == ()
    assert result.exclusions == ()


def test_claude_sidechain_shards_attach_to_canonical_parent_without_parsing(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    parent = claude_root / "project" / "claude-main.jsonl"
    shard = claude_root / "project" / "claude-main" / "subagents" / "agent-worker.jsonl"
    _write_jsonl(
        parent,
        {
            "type": "user",
            "sessionId": "claude-main",
            "cwd": str(repository),
            "message": {"content": "human"},
        },
    )
    shard.parent.mkdir(parents=True)
    shard.write_text("{not parsed during discovery\n", encoding="utf-8")

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=(),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
    )

    assert len(result.sources) == 1
    assert result.sources[0].session_id == "claude-main"
    assert result.sources[0].sidechain_shards == (shard,)


def test_cached_claude_workflow_shard_attaches_to_parent_session(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "brian-ed3d-plugins"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    parent = claude_root / "project" / "parent-session.jsonl"
    shard = (
        claude_root
        / "project"
        / "parent-session"
        / "subagents"
        / "workflows"
        / "wf-example"
        / "agent-worker.jsonl"
    )
    _write_jsonl(
        parent,
        {
            "type": "user",
            "sessionId": "parent-session",
            "cwd": str(repository),
            "message": {"content": "human"},
        },
    )
    _write_jsonl(
        shard,
        {
            "type": "assistant",
            "sessionId": "parent-session",
            "cwd": str(repository),
            "isSidechain": True,
            "agentId": "worker",
            "message": {"content": [{"type": "text", "text": "subagent"}]},
        },
    )

    result = discover_sessions_cached(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=(),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=tmp_path / "archive" / ".discovery.json",
    )

    assert len(result.sources) == 1
    assert result.sources[0].session_id == "parent-session"
    assert result.sources[0].sidechain_shards == (shard,)


def test_codex_matching_remote_in_sibling_clone_is_reported_and_excluded(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    worktree = repository / ".worktrees" / "feature"
    unrelated = tmp_path / "unrelated"
    for directory in (repository, worktree, unrelated):
        directory.mkdir(parents=True)
    common_dir = tmp_path / "common.git"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        codex_root / "rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "codex-mismatch",
                "session_id": "codex-mismatch",
                "cwd": str(unrelated),
                "git": {"repository_url": "https://github.com/example/google-live"},
            },
        },
    )
    resolver = _Resolver(
        {
            repository: common_dir,
            worktree: common_dir,
            unrelated: tmp_path / "other.git",
        }
    )

    result = discover_sessions(
        _identity(repository, common_dir, worktree),
        resolver=resolver,
        claude_root=tmp_path / "claude",
        codex_root=codex_root,
    )

    assert result.sources == ()
    assert len(result.exclusions) == 1
    assert result.exclusions[0].session_id == "codex-mismatch"
    assert "different Git common directory" in result.exclusions[0].reason
    assert "matching remote is corroboration only" in result.exclusions[0].reason


def test_codex_matching_remote_without_path_membership_is_reported_and_excluded(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        codex_root / "rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "session_id": "codex-no-path",
                "cwd": str(tmp_path / "deleted"),
                "git": {"repository_url": "https://github.com/example/google-live"},
            },
        },
    )

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=("github.com/example/google-live",),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=tmp_path / "claude",
        codex_root=codex_root,
    )

    assert result.sources == ()
    assert len(result.exclusions) == 1
    assert "no Git common-directory membership evidence" in (result.exclusions[0].reason)


def test_codex_member_with_conflicting_remote_is_reported_and_excluded(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        codex_root / "rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "session_id": "codex-conflict",
                "cwd": str(repository),
                "git": {"repository_url": "https://github.com/example/other"},
            },
        },
    )

    result = discover_sessions(
        RepositoryIdentity(
            root=repository,
            common_git_dir=common_dir,
            worktrees=(repository,),
            normalized_remotes=("github.com/example/google-live",),
        ),
        resolver=_Resolver({repository: common_dir}),
        claude_root=tmp_path / "claude",
        codex_root=codex_root,
    )

    assert result.sources == ()
    assert len(result.exclusions) == 1
    assert "conflicting remote" in result.exclusions[0].reason


def test_unrelated_sessions_are_excluded(tmp_path: Path) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    _write_jsonl(
        claude_root / "other" / "claude-other.jsonl",
        {
            "type": "user",
            "sessionId": "claude-other",
            "cwd": str(unrelated),
            "message": {"content": "other"},
        },
    )
    _write_jsonl(
        codex_root / "rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": "thread-other",
                "session_id": "codex-other",
                "cwd": str(unrelated),
                "git": {"repository_url": "https://github.com/example/other.git"},
            },
        },
    )
    resolver = _Resolver(
        {
            repository: common_dir,
            unrelated: tmp_path / "other.git",
        }
    )
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=("github.com/example/google-live",),
    )

    result = discover_sessions(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=codex_root,
    )

    assert result.sources == ()


def test_cached_discovery_does_not_open_unchanged_source(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    source = claude_root / "project" / "claude-main.jsonl"
    _write_jsonl(
        source,
        {
            "type": "user",
            "sessionId": "claude-main",
            "cwd": str(repository),
            "message": {"content": "human"},
        },
    )
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=(),
    )
    resolver = _Resolver({repository: common_dir})
    cache = tmp_path / "archive" / ".discovery.json"
    first = discover_sessions_cached(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )
    stat = source.stat()
    source.write_bytes(b"x" * stat.st_size)
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    second = discover_sessions_cached(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )

    assert first == second


def test_cached_discovery_classifies_new_source(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=(),
    )
    resolver = _Resolver({repository: common_dir})
    cache = tmp_path / "archive" / ".discovery.json"
    first = discover_sessions_cached(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )
    _write_jsonl(
        claude_root / "project" / "new-session.jsonl",
        {
            "type": "user",
            "sessionId": "new-session",
            "cwd": str(repository),
            "message": {"content": "human"},
        },
    )

    second = discover_sessions_cached(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
        cache_path=cache,
    )

    assert first.sources == ()
    assert [source.session_id for source in second.sources] == ["new-session"]


def test_malformed_unrelated_source_does_not_abort_repository_discovery(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "google-live"
    unrelated = tmp_path / "unrelated"
    repository.mkdir()
    unrelated.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    malformed = claude_root / "other" / "broken.jsonl"
    malformed.parent.mkdir(parents=True)
    malformed.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "broken",
                "cwd": str(unrelated),
                "message": {"content": "other"},
            }
        )
        + "\n{broken\n",
        encoding="utf-8",
    )
    resolver = _Resolver(
        {
            repository: common_dir,
            unrelated: tmp_path / "other.git",
        }
    )
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=(),
    )

    result = discover_sessions(
        identity,
        resolver=resolver,
        claude_root=claude_root,
        codex_root=tmp_path / "codex",
    )

    assert result.sources == ()


def test_malformed_matched_source_fails_with_line(tmp_path: Path) -> None:
    repository = tmp_path / "google-live"
    repository.mkdir()
    common_dir = tmp_path / "common.git"
    claude_root = tmp_path / "claude"
    malformed = claude_root / "project" / "broken.jsonl"
    malformed.parent.mkdir(parents=True)
    malformed.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "broken",
                "cwd": str(repository),
                "message": {"content": "ours"},
            }
        )
        + "\n{broken\n",
        encoding="utf-8",
    )
    resolver = _Resolver({repository: common_dir})
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=common_dir,
        worktrees=(repository,),
        normalized_remotes=(),
    )

    with pytest.raises(DiscoveryError, match=r"broken\.jsonl.*line 2"):
        discover_sessions(
            identity,
            resolver=resolver,
            claude_root=claude_root,
            codex_root=tmp_path / "codex",
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "git@github.com:Example/Google-Live.git",
            "github.com/example/google-live",
        ),
        (
            "https://github.com/Example/Google-Live.git/",
            "github.com/example/google-live",
        ),
        (
            "ssh://git@github.com/Example/Google-Live.git",
            "github.com/example/google-live",
        ),
    ],
)
def test_repository_url_normalization(value: str, expected: str) -> None:
    assert normalize_repository_url(value) == expected
