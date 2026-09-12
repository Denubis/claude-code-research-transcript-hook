"""Behavioral tests for permanent transcript annotation sidecars."""

import hashlib
import json
from pathlib import Path
from typing import cast

import claude_transcript_archive.cli as transcript_archive_cli
from claude_transcript_archive.annotations import (
    ANNOTATION_END,
    ANNOTATION_START,
    annotation_json_schema,
    strip_annotation_section,
)
from claude_transcript_archive.cli import generate_sources, main
from claude_transcript_archive.discovery import SourceDescriptor, SourceStamp
from claude_transcript_archive.redaction import ProcessResult, SecretFinding


def _transcript() -> str:
    return (
        "---\n"
        'source_tool: "claude"\n'
        'session_id: "session-1"\n'
        'prompt_summary: ""\n'
        'process_summary: ""\n'
        'provenance_summary: ""\n'
        "needs_review: true\n"
        "---\n\n"
        "# Transcript\n\n"
        "## User\n"
        "- Timestamp: 2026-08-18T00:00:00Z\n"
        "- Source: line 1, id user-1\n\n"
        "```text\n"
        "Shell redirects keep bypassing the file-edit guard.\n"
        "```\n"
    )


def _structured_output(*, locator: str = "line 1, id user-1") -> dict[str, object]:
    return {
        "title": "Guard repository writes structurally",
        "keywords": ["file writes", "shell redirection"],
        "prompt_summary": "Prevent repository writes from bypassing edit guards.",
        "process_summary": "Identify the enforcement boundary rather than add prose rules.",
        "provenance_summary": "Records a recurring workflow failure and its durable repair.",
        "decisions": ["Reject repository-targeted shell redirection in the approver."],
        "affected_artifacts": ["shell approver"],
        "findings": [
            {
                "kind": "friction_signal",
                "observation": "Repeated bypasses frustrated the user.",
                "why_it_matters": "The edit guard cannot work when writes evade its boundary.",
                "status": "open",
                "confidence": "high",
                "evidence_locators": [locator],
                "repair_locators": [],
            }
        ],
    }


class _AgyRunner:
    def __init__(self, structured_output: dict[str, object]) -> None:
        self.structured_output = structured_output
        self.calls: list[tuple[tuple[str, ...], str | None, Path | None]] = []

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
        cwd: Path | None = None,
    ) -> ProcessResult:
        self.calls.append((arguments, input_text, cwd))
        if arguments[:2] == ("agy", "--version"):
            assert timeout_seconds == 10
            return ProcessResult(returncode=0, stdout="agy 1.1.13\n", stderr="")
        if arguments[0] == "agy":
            assert timeout_seconds == 120
            assert cwd is not None
            assert input_text is not None
            assert "# Transcript" in input_text
            schema_index = arguments.index("--json-schema") + 1
            schema_path = Path(arguments[schema_index])
            assert schema_path.parent == cwd
            assert schema_path.is_file()
            envelope = {
                "conversation_id": "conversation-1",
                "status": "SUCCESS",
                "response": json.dumps(self.structured_output),
                "structured_output": self.structured_output,
            }
            return ProcessResult(returncode=0, stdout=json.dumps(envelope), stderr="")
        if arguments[0] == "/bin/true":
            assert input_text is not None
            return ProcessResult(returncode=0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected process: {arguments}")


class _FailedAgyRunner(_AgyRunner):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
        cwd: Path | None = None,
    ) -> ProcessResult:
        if arguments[0] == "agy" and "--output-format" in arguments:
            self.calls.append((arguments, input_text, cwd))
            return ProcessResult(
                returncode=1,
                stdout=json.dumps(
                    {
                        "conversation_id": "conversation-1",
                        "status": "ERROR",
                        "response": "",
                        "error": "Individual quota reached",
                    }
                ),
                stderr="",
            )
        return super().run(
            arguments,
            input_text=input_text,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )


class _CleanScanner:
    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        assert candidate
        return ()


def _write_source(path: Path, text: str) -> SourceDescriptor:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "user",
        "sessionId": "session-1",
        "uuid": "user-1",
        "timestamp": "2026-08-18T00:00:00Z",
        "cwd": "/repo",
        "gitBranch": "main",
        "message": {"role": "user", "content": text},
    }
    path.write_text(f"{json.dumps(record)}\n", encoding="utf-8")
    stat = path.stat()
    return SourceDescriptor(
        tool="claude",
        session_id="session-1",
        path=path,
        working_directories=("/repo",),
        stamp=SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns),
    )


