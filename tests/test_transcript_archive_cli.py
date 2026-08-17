"""Hermetic generation and contributor CLI tests."""

import json
from dataclasses import replace
from pathlib import Path

import claude_transcript_archive.cli as transcript_archive_cli
from claude_transcript_archive.cli import (
    format_discovery_exclusions,
    generate_sources,
    main,
)
from claude_transcript_archive.discovery import (
    DiscoveryExclusion,
    RepositoryIdentity,
    SourceDescriptor,
    SourceStamp,
    load_raw_records,
)
from claude_transcript_archive.redaction import (
    ProcessRunner,
    RedactionRule,
    SecretFinding,
    digest_value,
)


class _CleanScanner:
    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        assert candidate
        return ()


class _FindingScanner:
    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        for line_number, line in enumerate(candidate.splitlines(), start=1):
            if "synthetic-secret" in line:
                return (
                    SecretFinding(
                        rule_id="synthetic-rule",
                        start_line=line_number,
                        end_line=line_number,
                    ),
                )
        return ()


class _CountingLoader:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, path: Path):
        self.calls += 1
        return load_raw_records(path)


class _ForbiddenLoader:
    def __call__(self, path: Path):
        raise AssertionError(f"unchanged source was opened: {path}")


def _write_claude_source(path: Path, text: str) -> SourceDescriptor:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "user",
            "sessionId": "session-1",
            "uuid": "user-1",
            "timestamp": "2026-07-20T01:00:00Z",
            "cwd": "/repo",
            "gitBranch": "main",
            "message": {"role": "user", "content": text},
        }
    ]
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
    stat = path.stat()
    return SourceDescriptor(
        tool="claude",
        session_id="session-1",
        path=path,
        working_directories=("/repo",),
        stamp=SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
    )


def _write_sidechain_shard(
    source: SourceDescriptor,
    *records: dict[str, object],
) -> SourceDescriptor:
    shard = source.path.parent / source.session_id / "subagents" / "agent-worker.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
    return SourceDescriptor(
        tool=source.tool,
        session_id=source.session_id,
        path=source.path,
        working_directories=source.working_directories,
        stamp=source.stamp,
        sidechain_shards=(shard,),
    )


def test_source_marker_skips_unchanged_source_without_opening_jsonl(
    tmp_path: Path,
) -> None:
    source = _write_claude_source(tmp_path / "source.jsonl", "hello")
    archive = tmp_path / "archive"
    state = archive / ".state.json"

    first = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    second = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=_ForbiddenLoader(),
    )

    output = archive / "sessions" / "claude" / "session-1" / "transcript.md"
    assert first.rendered == 1
    assert second.skipped == 1
    assert second.rendered == 0
    assert output.is_file()
    assert "hello" in output.read_text(encoding="utf-8")
    assert not (output.parent / "summary.md").exists()


def test_previous_state_version_cannot_skip_changed_rendering_contract(
    tmp_path: Path,
) -> None:
    source = _write_claude_source(tmp_path / "source.jsonl", "hello")
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    previous = json.loads(state.read_text(encoding="utf-8"))
    previous["version"] = 3
    state.write_text(json.dumps(previous), encoding="utf-8")
    loader = _CountingLoader()

    regenerated = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=loader,
    )

    assert regenerated.rendered == 1
    assert regenerated.skipped == 0
    assert loader.calls == 1


def test_generation_marks_recovered_working_directory_in_header(
    tmp_path: Path,
) -> None:
    vanished = "/repo/.worktrees/deleted-feature"
    source = _write_claude_source(tmp_path / "source.jsonl", "hello")
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    first = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    output_path = archive / "sessions" / "claude" / "session-1" / "transcript.md"
    initial_output = output_path.read_text(encoding="utf-8")
    recovered = replace(
        source,
        recovered_working_directories=(vanished,),
    )

    second = generate_sources(
        (recovered,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )

    output = output_path.read_text(encoding="utf-8")
    assert first.rendered == 1
    assert "recovered: false" in initial_output
    assert "recovered_working_directories: []" in initial_output
    assert "- Recovered working directory:" not in initial_output
    assert second.rendered == 1
    assert second.skipped == 0
    assert "recovered: true" in output
    assert f'recovered_working_directories: ["{vanished}"]' in output
    assert f"- Recovered working directory: {vanished}" in output


def test_sidechain_shard_is_counted_but_never_rendered(
    tmp_path: Path,
) -> None:
    source = _write_sidechain_shard(
        _write_claude_source(tmp_path / "source.jsonl", "human dialogue"),
        {
            "type": "user",
            "sessionId": "session-1",
            "isSidechain": True,
            "message": {"content": "main agent dispatch"},
        },
        {
            "type": "assistant",
            "sessionId": "session-1",
            "isSidechain": True,
            "message": {
                "content": [
                    {"type": "text", "text": "sub-agent secret response"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "hidden.py"},
                    },
                ]
            },
        },
    )
    archive = tmp_path / "archive"

    result = generate_sources(
        (source,),
        archive_root=archive,
        state_path=archive / ".state.json",
        rules=(),
        scanner=_CleanScanner(),
    )

    output = (archive / "sessions" / "claude" / "session-1" / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert result.rendered == 1
    assert "| sub-agent-traffic | 2 |" in output
    assert "main agent dispatch" not in output
    assert "sub-agent secret response" not in output
    assert "hidden.py" not in output


def test_unchanged_sidechain_shard_uses_marker_without_opening_jsonl(
    tmp_path: Path,
) -> None:
    source = _write_sidechain_shard(
        _write_claude_source(tmp_path / "source.jsonl", "human dialogue"),
        {
            "type": "assistant",
            "sessionId": "session-1",
            "isSidechain": True,
            "message": {"content": "hidden"},
        },
    )
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )

    second = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=_ForbiddenLoader(),
    )

    assert second.skipped == 1
    assert second.rendered == 0


