"""Behavioral and property tests for transcript redaction policy."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from claude_transcript_archive.model import RawRecord, SourceLocation
from claude_transcript_archive.redaction import (
    GitleaksScanner,
    ProcessResult,
    RedactionError,
    RedactionRule,
    digest_value,
    parse_redactions,
    redact_records,
)


class _FindingRunner:
    def __init__(self) -> None:
        self.arguments: tuple[str, ...] | None = None
        self.input_text: str | None = None

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
    ) -> ProcessResult:
        assert timeout_seconds == 30
        self.arguments = arguments
        self.input_text = input_text
        report = [
            {
                "RuleID": "synthetic-rule",
                "StartLine": 4,
                "EndLine": 4,
                "Secret": "REDACTED",
                "Match": "REDACTED",
            }
        ]
        return ProcessResult(
            returncode=7,
            stdout=json.dumps(report),
            stderr="leaks found",
        )


def test_locator_overlay_redacts_copy_and_emits_public_ledger() -> None:
    protected = "synthetic protected value"
    records = (
        RawRecord(
            order=3,
            value={
                "type": "assistant",
                "uuid": "message-3",
                "message": {"content": [{"type": "text", "text": protected}]},
            },
        ),
    )
    rules = parse_redactions(
        {
            "redactions": [
                {
                    "tool": "claude",
                    "session_id": "session-1",
                    "stable_id": "message-3",
                    "json_pointer": "/message/content/0/text",
                    "digest": digest_value(protected),
                    "reason": "synthetic credential",
                }
            ]
        }
    )

    result = redact_records(
        records,
        rules,
        tool="claude",
        session_id="session-1",
    )

    assert records[0].value["message"] == {"content": [{"type": "text", "text": protected}]}
    assert result.records[0].value["message"] == {
        "content": [
            {
                "type": "text",
                "text": "[REDACTED: synthetic credential]",
            }
        ]
    }
    assert result.applied[0].location == SourceLocation(3, "message-3")
    assert result.applied[0].json_pointer == "/message/content/0/text"
    assert result.applied[0].reason == "synthetic credential"
    assert protected not in repr(rules)


def test_overlay_digest_mismatch_fails_closed() -> None:
    records = (
        RawRecord(
            order=3,
            value={"uuid": "message-3", "message": {"content": "changed"}},
        ),
    )
    rule = RedactionRule(
        tool="claude",
        session_id="session-1",
        source_order=None,
        stable_id="message-3",
        json_pointer="/message/content",
        digest=digest_value("old value"),
        reason="credential",
    )

    with pytest.raises(RedactionError, match=r"digest mismatch.*line 3"):
        redact_records(
            records,
            (rule,),
            tool="claude",
            session_id="session-1",
        )

    assert records[0].value["message"] == {"content": "changed"}


def test_json_pointer_escapes_are_resolved() -> None:
    protected = "protected"
    records = (
        RawRecord(
            order=1,
            value={"a/b": {"c~d": protected}},
        ),
    )
    rule = RedactionRule(
        tool="codex",
        session_id="session-1",
        source_order=1,
        stable_id=None,
        json_pointer="/a~1b/c~0d",
        digest=digest_value(protected),
        reason="credential",
    )

    result = redact_records(
        records,
        (rule,),
        tool="codex",
        session_id="session-1",
    )

    assert result.records[0].value == {"a/b": {"c~d": "[REDACTED: credential]"}}


@given(st.text(min_size=1))
def test_digest_bound_redaction_never_preserves_selected_value(
    protected: str,
) -> None:
    records = (RawRecord(order=1, value={"value": protected}),)
    rule = RedactionRule(
        tool="claude",
        session_id="session-1",
        source_order=1,
        stable_id=None,
        json_pointer="/value",
        digest=digest_value(protected),
        reason="property test",
    )

    result = redact_records(
        records,
        (rule,),
        tool="claude",
        session_id="session-1",
    )

    assert result.records[0].value["value"] == "[REDACTED: property test]"


def test_gitleaks_scans_stdin_and_returns_only_redacted_finding_metadata() -> None:
    runner = _FindingRunner()
    scanner = GitleaksScanner(
        executable="/configured/gitleaks",
        runner=runner,
    )

    findings = scanner.scan("candidate with synthetic credential")

    assert runner.arguments == (
        "/configured/gitleaks",
        "stdin",
        "--no-banner",
        "--no-color",
        "--redact=100",
        "--report-format=json",
        "--report-path=-",
        "--exit-code=7",
    )
    assert runner.input_text == "candidate with synthetic credential"
    assert findings[0].rule_id == "synthetic-rule"
    assert findings[0].start_line == 4
    assert not hasattr(findings[0], "secret")


@pytest.mark.parametrize(
    "entry",
    [
        {
            "tool": "claude",
            "session_id": "session-1",
            "source_order": 1,
            "stable_id": "both",
            "json_pointer": "/value",
            "digest": "sha256:" + ("a" * 64),
            "reason": "invalid selectors",
        },
        {
            "tool": "claude",
            "session_id": "session-1",
            "json_pointer": "/value",
            "digest": "not-a-digest",
            "reason": "missing selector",
        },
    ],
)
def test_invalid_overlay_entries_are_rejected(entry: dict[str, object]) -> None:
    with pytest.raises(RedactionError):
        parse_redactions({"redactions": [entry]})