def _annotate_arguments(archive_root: Path) -> list[str]:
    return [
        "annotate",
        "--archive-root",
        str(archive_root),
        "--tool",
        "claude",
        "--session-id",
        "session-1",
        "--agy",
        "agy",
        "--gitleaks",
        "/bin/true",
    ]


def test_agy_schema_constrains_finding_evidence_to_rendered_locators() -> None:
    locator = "line 1, id user-1"

    schema = annotation_json_schema(frozenset({locator}))

    properties = cast("dict[str, object]", schema["properties"])
    findings = cast("dict[str, object]", properties["findings"])
    finding = cast("dict[str, object]", findings["items"])
    finding_properties = cast("dict[str, object]", finding["properties"])
    evidence = cast("dict[str, object]", finding_properties["evidence_locators"])
    repairs = cast("dict[str, object]", finding_properties["repair_locators"])
    assert evidence["items"] == {"type": "string", "enum": [locator]}
    assert evidence["minItems"] == 1
    assert repairs["items"] == {"type": "string", "enum": [locator]}


def test_annotate_cli_publishes_validated_sidecar_and_renders_it(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "transcript.md"
    original = _transcript()
    transcript.write_text(original, encoding="utf-8")
    runner = _AgyRunner(_structured_output())
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)

    exit_code = main(_annotate_arguments(archive_root))

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert captured.err == ""
    assert captured.out == "annotated=claude/session-1\n"
    sidecar = json.loads((session_directory / "annotations.json").read_text(encoding="utf-8"))
    rendered = transcript.read_text(encoding="utf-8")
    agy_arguments, agy_input, agy_cwd = next(
        (arguments, input_text, cwd)
        for arguments, input_text, cwd in runner.calls
        if arguments[0] == "agy" and "--output-format" in arguments
    )
    assert sidecar["schema_version"] == 1
    assert sidecar["source"] == {"tool": "claude", "session_id": "session-1"}
    assert sidecar["base_transcript_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert sidecar["requested_model"] == "gemini-3.7-flash-medium"
    assert sidecar["generator"] == {
        "agy_version": "agy 1.1.13",
        "conversation_id": "conversation-1",
    }
    assert sidecar["review_status"] == "pending"
    assert sidecar["annotation"] == _structured_output()
    assert "## Session annotations" in rendered
    assert "Guard repository writes structurally" in rendered
    assert "Repeated bypasses frustrated the user." in rendered
    assert "Shell redirects keep bypassing the file-edit guard." in rendered
    assert agy_input is not None
    assert original in agy_input
    assert agy_cwd is not None
    assert agy_cwd != session_directory
    assert "-p" not in agy_arguments
    assert "--disable-slash-commands" in agy_arguments
    assert "--mode" in agy_arguments and "plan" in agy_arguments
    assert "--sandbox" in agy_arguments
    assert "--model" in agy_arguments and "gemini-3.7-flash-medium" in agy_arguments
    assert "--json-schema" in agy_arguments
    assert "--add-dir" not in agy_arguments
    assert str(session_directory) not in agy_arguments


def test_annotate_failure_leaves_transcript_and_sidecar_absent(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "transcript.md"
    original = _transcript()
    transcript.write_text(original, encoding="utf-8")
    runner = _FailedAgyRunner(_structured_output())
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)

    exit_code = main(_annotate_arguments(archive_root))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Individual quota reached" in captured.err
    assert transcript.read_text(encoding="utf-8") == original
    assert not (session_directory / "annotations.json").exists()


def test_annotate_rejects_invented_source_evidence_without_writes(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "transcript.md"
    original = _transcript()
    transcript.write_text(original, encoding="utf-8")
    runner = _AgyRunner(_structured_output(locator="line 999, id invented"))
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)

    exit_code = main(_annotate_arguments(archive_root))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unknown transcript locators" in captured.err
    assert transcript.read_text(encoding="utf-8") == original
    assert not (session_directory / "annotations.json").exists()


def test_generation_renders_current_sidecar_and_preserves_stale_sidecar(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    state = archive_root / ".state.json"
    source = _write_source(tmp_path / "source.jsonl", "First request")
    generated = generate_sources(
        (source,),
        archive_root=archive_root,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    runner = _AgyRunner(_structured_output())
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)
    assert main(_annotate_arguments(archive_root)) == 0
    capsys.readouterr()
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    transcript = session_directory / "transcript.md"
    sidecar_path = session_directory / "annotations.json"
    saved_sidecar = sidecar_path.read_bytes()

    current = generate_sources(
        (source,),
        archive_root=archive_root,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    current_content = transcript.read_text(encoding="utf-8")
    unchanged = generate_sources(
        (source,),
        archive_root=archive_root,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )
    _write_source(source.path, "Changed request")
    stale = generate_sources(
        (source,),
        archive_root=archive_root,
        state_path=state,
        rules=(),
        scanner=_CleanScanner(),
    )

    assert generated.rendered == 1
    assert current.rendered == 1
    assert "## Session annotations" in current_content
    assert unchanged.skipped == 1
    assert stale.rendered == 1
    assert "Changed request" in transcript.read_text(encoding="utf-8")
    assert "## Session annotations" not in transcript.read_text(encoding="utf-8")
    assert sidecar_path.read_bytes() == saved_sidecar


def test_review_annotations_marks_current_sidecar_without_calling_agy_again(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "transcript.md"
    transcript.write_text(_transcript(), encoding="utf-8")
    runner = _AgyRunner(_structured_output())
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)
    assert main(_annotate_arguments(archive_root)) == 0
    capsys.readouterr()

    exit_code = main(
        [
            "review-annotations",
            "--archive-root",
            str(archive_root),
            "--tool",
            "claude",
            "--session-id",
            "session-1",
            "--gitleaks",
            "/bin/true",
        ]
    )

    captured = capsys.readouterr()
    sidecar = json.loads((session_directory / "annotations.json").read_text(encoding="utf-8"))
    agy_requests = [
        arguments
        for arguments, _, _ in runner.calls
        if arguments[0] == "agy" and "--output-format" in arguments
    ]
    assert exit_code == 0, captured.err
    assert captured.out == "reviewed-annotations=claude/session-1\n"
    assert sidecar["review_status"] == "reviewed"
    assert "- Review: reviewed" in transcript.read_text(encoding="utf-8")
    assert len(agy_requests) == 1


def test_annotation_markers_quoted_in_dialogue_are_not_treated_as_generated_content() -> None:
    transcript = _transcript().replace(
        "Shell redirects keep bypassing the file-edit guard.",
        f"Quoted markers remain evidence: {ANNOTATION_START} and {ANNOTATION_END}",
    )

    assert strip_annotation_section(transcript) == transcript


def test_three_ps_update_keeps_current_annotation_digest_bound(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    archive_root = tmp_path / "archive"
    session_directory = archive_root / "sessions" / "claude" / "session-1"
    session_directory.mkdir(parents=True)
    transcript = session_directory / "transcript.md"
    transcript.write_text(_transcript(), encoding="utf-8")
    runner = _AgyRunner(_structured_output())
    monkeypatch.setattr(transcript_archive_cli, "SubprocessRunner", lambda: runner)
    assert main(_annotate_arguments(archive_root)) == 0
    capsys.readouterr()
    sidecar_path = session_directory / "annotations.json"
    before = json.loads(sidecar_path.read_text(encoding="utf-8"))

    exit_code = main(
        [
            "update",
            "--archive-root",
            str(archive_root),
            "--tool",
            "claude",
            "--session-id",
            "session-1",
            "--prompt",
            "Reviewed prompt",
            "--process",
            "Reviewed process",
            "--provenance",
            "Reviewed provenance",
        ]
    )

    captured = capsys.readouterr()
    updated = transcript.read_text(encoding="utf-8")
    after = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert exit_code == 0, captured.err
    assert "## Session annotations" in updated
    assert 'prompt_summary: "Reviewed prompt"' in updated
    assert after["annotation"] == before["annotation"]
    assert (
        after["base_transcript_sha256"]
        == hashlib.sha256(strip_annotation_section(updated).encode()).hexdigest()
    )