def test_changed_sidechain_shard_invalidates_parent_marker(
    tmp_path: Path,
) -> None:
    source = _write_sidechain_shard(
        _write_claude_source(tmp_path / "source.jsonl", "human dialogue"),
        {
            "type": "assistant",
            "sessionId": "session-1",
            "isSidechain": True,
            "message": {"content": "hidden"},
        },
    )
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    with source.sidechain_shards[0].open("a", encoding="utf-8") as target:
        target.write(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "session-1",
                    "isSidechain": True,
                    "message": {"content": "still hidden"},
                }
            )
            + "\n"
        )
    loader = _CountingLoader()

    changed = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=loader,
    )

    output = (archive / "sessions" / "claude" / "session-1" / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert changed.rendered == 1
    assert loader.calls == 2
    assert "| sub-agent-traffic | 2 |" in output


def test_sidechain_shard_requires_canonical_parent_and_sidechain_marker(
    tmp_path: Path,
) -> None:
    source = _write_sidechain_shard(
        _write_claude_source(tmp_path / "source.jsonl", "human dialogue"),
        {
            "type": "assistant",
            "sessionId": "different-session",
            "isSidechain": True,
            "message": {"content": "hidden"},
        },
    )
    archive = tmp_path / "archive"

    wrong_parent = generate_sources(
        (source,),
        archive_root=archive,
        state_path=archive / ".state.json",
        rules=(),
        scanner=_CleanScanner(),
    )

    assert wrong_parent.failed == 1
    assert "canonical sessionId different-session" in wrong_parent.failures[0].reason
    assert not (archive / "sessions" / "claude" / "session-1" / "transcript.md").exists()

    source.sidechain_shards[0].write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "session-1",
                "isSidechain": False,
                "message": {"content": "hidden"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    missing_marker = generate_sources(
        (source,),
        archive_root=archive,
        state_path=archive / ".state.json",
        rules=(),
        scanner=_CleanScanner(),
    )

    assert missing_marker.failed == 1
    assert "is not marked isSidechain true" in missing_marker.failures[0].reason


def test_codex_subagent_rollout_is_counted_but_never_rendered(
    tmp_path: Path,
) -> None:
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "child.jsonl"
    parent_records = (
        {
            "type": "session_meta",
            "timestamp": "2026-07-20T01:00:00Z",
            "payload": {
                "id": "parent-session",
                "session_id": "parent-session",
                "cwd": "/repo",
                "source": "cli",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "human dialogue"}],
            },
        },
    )
    child_records = (
        {
            "type": "session_meta",
            "payload": {
                "id": "child-session",
                "session_id": "parent-session",
                "parent_thread_id": "parent-session",
                "cwd": "/repo",
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
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "sub-agent hidden response"}],
            },
        },
    )
    for path, records in (
        (parent_path, parent_records),
        (child_path, child_records),
    ):
        path.write_text(
            "".join(f"{json.dumps(record)}\n" for record in records),
            encoding="utf-8",
        )
    stat = parent_path.stat()
    source = SourceDescriptor(
        tool="codex",
        session_id="parent-session",
        path=parent_path,
        working_directories=("/repo",),
        stamp=SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
        sidechain_shards=(child_path,),
    )
    archive = tmp_path / "archive"

    result = generate_sources(
        (source,),
        archive_root=archive,
        state_path=archive / ".state.json",
        rules=(),
        scanner=_CleanScanner(),
    )

    output = (archive / "sessions" / "codex" / "parent-session" / "transcript.md").read_text(
        encoding="utf-8"
    )
    assert result.rendered == 1
    assert "| sub-agent-traffic | 2 |" in output
    assert "human dialogue" in output
    assert "sub-agent hidden response" not in output


