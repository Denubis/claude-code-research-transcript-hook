"""Contributor-facing transcript archive generation.

# pattern: Imperative Shell
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from .annotations import (
    DEFAULT_ANNOTATION_MODEL,
    AnnotationError,
    AnnotationSidecar,
    annotation_json_schema,
    annotation_prompt,
    load_sidecar,
    parse_agy_envelope,
    render_annotation,
    reviewed_sidecar,
    sidecar_fingerprint,
    sidecar_json,
    source_locators,
    strip_annotation_section,
    transcript_digest,
)
from .claude import adapt_claude_records, sidechain_shard_omission
from .codex import adapt_codex_records, subagent_rollout_omission
from .discovery import (
    DiscoveryExclusion,
    DiscoveryResult,
    GitRepositoryResolver,
    SourceDescriptor,
    SourceStamp,
    discover_sessions_cached,
    inspect_repository,
    load_raw_records,
)
from .model import ArchiveError, Omission, RawRecord, Session, SourceLocation, SourceTool
from .redaction import (
    GitleaksScanner,
    ProcessResult,
    ProcessRunner,
    RedactionRule,
    SecretFinding,
    normalize_scanner_error,
    parse_redactions,
    redact_records,
)
from .render import RenderedPart, render_session
from .scanner_resolution import UnavailableGitleaksScanner, resolve_gitleaks

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_STATE_VERSION = 5


class CandidateScanner(Protocol):
    """Port for scanning one in-memory rendered candidate."""

    def scan(self, candidate: str) -> tuple[SecretFinding, ...]:
        """Return redacted finding metadata."""


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """One source session that could not be safely generated."""

    tool: str
    session_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Measured result of one deterministic generation run."""

    discovered: int
    rendered: int
    skipped: int
    bytes_written: int
    failures: tuple[SourceFailure, ...]

    @property
    def failed(self) -> int:
        """Return the number of failed source sessions."""
        return len(self.failures)


