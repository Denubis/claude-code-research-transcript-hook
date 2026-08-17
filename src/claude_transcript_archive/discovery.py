"""Repository-wide source-session discovery.

# pattern: Imperative Shell
"""

import dataclasses
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

from .model import ArchiveError, RawRecord, SourceTool

if TYPE_CHECKING:
    from .redaction import ProcessRunner


class DiscoveryError(ArchiveError):
    """Raised when source ownership cannot be determined without guessing."""


class RepositoryResolver(Protocol):
    """Port for resolving a path to its Git common directory."""

    def common_dir_for(self, path: Path) -> Path | None:
        """Return the shared Git directory or None for a non-repository path."""


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """Stable identity and registered checkouts for the target repository."""

    root: Path
    common_git_dir: Path
    worktrees: tuple[Path, ...]
    normalized_remotes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceStamp:
    """One source's cheap change detector."""

    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """A discovered vendor session source."""

    tool: SourceTool
    session_id: str
    path: Path
    working_directories: tuple[str, ...]
    stamp: SourceStamp
    recovered_working_directories: tuple[str, ...] = ()
    sidechain_shards: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryExclusion:
    """A source excluded after conflicting or insufficient membership evidence."""

    tool: SourceTool
    session_id: str
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """All source sessions positively attributed to a repository."""

    sources: tuple[SourceDescriptor, ...]
    exclusions: tuple[DiscoveryExclusion, ...] = ()


@dataclass(frozen=True, slots=True)
class _CodexCandidate:
    descriptor: SourceDescriptor
    parent_session_id: str | None


@dataclass(frozen=True, slots=True)
class _DiscoveryClassification:
    candidate: _CodexCandidate | None
    exclusion: DiscoveryExclusion | None


@dataclass(frozen=True, slots=True)
class _PathEvidence:
    """Repository membership and any vanished paths supporting it."""

    match: bool | None
    recovered_working_directories: tuple[str, ...] = ()


_DISCOVERY_CACHE_VERSION = 2