def test_grown_source_and_applicable_overlay_change_invalidate_marker(
    tmp_path: Path,
) -> None:
    source = _write_claude_source(tmp_path / "source.jsonl", "protected")
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    with source.path.open("a", encoding="utf-8") as target:
        target.write(
            json.dumps(
                {
                    "type": "mode",
                    "sessionId": "session-1",
                    "timestamp": "2026-07-20T01:00:01Z",
                }
            )
            + "\n"
        )
    loader = _CountingLoader()

    grown = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=loader,
    )

    assert grown.rendered == 1
    assert loader.calls == 1
    loader = _CountingLoader()
    rule = RedactionRule(
        tool="claude",
        session_id="session-1",
        source_order=None,
        stable_id="user-1",
        json_pointer="/message/content",
        digest=digest_value("protected"),
        reason="test",
    )
    overlaid = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(rule,),
        scanner=_CleanScanner(),
        loader=loader,
    )

    assert overlaid.rendered == 1
    assert loader.calls == 1
    output = archive / "sessions" / "claude" / "session-1" / "transcript.md"
    assert "[REDACTED: test]" in output.read_text(encoding="utf-8")


def test_absent_and_corrupt_markers_fail_open(tmp_path: Path) -> None:
    source = _write_claude_source(tmp_path / "source.jsonl", "hello")
    archive = tmp_path / "archive"
    state = archive / ".state.json"
    loader = _CountingLoader()

    absent = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=loader,
    )
    state.write_text("{broken", encoding="utf-8")
    corrupt = generate_sources(
        (source,),
        archive_root=archive,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
        loader=loader,
    )

    assert absent.rendered == 1
    assert corrupt.rendered == 1
    assert loader.calls == 2


def test_gitleaks_blocks_before_finalise_and_names_source_locator(
    tmp_path: Path,
) -> None:
    source = _write_claude_source(
        tmp_path / "source.jsonl",
        "synthetic-secret",
    )
    archive = tmp_path / "archive"

    result = generate_sources(
        (source,),
        archive_root=archive,
        state_path=archive / ".state.json",
        rules=(),
        scanner=_FindingScanner(),
    )

    assert result.rendered == 0
    assert result.failed == 1
    assert "synthetic-rule" in result.failures[0].reason
    assert "line 1, id user-1" in result.failures[0].reason
    assert not (archive / "sessions" / "claude" / "session-1" / "transcript.md").exists()


def test_cli_fails_loudly_and_names_missing_source_store_roots(
    tmp_path: Path,
    capsys,
) -> None:
    claude_root = tmp_path / "missing-claude"
    codex_root = tmp_path / "missing-codex"

    exit_code = main(
        [
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "source stores are unavailable; missing roots:" in captured.err
    assert f"Claude: {claude_root}" in captured.err
    assert f"Codex: {codex_root}" in captured.err
    assert "discovered=0" not in captured.out


def test_cli_accepts_fresh_archive_and_present_empty_source_stores(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    repository.mkdir()
    claude_root.mkdir()
    codex_root.mkdir()
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=tmp_path / ".git",
        worktrees=(repository,),
        normalized_remotes=(),
    )

    def inspect_empty_repository(
        path: Path,
        *,
        runner: ProcessRunner,
    ) -> RepositoryIdentity:
        assert path == repository
        assert runner is not None
        return identity

    monkeypatch.setattr(
        transcript_archive_cli,
        "inspect_repository",
        inspect_empty_repository,
    )

    exit_code = main(
        [
            "--repo",
            str(repository),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
            "--archive-root",
            str(tmp_path / "archive"),
            "--gitleaks",
            "/bin/true",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "discovered=0" in captured.out
    assert "failed=0" in captured.out


def test_claude_only_generation_does_not_require_codex_store(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "missing-codex"
    redactions = tmp_path / "redactions.toml"
    repository.mkdir()
    claude_root.mkdir()
    redactions.write_text("", encoding="utf-8")
    identity = RepositoryIdentity(
        root=repository,
        common_git_dir=tmp_path / ".git",
        worktrees=(repository,),
        normalized_remotes=(),
    )

    monkeypatch.setattr(
        transcript_archive_cli,
        "inspect_repository",
        lambda _path, **_options: identity,
    )

    exit_code = main(
        [
            "--source",
            "claude",
            "--repo",
            str(repository),
            "--claude-root",
            str(claude_root),
            "--codex-root",
            str(codex_root),
            "--archive-root",
            str(tmp_path / "archive"),
            "--redactions",
            str(redactions),
            "--gitleaks",
            "/bin/true",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "discovered=0" in captured.out


def test_discovery_exclusions_are_reported_without_source_content() -> None:
    report = format_discovery_exclusions(
        (
            DiscoveryExclusion(
                tool="codex",
                session_id="session-1",
                path=Path("/local/rollout.jsonl"),
                reason="different Git common directory",
            ),
        )
    )

    assert report == (
        "excluded=1\n"
        "exclusion codex/session-1: different Git common directory "
        "[/local/rollout.jsonl]\n"
    )
