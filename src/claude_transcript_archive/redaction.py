"""Locator-bound redaction and generation-time secret scanning.

# pattern: Functional Core
"""

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol, cast

from .model import (
    AppliedRedaction,
    ArchiveError,
    RawRecord,
    SourceLocation,
    SourceTool,
)

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RULE_FIELDS = frozenset(
    {
        "tool",
        "session_id",
        "source_order",
        "stable_id",
        "json_pointer",
        "digest",
        "reason",
    }
)


class RedactionError(ArchiveError):
    """Raised when redaction or candidate secret scanning fails closed."""


@dataclass(frozen=True, slots=True)
class RedactionRule:
    """A locator and digest without the protected source value."""

    tool: SourceTool
    session_id: str
    source_order: int | None
    stable_id: str | None
    json_pointer: str
    digest: str
    reason: str


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Redacted source records plus public ledger entries."""

    records: tuple[RawRecord, ...]
    applied: tuple[AppliedRedaction, ...]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Minimal external-process result used by the scanner port."""

    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    """Port for a fixed-argument text process."""

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
    ) -> ProcessResult:
        """Run one process without a shell."""


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """Redacted Gitleaks metadata safe to include in an error."""

    rule_id: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class GitleaksScanner:
    """Scan an in-memory rendered candidate with Gitleaks."""

    executable: str
    runner: ProcessRunner
    timeout_seconds: int = 30

    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        """Return redacted findings or fail on a scanner error."""
        arguments = (
            self.executable,
            "stdin",
            "--no-banner",
            "--no-color",
            "--redact=100",
            "--report-format=json",
            "--report-path=-",
            "--exit-code=7",
        )
        result = self.runner.run(
            arguments,
            input_text=candidate,
            timeout_seconds=self.timeout_seconds,
        )
        if result.returncode not in {0, 7}:
            detail = normalize_scanner_error(result.stderr)
            raise RedactionError(f"Gitleaks failed with exit {result.returncode}: {detail}")
        report = _parse_gitleaks_report(result.stdout)
        if result.returncode == 0 and report:
            raise RedactionError("Gitleaks returned findings with a clean exit status")
        if result.returncode == 7 and not report:
            raise RedactionError("Gitleaks reported a finding without redacted metadata")
        return report


def normalize_scanner_error(value: str) -> str:
    """Keep scanner diagnostics compact and single-line."""
    normalized = " ".join(value.split())
    if not normalized:
        return "no diagnostic"
    return normalized[:240]