@dataclass(frozen=True, slots=True)
class SubprocessRunner:
    """Run fixed argument vectors without a shell."""

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int,
        cwd: Path | None = None,
    ) -> ProcessResult:
        """Capture one text process result."""
        try:
            result = subprocess.run(
                arguments,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
                cwd=cwd,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            return ProcessResult(
                returncode=127,
                stdout="",
                stderr=f"Executable not found: {arguments[0]}",
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(
                returncode=124,
                stdout="",
                stderr=f"Command timed out after {timeout_seconds} seconds",
            )
        return ProcessResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


def _source_key(source: SourceDescriptor) -> str:
    return f"{source.tool}:{source.session_id}"


def _current_stamp(path: Path) -> SourceStamp:
    stat = path.stat()
    return SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _source_paths(source: SourceDescriptor) -> tuple[Path, ...]:
    return (source.path, *source.sidechain_shards)


def _current_stamps(
    source: SourceDescriptor,
) -> tuple[tuple[Path, SourceStamp], ...]:
    return tuple((path, _current_stamp(path)) for path in _source_paths(source))


def _stamp_state(
    stamps: tuple[tuple[Path, SourceStamp], ...],
) -> list[dict[str, object]]:
    return [
        {
            "path": str(path),
            "size": stamp.size,
            "mtime_ns": stamp.mtime_ns,
        }
        for path, stamp in stamps
    ]


def _rule_fingerprint(
    rules: tuple[RedactionRule, ...],
    source: SourceDescriptor,
) -> str:
    applicable = [
        {
            "tool": rule.tool,
            "session_id": rule.session_id,
            "source_order": rule.source_order,
            "stable_id": rule.stable_id,
            "json_pointer": rule.json_pointer,
            "digest": rule.digest,
            "reason": rule.reason,
        }
        for rule in rules
        if rule.tool == source.tool and rule.session_id == source.session_id
    ]
    encoded = json.dumps(
        applicable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _empty_state() -> dict[str, object]:
    return {"version": _STATE_VERSION, "sources": {}}


def _load_state(path: Path) -> dict[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(decoded, dict) or decoded.get("version") != _STATE_VERSION:
        return _empty_state()
    sources = decoded.get("sources")
    if not isinstance(sources, dict):
        return _empty_state()
    return decoded


def _state_sources(state: dict[str, object]) -> dict[str, object]:
    sources = state.get("sources")
    return cast("dict[str, object]", sources) if isinstance(sources, dict) else {}


def _outputs_exist(entry: dict[str, object], archive_root: Path) -> bool:
    outputs = entry.get("outputs")
    return (
        isinstance(outputs, list)
        and bool(outputs)
        and all(isinstance(output, str) and (archive_root / output).is_file() for output in outputs)
    )


def _can_skip(
    source: SourceDescriptor,
    stamps: tuple[tuple[Path, SourceStamp], ...],
    fingerprint: str,
    annotation_fingerprint: str | None,
    entry: object,
    archive_root: Path,
) -> bool:
    if not isinstance(entry, dict):
        return False
    entry_value = cast("dict[str, object]", entry)
    return (
        entry_value.get("path") == str(source.path)
        and entry_value.get("inputs") == _stamp_state(stamps)
        and entry_value.get("redactions") == fingerprint
        and entry_value.get("annotations") == annotation_fingerprint
        and entry_value.get("recovered_working_directories")
        == list(source.recovered_working_directories)
        and _outputs_exist(entry_value, archive_root)
    )


def _adapt(
    source: SourceDescriptor,
    records: tuple[RawRecord, ...],
) -> Session:
    if source.tool == "claude":
        return adapt_claude_records(
            records,
            session_id=source.session_id,
            source_path=source.path,
        )
    return adapt_codex_records(
        records,
        session_id=source.session_id,
        source_path=source.path,
    )


def _scan_parts(
    parts: tuple[RenderedPart, ...],
    scanner: CandidateScanner,
) -> None:
    for part in parts:
        findings = scanner.scan(part.content)
        if not findings:
            continue
        finding = findings[0]
        location = part.source_for_line(finding.start_line) or SourceLocation(1)
        raise ArchiveError(
            f"{part.filename} contains Gitleaks rule {finding.rule_id} at {location.label}"
        )


def _safe_session_directory(
    archive_root: Path,
    source: SourceDescriptor,
) -> Path:
    session_id = source.session_id
    if not session_id or session_id in {".", ".."} or Path(session_id).name != session_id:
        raise ArchiveError(f"Unsafe session ID: {session_id}")
    return archive_root / "sessions" / source.tool / session_id


def _temporary_file(session_directory: Path, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=session_directory,
        prefix=".transcript-",
        suffix=".tmp",
        delete=False,
    ) as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
        return Path(target.name)


def _annotation_part(part: RenderedPart, sidecar: AnnotationSidecar) -> RenderedPart:
    content = render_annotation(part.content, sidecar)
    added_lines = len(content.splitlines()) - len(part.content.splitlines())
    anchor_line = part.content.splitlines().index("# Transcript") + 1
    sources = (
        part.line_sources[:anchor_line] + ((None,) * added_lines) + part.line_sources[anchor_line:]
    )
    return RenderedPart(
        filename=part.filename,
        content=content,
        line_sources=sources,
    )


def _load_current_annotation(
    path: Path,
    transcript: str,
    source: SourceDescriptor,
) -> AnnotationSidecar | None:
    if not path.is_file():
        return None
    sidecar = load_sidecar(path, transcript, validate_locators=False)
    if sidecar.source_tool != source.tool or sidecar.session_id != source.session_id:
        raise AnnotationError("Annotation sidecar source identity does not match its directory")
    if sidecar.base_transcript_sha256 != transcript_digest(transcript):
        return None
    return load_sidecar(path, transcript)


def _candidate_part(filename: str, content: str) -> RenderedPart:
    return RenderedPart(
        filename=filename,
        content=content,
        line_sources=tuple(None for _ in content.splitlines()),
    )


def _annotation_scanner(
    explicit: str | None,
    runner: ProcessRunner,
) -> CandidateScanner:
    resolution = resolve_gitleaks(
        explicit,
        environment=os.environ,
        home=Path.home(),
    )
    if resolution.executable is None:
        return UnavailableGitleaksScanner(resolution.failure_message)
    return GitleaksScanner(executable=resolution.executable, runner=runner)


def _agy_version(agy: str, runner: ProcessRunner) -> str | None:
    result = runner.run((agy, "--version"), timeout_seconds=10)
    if result.returncode != 0:
        return None
    value = " ".join(result.stdout.split())
    return value or None


def _agy_failure_detail(result: ProcessResult) -> str:
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, dict):
        error = envelope.get("error")
        if isinstance(error, str) and error.strip():
            return normalize_scanner_error(error)
    return normalize_scanner_error(result.stderr or result.stdout)


def _request_annotation(
    transcript: Path,
    *,
    source: SourceDescriptor,
    agy: str,
    model: str,
    runner: ProcessRunner,
) -> AnnotationSidecar:
    base = strip_annotation_section(transcript.read_text(encoding="utf-8"))
    schema = (
        json.dumps(
            annotation_json_schema(source_locators(base)),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
    with tempfile.TemporaryDirectory(prefix="transcript-annotation-") as temporary:
        isolated_directory = Path(temporary)
        schema_path = isolated_directory / "annotation-schema.json"
        schema_path.write_text(schema, encoding="utf-8")
        result = runner.run(
            (
                agy,
                "--mode",
                "plan",
                "--sandbox",
                "--model",
                model,
                "--output-format",
                "json",
                "--json-schema",
                str(schema_path),
                "--disable-slash-commands",
                "--print-timeout",
                "2m",
            ),
            input_text=annotation_prompt(base),
            timeout_seconds=120,
            cwd=isolated_directory,
        )
    if result.returncode != 0:
        detail = _agy_failure_detail(result)
        raise AnnotationError(f"Agy exited {result.returncode}: {detail}")
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AnnotationError("Agy did not return one valid JSON envelope") from error
    return parse_agy_envelope(
        envelope,
        transcript=base,
        source_tool=source.tool,
        session_id=source.session_id,
        requested_model=model,
        agy_version=_agy_version(agy, runner),
    )


def _publish_annotation(
    transcript: Path,
    sidecar_path: Path,
    *,
    rendered: str,
    sidecar_content: str,
) -> None:
    original_transcript = transcript.read_text(encoding="utf-8")
    transcript_temporary = _temporary_file(transcript.parent, rendered)
    sidecar_temporary = _temporary_file(transcript.parent, sidecar_content)
    try:
        transcript_temporary.replace(transcript)
        try:
            sidecar_temporary.replace(sidecar_path)
        except OSError:
            restoration = _temporary_file(transcript.parent, original_transcript)
            try:
                restoration.replace(transcript)
            finally:
                restoration.unlink(missing_ok=True)
            raise
    finally:
        transcript_temporary.unlink(missing_ok=True)
        sidecar_temporary.unlink(missing_ok=True)


_THREE_PS_KEYS = (
    "prompt_summary",
    "process_summary",
    "provenance_summary",
)


def _front_matter(content: str) -> tuple[list[str], int]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise ArchiveError("Transcript is missing opening frontmatter delimiter")
    try:
        closing = lines.index("---", 1)
    except ValueError as error:
        raise ArchiveError("Transcript is missing closing frontmatter delimiter") from error
    return lines, closing


def _front_matter_value(lines: list[str], closing: int, key: str) -> object | None:
    prefix = f"{key}:"
    matches = [
        line.removeprefix(prefix).strip() for line in lines[1:closing] if line.startswith(prefix)
    ]
    if len(matches) > 1:
        raise ArchiveError(f"Transcript frontmatter repeats {key}")
    if not matches:
        return None
    try:
        return json.loads(matches[0])
    except json.JSONDecodeError as error:
        raise ArchiveError(f"Transcript frontmatter has invalid {key}") from error


def _load_three_ps(path: Path) -> tuple[str, str, str]:
    if not path.exists():
        return "", "", ""
    lines, closing = _front_matter(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for key in _THREE_PS_KEYS:
        value = _front_matter_value(lines, closing, key)
        if value is None:
            values.append("")
        elif isinstance(value, str):
            values.append(value)
        else:
            raise ArchiveError(f"Transcript frontmatter {key} must be a string")
    return values[0], values[1], values[2]


def _set_front_matter_values(content: str, values: dict[str, object]) -> str:
    lines, closing = _front_matter(content)
    indices: dict[str, int] = {}
    for index, line in enumerate(lines[1:closing], start=1):
        key = line.partition(":")[0]
        if key in values:
            if key in indices:
                raise ArchiveError(f"Transcript frontmatter repeats {key}")
            indices[key] = index
    for key, value in values.items():
        rendered = f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        if key in indices:
            lines[indices[key]] = rendered
        else:
            lines.insert(closing, rendered)
            closing += 1
    return "\n".join(lines) + "\n"


def update_three_ps(
    transcript: Path,
    *,
    prompt: str | None = None,
    process: str | None = None,
    provenance: str | None = None,
) -> None:
    """Update Three-Ps frontmatter without creating a metadata sidecar."""
    current = _load_three_ps(transcript)
    summaries = (
        current[0] if prompt is None else prompt,
        current[1] if process is None else process,
        current[2] if provenance is None else provenance,
    )
    base = strip_annotation_section(transcript.read_text(encoding="utf-8"))
    sidecar_path = transcript.parent / "annotations.json"
    current_sidecar: AnnotationSidecar | None = None
    if sidecar_path.is_file():
        candidate = load_sidecar(sidecar_path, base, validate_locators=False)
        if candidate.base_transcript_sha256 == transcript_digest(base):
            current_sidecar = load_sidecar(sidecar_path, base)
    updated = _set_front_matter_values(
        base,
        {
            "prompt_summary": summaries[0],
            "process_summary": summaries[1],
            "provenance_summary": summaries[2],
            "needs_review": not all(value.strip() for value in summaries),
        },
    )
    if current_sidecar is not None:
        updated_sidecar = replace(
            current_sidecar,
            base_transcript_sha256=transcript_digest(updated),
        )
        _publish_annotation(
            transcript,
            sidecar_path,
            rendered=render_annotation(updated, updated_sidecar),
            sidecar_content=sidecar_json(updated_sidecar),
        )
        return
    temporary = _temporary_file(transcript.parent, updated)
    try:
        temporary.replace(transcript)
    finally:
        temporary.unlink(missing_ok=True)


def _load_three_ps_metadata(path: Path) -> tuple[str, str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArchiveError(f"Could not read Three-Ps metadata from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ArchiveError("Three-Ps metadata must be a JSON object")
    expected = {"prompt", "process", "provenance"}
    if set(value) != expected:
        raise ArchiveError("Three-Ps metadata must contain exactly prompt, process, and provenance")
    summaries = tuple(value[key] for key in ("prompt", "process", "provenance"))
    if not all(isinstance(summary, str) for summary in summaries):
        raise ArchiveError("Every Three-Ps metadata value must be a string")
    return cast("tuple[str, str, str]", summaries)


def _publish_parts(
    source: SourceDescriptor,
    parts: tuple[RenderedPart, ...],
    archive_root: Path,
) -> tuple[Path, ...]:
    session_directory = _safe_session_directory(archive_root, source)
    session_directory.mkdir(parents=True, exist_ok=True)
    temporary: list[tuple[Path, Path]] = []
    try:
        for part in parts:
            destination = session_directory / part.filename
            temporary.append((_temporary_file(session_directory, part.content), destination))
        destinations = {destination for _, destination in temporary}
        for temporary_path, destination in temporary:
            temporary_path.replace(destination)
        for stale in session_directory.glob("transcript*.md"):
            if stale not in destinations:
                stale.unlink()
        return tuple(sorted(destinations))
    finally:
        for temporary_path, _ in temporary:
            temporary_path.unlink(missing_ok=True)


def _state_entry(
    source: SourceDescriptor,
    stamps: tuple[tuple[Path, SourceStamp], ...],
    fingerprint: str,
    annotation_fingerprint: str | None,
    outputs: tuple[Path, ...],
    archive_root: Path,
) -> dict[str, object]:
    return {
        "path": str(source.path),
        "inputs": _stamp_state(stamps),
        "redactions": fingerprint,
        "annotations": annotation_fingerprint,
        "recovered_working_directories": list(source.recovered_working_directories),
        "outputs": [str(output.relative_to(archive_root)) for output in sorted(outputs)],
    }


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(state, indent=2, sort_keys=True) + "\n"
    temporary = _temporary_file(path.parent, content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _generate_one(
    source: SourceDescriptor,
    *,
    archive_root: Path,
    rules: tuple[RedactionRule, ...],
    scanner: CandidateScanner,
    loader: Callable[[Path], tuple[RawRecord, ...]],
) -> tuple[tuple[Path, ...], int]:
    before = _current_stamps(source)
    records = loader(source.path)
    sidechain_omissions = tuple(
        omission
        for path in source.sidechain_shards
        if (
            omission := _sidechain_omission(
                source,
                path,
                loader(path),
            )
        )
        is not None
    )
    after = _current_stamps(source)
    if before != after:
        raise ArchiveError(f"Source changed while reading session {source.session_id}")
    redacted = redact_records(
        records,
        rules,
        tool=source.tool,
        session_id=source.session_id,
    )
    adapted = _adapt(source, redacted.records)
    transcript = _safe_session_directory(archive_root, source) / "transcript.md"
    prompt, process, provenance = _load_three_ps(transcript)
    session = replace(
        adapted,
        events=(*adapted.events, *sidechain_omissions),
        recovered_working_directories=source.recovered_working_directories,
        redactions=redacted.applied,
        sidechain_source_paths=source.sidechain_shards,
        prompt_summary=prompt,
        process_summary=process,
        provenance_summary=provenance,
        needs_review=not all(value.strip() for value in (prompt, process, provenance)),
    )
    parts = render_session(session)
    sidecar_path = transcript.parent / "annotations.json"
    current_annotation = _load_current_annotation(
        sidecar_path,
        parts[0].content,
        source,
    )
    if current_annotation is not None:
        parts = (_annotation_part(parts[0], current_annotation),)
    _scan_parts(parts, scanner)
    outputs = _publish_parts(source, parts, archive_root)
    return outputs, sum(len(part.content.encode()) for part in parts)


def _sidechain_omission(
    source: SourceDescriptor,
    path: Path,
    records: tuple[RawRecord, ...],
) -> Omission | None:
    if source.tool == "claude":
        return sidechain_shard_omission(
            records,
            session_id=source.session_id,
            source_path=path,
        )
    return subagent_rollout_omission(
        records,
        parent_session_id=source.session_id,
        source_path=path,
    )


def generate_sources(
    sources: tuple[SourceDescriptor, ...],
    *,
    archive_root: Path,
    state_path: Path,
    rules: tuple[RedactionRule, ...],
    scanner: CandidateScanner,
    loader: Callable[[Path], tuple[RawRecord, ...]] = load_raw_records,
) -> GenerationResult:
    """Generate every changed source and preserve safe per-source failures."""
    previous = _load_state(state_path)
    previous_sources = _state_sources(previous)
    next_sources: dict[str, object] = {}
    failures: list[SourceFailure] = []
    rendered = 0
    skipped = 0
    bytes_written = 0
    for source in sources:
        key = _source_key(source)
        try:
            stamps = _current_stamps(source)
            fingerprint = _rule_fingerprint(rules, source)
            annotation_fingerprint = sidecar_fingerprint(
                _safe_session_directory(archive_root, source) / "annotations.json"
            )
            previous_entry = previous_sources.get(key)
            if _can_skip(
                source,
                stamps,
                fingerprint,
                annotation_fingerprint,
                previous_entry,
                archive_root,
            ):
                next_sources[key] = previous_entry
                skipped += 1
                continue
            outputs, written = _generate_one(
                source,
                archive_root=archive_root,
                rules=rules,
                scanner=scanner,
                loader=loader,
            )
            final_stamps = _current_stamps(source)
            next_sources[key] = _state_entry(
                source,
                final_stamps,
                fingerprint,
                sidecar_fingerprint(
                    _safe_session_directory(archive_root, source) / "annotations.json"
                ),
                outputs,
                archive_root,
            )
            rendered += 1
            bytes_written += written
        except (ArchiveError, OSError, ValueError) as error:
            failures.append(
                SourceFailure(
                    tool=source.tool,
                    session_id=source.session_id,
                    reason=str(error),
                )
            )
    _write_state(
        state_path,
        {"version": _STATE_VERSION, "sources": next_sources},
    )
    return GenerationResult(
        discovered=len(sources),
        rendered=rendered,
        skipped=skipped,
        bytes_written=bytes_written,
        failures=tuple(failures),
    )


def _load_rules(path: Path) -> tuple[RedactionRule, ...]:
    with path.open("rb") as source:
        return parse_redactions(tomllib.load(source))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate repository-local AI session transcripts")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("generate", "update", "annotate", "review-annotations"),
        default="generate",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--source",
        choices=("all", "claude", "codex"),
        default="all",
        help="source store to generate (default: both)",
    )
    parser.add_argument(
        "--claude-root",
        type=Path,
        default=Path.home() / ".claude" / "projects",
    )
    parser.add_argument(
        "--codex-root",
        type=Path,
        default=Path.home() / ".codex" / "sessions",
    )
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--tool", choices=("claude", "codex"))
    parser.add_argument("--session-id")
    parser.add_argument("--prompt")
    parser.add_argument("--process")
    parser.add_argument("--provenance")
    parser.add_argument("--metadata-file", type=Path)
    parser.add_argument("--agy", default="agy")
    parser.add_argument("--model", default=DEFAULT_ANNOTATION_MODEL)
    parser.add_argument("--redactions", type=Path)
    parser.add_argument(
        "--gitleaks",
        help=("explicit Gitleaks executable; otherwise resolve GITLEAKS, PATH, then pre-commit"),
    )
    return parser


def _report(result: GenerationResult) -> str:
    lines = [
        f"discovered={result.discovered}",
        f"rendered={result.rendered}",
        f"skipped={result.skipped}",
        f"bytes_written={result.bytes_written}",
        f"failed={result.failed}",
    ]
    lines.extend(
        f"failure {failure.tool}/{failure.session_id}: {failure.reason}"
        for failure in result.failures
    )
    return "\n".join(lines) + "\n"


def format_discovery_exclusions(
    exclusions: tuple[DiscoveryExclusion, ...],
) -> str:
    """Render deterministic source-membership exclusions without source content."""
    lines = [f"excluded={len(exclusions)}"]
    lines.extend(
        f"exclusion {exclusion.tool}/{exclusion.session_id}: {exclusion.reason} [{exclusion.path}]"
        for exclusion in exclusions
    )
    return "\n".join(lines) + "\n"


def missing_source_roots(
    claude_root: Path,
    codex_root: Path,
    source: str = "all",
) -> tuple[tuple[str, Path], ...]:
    """Name the session stores that are absent, so callers can fail loudly.

    Every caller that discovers from the local stores must ask this first:
    discovery reports zero sources for a missing root, which reads as an
    honest empty result and hides the fact that nothing was searched.
    """
    return tuple(
        (tool, root)
        for tool, root in (("Claude", claude_root), ("Codex", codex_root))
        if source == "all" or tool.lower() == source
        if not root.is_dir()
    )


def _format_missing_source_roots(
    missing_roots: tuple[tuple[str, Path], ...],
) -> str:
    lines = ["source stores are unavailable; missing roots:"]
    lines.extend(f"- {tool}: {root}" for tool, root in missing_roots)
    return "\n".join(lines) + "\n"


def _run_update(options: argparse.Namespace) -> int:
    if options.tool is None or options.session_id is None:
        sys.stderr.write("update requires --tool and --session-id\n")
        return 1
    direct_summaries = (options.prompt, options.process, options.provenance)
    if options.metadata_file is not None and any(
        summary is not None for summary in direct_summaries
    ):
        sys.stderr.write(
            "update accepts either --metadata-file or direct Three-Ps options, not both\n"
        )
        return 1
    try:
        if options.metadata_file is None:
            prompt, process, provenance = direct_summaries
        else:
            prompt, process, provenance = _load_three_ps_metadata(options.metadata_file)
    except ArchiveError as error:
        sys.stderr.write(f"update failed: {error}\n")
        return 1
    runner: ProcessRunner = SubprocessRunner()
    if options.archive_root is None:
        archive_root = inspect_repository(options.repo, runner=runner).root / "ai_transcripts"
    else:
        archive_root = options.archive_root
    source = SourceDescriptor(
        tool=options.tool,
        session_id=options.session_id,
        path=Path("unused"),
        working_directories=(),
        stamp=SourceStamp(size=0, mtime_ns=0),
    )
    transcript = _safe_session_directory(archive_root, source) / "transcript.md"
    try:
        update_three_ps(
            transcript,
            prompt=prompt,
            process=process,
            provenance=provenance,
        )
    except (ArchiveError, OSError, ValueError) as error:
        sys.stderr.write(f"update failed: {error}\n")
        return 1
    sys.stdout.write(f"updated={options.tool}/{options.session_id}\n")
    return 0


def _annotation_source(options: argparse.Namespace) -> SourceDescriptor | None:
    if options.tool is None or options.session_id is None:
        return None
    return SourceDescriptor(
        tool=cast("SourceTool", options.tool),
        session_id=options.session_id,
        path=Path("unused"),
        working_directories=(),
        stamp=SourceStamp(size=0, mtime_ns=0),
    )


def _archive_root(options: argparse.Namespace, runner: ProcessRunner) -> Path:
    if options.archive_root is not None:
        return options.archive_root
    return inspect_repository(options.repo, runner=runner).root / "ai_transcripts"


def _annotation_paths(
    options: argparse.Namespace,
    runner: ProcessRunner,
    source: SourceDescriptor,
) -> tuple[Path, Path]:
    session_directory = _safe_session_directory(_archive_root(options, runner), source)
    return session_directory / "transcript.md", session_directory / "annotations.json"


def _run_annotate(options: argparse.Namespace) -> int:
    source = _annotation_source(options)
    if source is None:
        sys.stderr.write("annotate requires --tool and --session-id\n")
        return 1
    runner: ProcessRunner = SubprocessRunner()
    transcript, sidecar_path = _annotation_paths(options, runner, source)
    try:
        sidecar = _request_annotation(
            transcript,
            source=source,
            agy=options.agy,
            model=options.model,
            runner=runner,
        )
        current = transcript.read_text(encoding="utf-8")
        rendered = render_annotation(current, sidecar)
        sidecar_content = sidecar_json(sidecar)
        scanner = _annotation_scanner(options.gitleaks, runner)
        _scan_parts(
            (
                _candidate_part("transcript.md", rendered),
                _candidate_part("annotations.json", sidecar_content),
            ),
            scanner,
        )
        _publish_annotation(
            transcript,
            sidecar_path,
            rendered=rendered,
            sidecar_content=sidecar_content,
        )
    except (ArchiveError, OSError, ValueError) as error:
        sys.stderr.write(f"annotate failed: {error}\n")
        return 1
    sys.stdout.write(f"annotated={source.tool}/{source.session_id}\n")
    return 0


def _run_review_annotations(options: argparse.Namespace) -> int:
    source = _annotation_source(options)
    if source is None:
        sys.stderr.write("review-annotations requires --tool and --session-id\n")
        return 1
    runner: ProcessRunner = SubprocessRunner()
    transcript, sidecar_path = _annotation_paths(options, runner, source)
    try:
        current = transcript.read_text(encoding="utf-8")
        base = strip_annotation_section(current)
        sidecar = load_sidecar(sidecar_path, base)
        if sidecar.source_tool != source.tool or sidecar.session_id != source.session_id:
            raise AnnotationError("Annotation sidecar source identity does not match its directory")
        reviewed = reviewed_sidecar(sidecar)
        rendered = render_annotation(current, reviewed)
        sidecar_content = sidecar_json(reviewed)
        scanner = _annotation_scanner(options.gitleaks, runner)
        _scan_parts(
            (
                _candidate_part("transcript.md", rendered),
                _candidate_part("annotations.json", sidecar_content),
            ),
            scanner,
        )
        _publish_annotation(
            transcript,
            sidecar_path,
            rendered=rendered,
            sidecar_content=sidecar_content,
        )
    except (ArchiveError, OSError, ValueError) as error:
        sys.stderr.write(f"review-annotations failed: {error}\n")
        return 1
    sys.stdout.write(f"reviewed-annotations={source.tool}/{source.session_id}\n")
    return 0


def _run_generate(options: argparse.Namespace) -> int:
    missing_roots = missing_source_roots(
        options.claude_root,
        options.codex_root,
        options.source,
    )
    if missing_roots:
        sys.stderr.write(_format_missing_source_roots(missing_roots))
        return 1

    runner = SubprocessRunner()
    gitleaks = resolve_gitleaks(
        options.gitleaks,
        environment=os.environ,
        home=Path.home(),
    )
    scanner: CandidateScanner
    if gitleaks.executable is None:
        scanner = UnavailableGitleaksScanner(gitleaks.failure_message)
    else:
        scanner = GitleaksScanner(
            executable=gitleaks.executable,
            runner=runner,
        )
    identity = inspect_repository(options.repo, runner=runner)
    resolver = GitRepositoryResolver(runner)
    archive_root = options.archive_root or identity.root / "ai_transcripts"
    discovery = discover_sessions_cached(
        identity,
        resolver=resolver,
        claude_root=options.claude_root,
        codex_root=options.codex_root,
        cache_path=archive_root / ".discovery.json",
    )
    if options.source != "all":
        discovery = DiscoveryResult(
            sources=tuple(source for source in discovery.sources if source.tool == options.source),
            exclusions=tuple(
                exclusion for exclusion in discovery.exclusions if exclusion.tool == options.source
            ),
        )
    redactions_path = options.redactions or archive_root / "redactions.toml"
    rules = (
        ()
        if options.redactions is None and not redactions_path.exists()
        else _load_rules(redactions_path)
    )
    result = generate_sources(
        discovery.sources,
        archive_root=archive_root,
        state_path=archive_root
        / (".state.json" if options.source == "all" else f".state-{options.source}.json"),
        rules=rules,
        scanner=scanner,
    )
    sys.stdout.write(format_discovery_exclusions(discovery.exclusions))
    sys.stdout.write(_report(result))
    return 1 if result.failures else 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run deterministic discovery and generation."""
    options = _parser().parse_args(arguments)
    if options.command == "update":
        return _run_update(options)
    if options.command == "annotate":
        return _run_annotate(options)
    if options.command == "review-annotations":
        return _run_review_annotations(options)
    return _run_generate(options)


if __name__ == "__main__":
    raise SystemExit(main())