@dataclass(frozen=True, slots=True)
class GitRepositoryResolver:
    """Resolve repository membership through Git."""

    runner: ProcessRunner
    executable: str = "git"

    def common_dir_for(self, path: Path) -> Path | None:
        """Resolve an existing path or its nearest existing parent."""
        existing = _nearest_existing_parent(path)
        if existing is None:
            return None
        result = self.runner.run(
            (
                self.executable,
                "-C",
                str(existing),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ),
            timeout_seconds=10,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return _normalized_path(Path(value)) if value else None


def _normalized_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = _normalized_path(path)
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent
    return candidate


def normalize_repository_url(value: str) -> str:
    """Normalize common SSH and HTTPS Git remote spellings."""
    cleaned = value.strip().rstrip("/")
    if "://" not in cleaned and ":" in cleaned:
        user_host, path = cleaned.split(":", maxsplit=1)
        host = user_host.rsplit("@", maxsplit=1)[-1]
        normalized = f"{host}/{path}"
    else:
        parsed = urlsplit(cleaned)
        if parsed.scheme == "file":
            normalized = parsed.path
        elif parsed.scheme:
            host = parsed.hostname or ""
            normalized = f"{host}/{parsed.path.lstrip('/')}"
        else:
            normalized = cleaned
    normalized = normalized.rstrip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/").lower()


def _decode_line(path: Path, line: str, order: int) -> dict[str, object]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise DiscoveryError(f"Invalid JSON in {path} at line {order}") from error
    if not isinstance(value, dict):
        raise DiscoveryError(f"Non-object JSON in {path} at line {order}")
    return value


def load_raw_records(path: Path) -> tuple[RawRecord, ...]:
    """Decode a JSONL file while preserving one-based source order."""
    records: list[RawRecord] = []
    with path.open(encoding="utf-8") as source:
        for order, line in enumerate(source, start=1):
            if not line.strip():
                continue
            records.append(RawRecord(order=order, value=_decode_line(path, line, order)))
    return tuple(records)


def load_first_raw_record(path: Path) -> RawRecord | None:
    """Decode only the first non-blank JSONL record."""
    record, error = _load_first_discovery_record(path)
    if error is not None:
        raise error
    return record


def _load_discovery_records(
    path: Path,
) -> tuple[tuple[RawRecord, ...], DiscoveryError | None]:
    records: list[RawRecord] = []
    with path.open(encoding="utf-8") as source:
        for order, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = _decode_line(path, line, order)
            except DiscoveryError as error:
                return tuple(records), error
            records.append(RawRecord(order=order, value=value))
    return tuple(records), None


def _load_first_discovery_record(
    path: Path,
) -> tuple[RawRecord | None, DiscoveryError | None]:
    with path.open(encoding="utf-8") as source:
        for order, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = _decode_line(path, line, order)
            except DiscoveryError as error:
                return None, error
            return RawRecord(order=order, value=value), None
    return None, None


def _source_stamp(path: Path) -> SourceStamp:
    stat = path.stat()
    return SourceStamp(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _ordered_cwds(records: tuple[RawRecord, ...]) -> tuple[str, ...]:
    values: dict[str, None] = {}
    for record in records:
        cwd = record.value.get("cwd")
        if isinstance(cwd, str) and cwd:
            values[cwd] = None
        payload = record.value.get("payload")
        if isinstance(payload, dict):
            cwd = cast("dict[str, object]", payload).get("cwd")
            if isinstance(cwd, str) and cwd:
                values[cwd] = None
    return tuple(values)


def _path_evidence(
    directories: tuple[str, ...],
    identity: RepositoryIdentity,
    resolver: RepositoryResolver,
) -> _PathEvidence:
    saw_other = False
    saw_match = False
    recovered: list[str] = []
    expected = _normalized_path(identity.common_git_dir)
    for directory in directories:
        common_dir = resolver.common_dir_for(Path(directory))
        if common_dir is None:
            continue
        if _normalized_path(common_dir) == expected:
            saw_match = True
            if not Path(directory).expanduser().exists():
                recovered.append(directory)
        else:
            saw_other = True
    match = True if saw_match else False if saw_other else None
    return _PathEvidence(
        match=match,
        recovered_working_directories=tuple(recovered),
    )


def _remote_evidence(
    remote: object,
    identity: RepositoryIdentity,
) -> bool | None:
    if not isinstance(remote, str) or not remote:
        return None
    normalized = normalize_repository_url(remote)
    return normalized in identity.normalized_remotes


def _claude_descriptor(
    path: Path,
    identity: RepositoryIdentity,
    resolver: RepositoryResolver,
) -> SourceDescriptor | None:
    records, decoding_error = _load_discovery_records(path)
    directories = _ordered_cwds(records)
    path_evidence = _path_evidence(directories, identity, resolver)
    if path_evidence.match is not True:
        return None
    if decoding_error is not None:
        raise decoding_error
    seen_ids = {
        session_id for record in records if (session_id := _claude_session_id(record)) is not None
    }
    session_id = path.stem
    if seen_ids and seen_ids != {session_id}:
        raise DiscoveryError(f"Claude source {path} has inconsistent session IDs")
    return SourceDescriptor(
        tool="claude",
        session_id=session_id,
        path=path,
        working_directories=directories,
        stamp=_source_stamp(path),
        recovered_working_directories=(path_evidence.recovered_working_directories),
        sidechain_shards=_claude_sidechain_shards(path),
    )


def _claude_session_id(record: RawRecord) -> str | None:
    canonical = record.value.get("sessionId")
    if isinstance(canonical, str) and canonical:
        return canonical
    fallback = record.value.get("session_id")
    return fallback if isinstance(fallback, str) and fallback else None


def _first_codex_metadata(
    path: Path,
    records: tuple[RawRecord, ...],
) -> dict[str, object]:
    if not records or records[0].value.get("type") != "session_meta":
        raise DiscoveryError(f"Codex source {path} does not start with session_meta")
    payload = records[0].value.get("payload")
    if not isinstance(payload, dict):
        raise DiscoveryError(f"Codex source {path} has invalid session_meta")
    return cast("dict[str, object]", payload)


def _codex_session_id(path: Path, metadata: dict[str, object]) -> str:
    for key in ("id", "session_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    raise DiscoveryError(f"Codex source {path} has no session ID")


def _codex_parent_session_id(
    path: Path,
    metadata: dict[str, object],
) -> str | None:
    source = metadata.get("source")
    if not isinstance(source, dict) or "subagent" not in source:
        return None
    parent = metadata.get("session_id")
    if not isinstance(parent, str) or not parent:
        raise DiscoveryError(f"Codex sub-agent source {path} has no parent session_id")
    if parent == _codex_session_id(path, metadata):
        raise DiscoveryError(f"Codex sub-agent source {path} names itself as its parent")
    return parent


def _codex_is_subagent(metadata: dict[str, object]) -> bool:
    source = metadata.get("source")
    return isinstance(source, dict) and "subagent" in source


def _codex_remote(metadata: dict[str, object]) -> object:
    git = metadata.get("git")
    return cast("dict[str, object]", git).get("repository_url") if isinstance(git, dict) else None


def _codex_descriptor(
    path: Path,
    identity: RepositoryIdentity,
    resolver: RepositoryResolver,
) -> tuple[_CodexCandidate | None, DiscoveryExclusion | None]:
    first, first_error = _load_first_discovery_record(path)
    if first is None:
        return None, None
    first_records = (first,)
    metadata = _first_codex_metadata(path, first_records)
    if _codex_is_subagent(metadata):
        records, decoding_error = first_records, first_error
    else:
        records, decoding_error = _load_discovery_records(path)
    session_id = _codex_session_id(path, metadata)
    directories = _ordered_cwds(records)
    path_evidence = _path_evidence(directories[:1], identity, resolver)
    path_match = path_evidence.match
    remote_match = _remote_evidence(_codex_remote(metadata), identity)
    exclusion = _codex_membership_exclusion(
        path=path,
        session_id=session_id,
        directories=directories,
        path_match=path_match,
        remote_match=remote_match,
    )
    if exclusion is not None:
        return None, exclusion
    if path_match is not True:
        return None, None
    if decoding_error is not None:
        raise decoding_error
    parent_session_id = _codex_parent_session_id(path, metadata)
    return (
        _CodexCandidate(
            descriptor=SourceDescriptor(
                tool="codex",
                session_id=session_id,
                path=path,
                working_directories=directories,
                stamp=_source_stamp(path),
                recovered_working_directories=(path_evidence.recovered_working_directories),
            ),
            parent_session_id=parent_session_id,
        ),
        None,
    )


def _codex_membership_exclusion(
    *,
    path: Path,
    session_id: str,
    directories: tuple[str, ...],
    path_match: bool | None,
    remote_match: bool | None,
) -> DiscoveryExclusion | None:
    cwd = directories[0] if directories else "unavailable"
    if path_match is False and remote_match is True:
        reason = (
            f"recorded cwd {cwd} resolves to a different Git common directory; "
            "matching remote is corroboration only"
        )
    elif path_match is None and remote_match is True:
        reason = (
            f"recorded cwd {cwd} has no Git common-directory membership "
            "evidence; matching remote is corroboration only"
        )
    elif path_match is True and remote_match is False:
        reason = (
            f"recorded cwd {cwd} belongs to the target Git common directory "
            "but session metadata has a conflicting remote"
        )
    else:
        return None
    return DiscoveryExclusion(
        tool="codex",
        session_id=session_id,
        path=path,
        reason=reason,
    )


def _jsonl_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    return tuple(sorted(root.rglob("*.jsonl")))


def _claude_session_files(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in _jsonl_files(root) if path.parent.name != "subagents")


def _claude_sidechain_shards(parent: Path) -> tuple[Path, ...]:
    shard_directory = parent.parent / parent.stem / "subagents"
    if not shard_directory.is_dir():
        return ()
    return tuple(sorted(shard_directory.glob("agent-*.jsonl")))


def discover_sessions(
    identity: RepositoryIdentity,
    *,
    resolver: RepositoryResolver,
    claude_root: Path,
    codex_root: Path,
) -> DiscoveryResult:
    """Discover every source session positively attributed to the repository."""
    classifications: list[_DiscoveryClassification] = []
    for path in _claude_session_files(claude_root):
        descriptor = _claude_descriptor(path, identity, resolver)
        candidate = (
            _CodexCandidate(descriptor=descriptor, parent_session_id=None)
            if descriptor is not None
            else None
        )
        classifications.append(_DiscoveryClassification(candidate=candidate, exclusion=None))
    for path in _jsonl_files(codex_root):
        candidate, exclusion = _codex_descriptor(path, identity, resolver)
        classifications.append(
            _DiscoveryClassification(
                candidate=candidate,
                exclusion=exclusion,
            )
        )
    return _finalize_discovery(classifications)


def _finalize_discovery(
    classifications: list[_DiscoveryClassification],
) -> DiscoveryResult:
    sources: list[SourceDescriptor] = []
    codex_sidechains: list[_CodexCandidate] = []
    exclusions: list[DiscoveryExclusion] = []
    for classification in classifications:
        candidate = classification.candidate
        if candidate is not None and candidate.parent_session_id is None:
            sources.append(candidate.descriptor)
        elif candidate is not None:
            codex_sidechains.append(candidate)
        if classification.exclusion is not None:
            exclusions.append(classification.exclusion)
    sources = _attach_codex_sidechains(
        sources,
        codex_sidechains,
        exclusions,
    )
    sources.sort(key=lambda source: (source.tool, source.session_id))
    seen: set[tuple[SourceTool, str]] = set()
    for source in sources:
        key = (source.tool, source.session_id)
        if key in seen:
            raise DiscoveryError(f"Duplicate {source.tool} session source: {source.session_id}")
        seen.add(key)
    exclusions.sort(
        key=lambda exclusion: (
            exclusion.tool,
            exclusion.session_id,
            str(exclusion.path),
        )
    )
    return DiscoveryResult(
        sources=tuple(sources),
        exclusions=tuple(exclusions),
    )


def _cache_identity(identity: RepositoryIdentity) -> dict[str, object]:
    return {
        "root": str(identity.root),
        "common_git_dir": str(identity.common_git_dir),
        "worktrees": [str(path) for path in identity.worktrees],
        "normalized_remotes": list(identity.normalized_remotes),
    }


def _load_discovery_cache(
    path: Path,
    identity: RepositoryIdentity,
) -> dict[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    if decoded.get("version") != _DISCOVERY_CACHE_VERSION:
        return {}
    if decoded.get("identity") != _cache_identity(identity):
        return {}
    entries = decoded.get("entries")
    return cast("dict[str, object]", entries) if isinstance(entries, dict) else {}


def _string_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return tuple(cast("list[str]", value))


def _cached_classification(
    *,
    path: Path,
    vendor: SourceTool,
    stamp: SourceStamp,
    shards: tuple[Path, ...],
    entry: object,
) -> _DiscoveryClassification | None:
    if not isinstance(entry, dict):
        return None
    value = cast("dict[str, object]", entry)
    if (
        value.get("vendor") != vendor
        or value.get("size") != stamp.size
        or value.get("mtime_ns") != stamp.mtime_ns
        or value.get("shards") != [str(shard) for shard in shards]
    ):
        return None
    kind = value.get("kind")
    if kind == "unrelated":
        return _DiscoveryClassification(candidate=None, exclusion=None)
    if kind == "exclusion":
        return _cached_exclusion(
            path=path,
            vendor=vendor,
            value=value,
        )
    return _cached_candidate(
        path=path,
        vendor=vendor,
        stamp=stamp,
        shards=shards,
        value=value,
    )


def _cached_exclusion(
    *,
    path: Path,
    vendor: SourceTool,
    value: dict[str, object],
) -> _DiscoveryClassification | None:
    session_id = value.get("session_id")
    reason = value.get("reason")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(reason, str)
        or not reason
    ):
        return None
    return _DiscoveryClassification(
        candidate=None,
        exclusion=DiscoveryExclusion(
            tool=vendor,
            session_id=session_id,
            path=path,
            reason=reason,
        ),
    )


def _cached_candidate(
    *,
    path: Path,
    vendor: SourceTool,
    stamp: SourceStamp,
    shards: tuple[Path, ...],
    value: dict[str, object],
) -> _DiscoveryClassification | None:
    session_id = value.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    kind = value.get("kind")
    directories = _string_tuple(value.get("working_directories"))
    recovered = _string_tuple(value.get("recovered_working_directories"))
    if kind not in {"source", "sidechain"} or directories is None or recovered is None:
        return None
    parent = value.get("parent_session_id")
    if kind == "sidechain" and not isinstance(parent, str):
        return None
    descriptor = SourceDescriptor(
        tool=vendor,
        session_id=session_id,
        path=path,
        working_directories=directories,
        stamp=stamp,
        recovered_working_directories=recovered,
        sidechain_shards=shards,
    )
    return _DiscoveryClassification(
        candidate=_CodexCandidate(
            descriptor=descriptor,
            parent_session_id=parent if isinstance(parent, str) else None,
        ),
        exclusion=None,
    )


def _cache_entry(
    *,
    vendor: SourceTool,
    stamp: SourceStamp,
    shards: tuple[Path, ...],
    classification: _DiscoveryClassification,
) -> dict[str, object]:
    base: dict[str, object] = {
        "vendor": vendor,
        "size": stamp.size,
        "mtime_ns": stamp.mtime_ns,
        "shards": [str(shard) for shard in shards],
    }
    candidate = classification.candidate
    exclusion = classification.exclusion
    if candidate is not None:
        base.update(
            {
                "kind": ("sidechain" if candidate.parent_session_id is not None else "source"),
                "session_id": candidate.descriptor.session_id,
                "working_directories": list(candidate.descriptor.working_directories),
                "recovered_working_directories": list(
                    candidate.descriptor.recovered_working_directories
                ),
                "parent_session_id": candidate.parent_session_id,
            }
        )
    elif exclusion is not None:
        base.update(
            {
                "kind": "exclusion",
                "session_id": exclusion.session_id,
                "reason": exclusion.reason,
            }
        )
    else:
        base["kind"] = "unrelated"
    return base


def _classify_source(
    *,
    vendor: SourceTool,
    path: Path,
    identity: RepositoryIdentity,
    resolver: RepositoryResolver,
) -> _DiscoveryClassification:
    if vendor == "claude":
        descriptor = _claude_descriptor(path, identity, resolver)
        candidate = (
            _CodexCandidate(descriptor=descriptor, parent_session_id=None)
            if descriptor is not None
            else None
        )
        return _DiscoveryClassification(candidate=candidate, exclusion=None)
    candidate, exclusion = _codex_descriptor(path, identity, resolver)
    return _DiscoveryClassification(
        candidate=candidate,
        exclusion=exclusion,
    )


def _write_discovery_cache(
    path: Path,
    identity: RepositoryIdentity,
    entries: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            {
                "version": _DISCOVERY_CACHE_VERSION,
                "identity": _cache_identity(identity),
                "entries": entries,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".discovery-",
        suffix=".tmp",
        delete=False,
    ) as target:
        target.write(content)
        temporary = Path(target.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def discover_sessions_cached(
    identity: RepositoryIdentity,
    *,
    resolver: RepositoryResolver,
    claude_root: Path,
    codex_root: Path,
    cache_path: Path,
) -> DiscoveryResult:
    """Discover sessions while opening only new or stat-changed JSONL files."""
    previous = _load_discovery_cache(cache_path, identity)
    entries: dict[str, object] = {}
    classifications: list[_DiscoveryClassification] = []
    candidates = (
        *(("claude", path) for path in _claude_session_files(claude_root)),
        *(("codex", path) for path in _jsonl_files(codex_root)),
    )
    for vendor, path in candidates:
        stamp = _source_stamp(path)
        shards = _claude_sidechain_shards(path) if vendor == "claude" else ()
        classification = _cached_classification(
            path=path,
            vendor=vendor,
            stamp=stamp,
            shards=shards,
            entry=previous.get(str(path)),
        )
        if classification is None:
            classification = _classify_source(
                vendor=vendor,
                path=path,
                identity=identity,
                resolver=resolver,
            )
        entries[str(path)] = _cache_entry(
            vendor=vendor,
            stamp=stamp,
            shards=shards,
            classification=classification,
        )
        classifications.append(classification)
    result = _finalize_discovery(classifications)
    _write_discovery_cache(cache_path, identity, entries)
    return result


def _attach_codex_sidechains(
    sources: list[SourceDescriptor],
    sidechains: list[_CodexCandidate],
    exclusions: list[DiscoveryExclusion],
) -> list[SourceDescriptor]:
    positions = {(source.tool, source.session_id): index for index, source in enumerate(sources)}
    for sidechain in sidechains:
        parent_id = sidechain.parent_session_id
        position = positions.get(("codex", parent_id or ""))
        if position is None:
            exclusions.append(
                DiscoveryExclusion(
                    tool="codex",
                    session_id=sidechain.descriptor.session_id,
                    path=sidechain.descriptor.path,
                    reason=(
                        f"sub-agent rollout parent {parent_id} is not a "
                        "discovered repository session"
                    ),
                )
            )
            continue
        parent = sources[position]
        sources[position] = dataclasses.replace(
            parent,
            sidechain_shards=tuple(sorted((*parent.sidechain_shards, sidechain.descriptor.path))),
        )
    return sources


def _git_output(
    runner: ProcessRunner,
    executable: str,
    root: Path,
    *arguments: str,
) -> str:
    result = runner.run(
        (executable, "-C", str(root), *arguments),
        timeout_seconds=10,
    )
    if result.returncode != 0:
        raise DiscoveryError(f"Git {' '.join(arguments)} failed with exit {result.returncode}")
    return result.stdout


def _parse_worktrees(value: str) -> tuple[Path, ...]:
    return tuple(
        _normalized_path(Path(line.removeprefix("worktree ")))
        for line in value.splitlines()
        if line.startswith("worktree ")
    )


def _parse_remotes(value: str) -> tuple[str, ...]:
    remotes: dict[str, None] = {}
    for line in value.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) == 2:
            remotes[normalize_repository_url(fields[1])] = None
    return tuple(remotes)


def inspect_repository(
    root: Path,
    *,
    runner: ProcessRunner,
    executable: str = "git",
) -> RepositoryIdentity:
    """Inspect the target repository through documented Git interfaces."""
    top_level = Path(
        _git_output(
            runner,
            executable,
            root,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
        ).strip()
    )
    common_dir = Path(
        _git_output(
            runner,
            executable,
            top_level,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ).strip()
    )
    worktrees = _parse_worktrees(
        _git_output(
            runner,
            executable,
            top_level,
            "worktree",
            "list",
            "--porcelain",
        )
    )
    remote_result = runner.run(
        (
            executable,
            "-C",
            str(top_level),
            "config",
            "--get-regexp",
            r"^remote\..*\.url$",
        ),
        timeout_seconds=10,
    )
    remotes = _parse_remotes(remote_result.stdout)
    return RepositoryIdentity(
        root=_normalized_path(top_level),
        common_git_dir=_normalized_path(common_dir),
        worktrees=worktrees or (_normalized_path(top_level),),
        normalized_remotes=remotes,
    )