def digest_value(value: str) -> str:
    """Return the overlay digest for one exact protected string."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"sha256:{digest}"


def _required_string(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise RedactionError(f"Redaction field {key} must be a non-empty string")
    return value


def _selector(entry: dict[str, object]) -> tuple[int | None, str | None]:
    source_order = entry.get("source_order")
    stable_id = entry.get("stable_id")
    selected_order = (
        source_order
        if (
            isinstance(source_order, int)
            and not isinstance(source_order, bool)
            and source_order > 0
        )
        else None
    )
    selected_stable = stable_id if isinstance(stable_id, str) and stable_id else None
    if (selected_order is None) == (selected_stable is None):
        raise RedactionError("A redaction needs exactly one of source_order or stable_id")
    return selected_order, selected_stable


def _parse_rule(entry: object) -> RedactionRule:
    if not isinstance(entry, dict):
        raise RedactionError("Each redaction must be a TOML table")
    entry_value = cast("dict[str, object]", entry)
    unknown = set(entry_value) - _RULE_FIELDS
    if unknown:
        raise RedactionError(f"Unknown redaction fields: {', '.join(sorted(unknown))}")
    tool = _required_string(entry_value, "tool")
    if tool not in {"claude", "codex"}:
        raise RedactionError(f"Unsupported redaction tool: {tool}")
    source_order, stable_id = _selector(entry_value)
    pointer = _required_string(entry_value, "json_pointer")
    if not pointer.startswith("/"):
        raise RedactionError("Redaction json_pointer must start with /")
    digest = _required_string(entry_value, "digest")
    if _DIGEST_PATTERN.fullmatch(digest) is None:
        raise RedactionError("Redaction digest must be sha256 plus 64 hex digits")
    reason = _required_string(entry_value, "reason")
    if "\n" in reason or "\r" in reason or len(reason) > 120:
        raise RedactionError("Redaction reason must be one line of at most 120 characters")
    source_tool: SourceTool = "claude" if tool == "claude" else "codex"
    return RedactionRule(
        tool=source_tool,
        session_id=_required_string(entry_value, "session_id"),
        source_order=source_order,
        stable_id=stable_id,
        json_pointer=pointer,
        digest=digest,
        reason=reason,
    )


def parse_redactions(document: object) -> tuple[RedactionRule, ...]:
    """Validate a decoded redactions TOML document."""
    if not isinstance(document, dict):
        raise RedactionError("Redaction overlay must decode to a table")
    document_value = cast("dict[str, object]", document)
    unknown = set(document_value) - {"redactions"}
    if unknown:
        raise RedactionError(f"Unknown overlay fields: {', '.join(sorted(unknown))}")
    entries = document_value.get("redactions", [])
    if not isinstance(entries, list):
        raise RedactionError("redactions must be an array of tables")
    rules = tuple(_parse_rule(entry) for entry in entries)
    if len(set(rules)) != len(rules):
        raise RedactionError("Duplicate redaction rules are not allowed")
    return rules


def _stable_id(record: RawRecord) -> str | None:
    value = record.value
    direct = value.get("uuid")
    if isinstance(direct, str) and direct:
        return direct
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return None
    payload_value = cast("dict[str, object]", payload)
    for key in ("id", "call_id"):
        candidate = payload_value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _matches(record: RawRecord, rule: RedactionRule) -> bool:
    if rule.source_order is not None:
        return record.order == rule.source_order
    return _stable_id(record) == rule.stable_id


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    return tuple(
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer.removeprefix("/").split("/")
    )


def _list_index(token: str, length: int, pointer: str) -> int:
    try:
        index = int(token)
    except ValueError as error:
        raise RedactionError(
            f"JSON pointer {pointer} has non-numeric list index {token}"
        ) from error
    if index < 0 or index >= length:
        raise RedactionError(f"JSON pointer {pointer} list index is out of range")
    return index


def _child(container: object, token: str, pointer: str) -> object:
    if isinstance(container, dict):
        dictionary = cast("dict[str, object]", container)
        if token not in dictionary:
            raise RedactionError(f"JSON pointer {pointer} does not exist")
        return dictionary[token]
    if isinstance(container, list):
        values = cast("list[object]", container)
        return values[_list_index(token, len(values), pointer)]
    raise RedactionError(f"JSON pointer {pointer} traverses a scalar")


def _replace_pointer(
    root: dict[str, object],
    pointer: str,
    replacement: str,
    expected_digest: str,
    *,
    location: SourceLocation,
) -> None:
    tokens = _pointer_tokens(pointer)
    parent: object = root
    for token in tokens[:-1]:
        parent = _child(parent, token, pointer)
    final = tokens[-1]
    current = _child(parent, final, pointer)
    if not isinstance(current, str):
        raise RedactionError(f"Redaction target at {location.label} must be a string")
    if digest_value(current) != expected_digest:
        raise RedactionError(f"Redaction digest mismatch at {location.label}")
    if isinstance(parent, dict):
        cast("dict[str, object]", parent)[final] = replacement
    elif isinstance(parent, list):
        values = cast("list[object]", parent)
        values[_list_index(final, len(values), pointer)] = replacement
    else:
        raise RedactionError(f"JSON pointer {pointer} targets a scalar parent")


def redact_records(
    records: tuple[RawRecord, ...],
    rules: tuple[RedactionRule, ...],
    *,
    tool: SourceTool,
    session_id: str,
) -> RedactionResult:
    """Apply every matching overlay rule exactly once to copied records."""
    applicable = tuple(
        rule for rule in rules if rule.tool == tool and rule.session_id == session_id
    )
    copied = tuple(
        RawRecord(order=record.order, value=copy.deepcopy(record.value)) for record in records
    )
    applied: list[AppliedRedaction] = []
    for rule in applicable:
        matches = [record for record in copied if _matches(record, rule)]
        if len(matches) != 1:
            raise RedactionError(
                f"Redaction {rule.json_pointer} for session {session_id} "
                f"matched {len(matches)} records"
            )
        record = matches[0]
        location = SourceLocation(record.order, _stable_id(record))
        _replace_pointer(
            record.value,
            rule.json_pointer,
            f"[REDACTED: {rule.reason}]",
            rule.digest,
            location=location,
        )
        applied.append(
            AppliedRedaction(
                location=location,
                json_pointer=rule.json_pointer,
                reason=rule.reason,
            )
        )
    return RedactionResult(records=copied, applied=tuple(applied))


def _integer(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise RedactionError(f"Gitleaks finding has invalid {key}")


def _parse_gitleaks_report(value: str) -> tuple[SecretFinding, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise RedactionError("Gitleaks returned invalid JSON") from error
    if not isinstance(decoded, list):
        raise RedactionError("Gitleaks report must be a JSON array")
    findings: list[SecretFinding] = []
    for entry in decoded:
        if not isinstance(entry, dict):
            raise RedactionError("Gitleaks finding must be a JSON object")
        rule_id = entry.get("RuleID")
        if not isinstance(rule_id, str) or not rule_id:
            raise RedactionError("Gitleaks finding has no RuleID")
        findings.append(
            SecretFinding(
                rule_id=rule_id,
                start_line=_integer(entry, "StartLine"),
                end_line=_integer(entry, "EndLine"),
            )
        )
    return tuple(findings)
