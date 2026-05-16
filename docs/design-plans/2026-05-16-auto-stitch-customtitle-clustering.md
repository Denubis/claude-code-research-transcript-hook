# Auto-Stitch via `customTitle` Clustering Design

**GitHub Issue:** None

## Summary

When a single research conversation spans many Claude Code sessions (typically because context-window pressure forces a `claude --resume` or a fresh process with a `@docs/WIP-resume-prompt-*.md` hand-off), the current archive tool produces one directory per session. Three Ps metadata is collected per session, which fragments the research provenance: a 10-session conversation captures the user's intent at 10 disjoint moments rather than once over the whole arc.

This design adds **auto-stitch**: sessions that share the same `customTitle` (a stable per-session string Claude Code writes on every JSONL entry, set by tools like `/exec-session-naming`) in the same project are archived as a single stitched cluster — one directory, one concatenated `raw-transcript.jsonl`, one set of Three Ps, all constituent UUIDs listed in `_constituent_sessions` and fanned into the manifest. The schema matches the hand-rolled cluster (`ai_transcripts/2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/`) the user already produced manually for the MELICA `dvc-sciencedata-archive` arc.

## Definition of Done

1. **Same-`customTitle` sessions cluster automatically.** `bulk` groups unarchived sessions by `customTitle` (within one project) and emits one stitched directory per group with 2+ members. Singletons (one session, or no `customTitle`) keep the current single-session archive format.

2. **Schema matches the MELICA hand-rolled cluster verbatim.** Stitched archives emit `session.meta.json` with: `archive.stitched: true`, `_constituent_sessions: [{id, rank}, ...]` (chronological), `artifacts.primary_jsonl` naming the first constituent's filename, aggregated `statistics` (turns, user_messages, assistant_messages, jsonl_lines, raw_transcript_bytes, raw_transcript_sha256), Three Ps extended with optional `title` and `tags` fields. Singletons keep the current schema unchanged.

3. **Hook-path promotion preserves prior singletons.** When a session arrives via the Stop hook (one at a time) and shares `customTitle` with a previously-archived singleton, `archive()` promotes the singleton to a cluster in place: appends the new JSONL, rewrites `session.meta.json` to the stitched schema, fans the new UUID into the manifest pointing at the same directory.

4. **Manual stitch for edge cases.** A new `claude-research-transcript stitch --into <cluster-uuid> <session-uuid> [<session-uuid> ...]` command force-adds sessions to an existing cluster regardless of `customTitle`. Covers cases like the MELICA chain's first session (`dad509ba`), which predates the `customTitle` convention.

5. **`status` reports clusters as one row.** Stitched archives appear once in `status`, not N times. Constituent UUIDs are listed underneath. Manifest fan-in is hidden from the user-facing count.

6. **Three Ps run on the full convo.** When `/transcript` (the interactive flow) fires against any constituent session UUID, it operates on the cluster's concatenated `raw-transcript.jsonl` and writes Three Ps to the cluster's `session.meta.json`. All constituent UUIDs share the same Three Ps via cluster identity.

7. **`regenerate` works on stitched archives.** Re-renders `index.html`, `page-*.html`, `conversation.md`, `conversation.pdf` from the concatenated transcript. Existing normalisation guarantees no trailing whitespace.

## Acceptance Criteria

### auto-stitch.AC1: Bulk groups same-customTitle sessions
- **auto-stitch.AC1.1 Success:** Two unarchived sessions with identical `customTitle` in the same project produce one stitched archive directory after `bulk`, not two singletons.
- **auto-stitch.AC1.2 Success:** Three sessions with identical `customTitle` produce one cluster with three entries in `_constituent_sessions`, chronologically ranked by `started_at`.
- **auto-stitch.AC1.3 Success:** Sessions with no `customTitle` (predates convention) remain singletons after `bulk`.
- **auto-stitch.AC1.4 Success:** Sessions with different `customTitle` strings remain separate (each in its own archive).
- **auto-stitch.AC1.5 Failure:** A session in a different project with the same `customTitle` does NOT cluster with another project's sessions.

### auto-stitch.AC2: Stitched schema matches MELICA hand-rolled format
- **auto-stitch.AC2.1 Success:** Cluster `session.meta.json` has `archive.stitched: true`, `_constituent_sessions: [{id, rank}, ...]` with rank starting at 1, `artifacts.primary_jsonl` matching `<first-constituent-uuid>.jsonl`.
- **auto-stitch.AC2.2 Success:** Cluster `statistics` aggregates: `turns`, `user_messages`, `assistant_messages`, `jsonl_lines`, `raw_transcript_bytes`, `raw_transcript_sha256` reflect the concatenated transcript.
- **auto-stitch.AC2.3 Success:** Cluster `session.id` equals the first constituent's UUID (the "primary"); `session.started_at` is the earliest, `session.ended_at` is the latest, `session.duration_minutes` spans the full range.
- **auto-stitch.AC2.4 Success:** Singleton archives keep the current schema (no `_constituent_sessions`, no `stitched` flag, current `statistics` shape with tokens/cost/tool_calls).

### auto-stitch.AC3: Manifest fan-in
- **auto-stitch.AC3.1 Success:** Every constituent UUID has a manifest entry pointing at the cluster's directory after stitching.
- **auto-stitch.AC3.2 Success:** Looking up any constituent UUID's archive resolves to the cluster directory.

### auto-stitch.AC4: Hook-path promotion
- **auto-stitch.AC4.1 Success:** When `archive()` runs for a session whose `customTitle` matches an already-archived singleton in the same project, the singleton is promoted in place: JSONL is appended, `session.meta.json` rewritten to stitched schema, new UUID added to manifest.
- **auto-stitch.AC4.2 Success:** When `archive()` runs for a session whose `customTitle` matches an already-archived cluster, the cluster is extended (new entry in `_constituent_sessions`, new UUID in manifest).
- **auto-stitch.AC4.3 Failure:** Promotion never deletes constituent data — `raw-transcript.jsonl` content is preserved; only metadata schema changes.
- **auto-stitch.AC4.4 Edge:** Same-UUID re-archive (resumed session, `.last_size` grew) re-renders in place; does NOT add a duplicate `_constituent_sessions` entry.

### auto-stitch.AC5: Manual stitch CLI
- **auto-stitch.AC5.1 Success:** `claude-research-transcript stitch --into <cluster-uuid> <session-uuid>` adds a session (with or without `customTitle`) to an existing cluster: appends JSONL, updates schema, fans manifest.
- **auto-stitch.AC5.2 Success:** `stitch` can target a singleton — promoting it to a cluster as part of the operation.
- **auto-stitch.AC5.3 Failure:** `stitch --into <missing-uuid>` errors with "no archive found for <uuid>".
- **auto-stitch.AC5.4 Failure:** `stitch` of an already-constituent UUID is a no-op (idempotent), reports "already in cluster", exits 0.

### auto-stitch.AC6: Three Ps and status reflect clusters
- **auto-stitch.AC6.1 Success:** `/transcript` invoked on any constituent UUID writes Three Ps to the cluster's meta; all constituent UUIDs share those Three Ps via cluster identity.
- **auto-stitch.AC6.2 Success:** `status` lists each stitched cluster as one row with constituent count shown (e.g. "3 sessions stitched"), not three separate rows.
- **auto-stitch.AC6.3 Success:** `regenerate` on any constituent UUID re-renders the cluster's HTML/MD/PDF from the concatenated transcript.

## Architecture

### Data model

A stitched archive is one directory on disk containing:

- `raw-transcript.jsonl` — concatenation of all constituent JSONLs in chronological rank order.
- `<primary-uuid>.jsonl` — a copy of the concatenated stream named after the primary (first) constituent, mirroring claude-code-transcripts' convention.
- `session.meta.json` — cluster metadata in the stitched schema (see below).
- `index.html` + `page-*.html` — rendered from the concatenated transcript.
- `conversation.md`, `conversation.pdf` — generated only when Three Ps are supplied (existing convention).

The manifest (`.session_manifest.json`) maps every constituent UUID to the cluster directory. The catalog (`CATALOG.json`) lists the cluster once, with constituent UUIDs in a field.

### Stitched schema (matches MELICA hand-rolled cluster verbatim)

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ConstituentEntry:
    id: str        # UUID of the constituent session
    rank: int      # 1-indexed chronological rank by session start

@dataclass(frozen=True)
class StitchedStatistics:
    turns: int
    user_messages: int          # NB: renamed from "human_messages" to match hand-rolled
    assistant_messages: int
    jsonl_lines: int
    raw_transcript_bytes: int

@dataclass(frozen=True)
class StitchedArtifacts:
    raw_transcript: str         # "raw-transcript.jsonl"
    primary_jsonl: str          # "<primary-uuid>.jsonl"
    raw_transcript_sha256: str
```

The full meta JSON keeps the standard top-level shape (`session`, `project`, `model`, `auto_generated`, `three_ps`, `archive`, etc.) with these stitched-specific additions:
- `archive.stitched: true`
- `_constituent_sessions: [ConstituentEntry, ...]`
- `statistics` uses the simplified shape above (no tokens, cost, tool_calls — the hand-rolled MELICA cluster dropped these; this design preserves that choice for verbatim match).
- `three_ps` accepts optional `title` and `tags` fields on top of the standard `prompt_summary`/`process_summary`/`provenance_summary`.

Singleton archives keep the current schema (which includes `statistics.tokens`, `statistics.tool_calls`, `statistics.estimated_cost_usd`). The two schemas coexist; readers discriminate via `archive.stitched`.

### Detection contract

```python
def discover_clusters(sessions: list[tuple[Path, str]]) -> dict[str, list[tuple[Path, str]]]:
    """Group sessions by (project_dir, customTitle).

    Returns a dict keyed by 'project_dir||customTitle' whose values are lists of
    (transcript_path, session_id) tuples for that cluster. Sessions without a
    customTitle field, or whose customTitle is empty, are returned in keys of
    the form 'project_dir||__singleton__:<session_id>' so each becomes its own
    "cluster" of size 1. Caller distinguishes 'should-stitch' from
    'leave-as-singleton' by checking len(value) >= 2.
    """
```

`customTitle` is read by scanning the first ~100 JSONL entries of a session and taking the first non-empty `customTitle` field. Claude Code writes it on every entry from the moment it's set, so reading from the head is sufficient.

### Components

| Component | File | Responsibility |
|---|---|---|
| `extract_custom_title` | `metadata.py` | Read `customTitle` from JSONL (or `None`) |
| `discover_clusters` | `discovery.py` | Group sessions by (project, customTitle) for bulk |
| `stitch_cluster` | `archive.py` | Build a fresh stitched archive from N constituent JSONLs |
| `extend_cluster` | `archive.py` | Add one new session to an existing stitched archive |
| `promote_singleton_to_cluster` | `archive.py` | In-place upgrade of a singleton archive when a chain-mate arrives |
| `bulk` (modified) | `cli.py` | Iterate `discover_clusters`, dispatch to `stitch_cluster`/`archive` per group |
| `archive` (modified) | `archive.py` | Detect `customTitle` match against existing archives; dispatch to `extend_cluster` or `promote_singleton_to_cluster` |
| `stitch` (new) | `cli.py` | Manual override CLI verb |
| `status` (modified) | `cli.py` | Render clusters as one row |
| `regenerate` (modified, small) | `archive.py` | Already path-agnostic; just needs to read concatenated raw |

### Naming

Cluster directory name: `YYYY-MM-DD-<sanitised-customTitle>-stitched` where the date is the earliest constituent's start date and `<sanitised-customTitle>` runs through `sanitize_filename`. The `-stitched` suffix mirrors the MELICA convention and makes clusters visually distinguishable from singletons.

When a singleton is promoted, its directory is renamed from `YYYY-MM-DD-<title>` to `YYYY-MM-DD-<customTitle>-stitched` (rebuilt from `customTitle`, not the prior title). Old manifest entry is updated to the new path. This rename is the load-bearing risky operation — see DR3.

## Decision Record

### DR1: `customTitle` as the stitch signal
**Status:** Accepted
**Confidence:** Medium
**Reevaluation triggers:** Claude Code stops writing `customTitle` on every entry; users start resuming long conversations without setting `customTitle`; false-positive clustering causes data confusion.

**Decision:** We chose `customTitle` equality (within a project) as the auto-stitch detection signal over pattern-matching first-user-message resume prompts.

**Consequences:**
- **Enables:** Deterministic, content-agnostic clustering. The signal is stable across the entire session (every JSONL entry carries it). Same convention any tool can set, including future `/exec-session-naming` evolutions.
- **Prevents:** Auto-stitching of pre-`customTitle` sessions (e.g. MELICA's `dad509ba`). These need manual stitch via DR4's CLI.

**Alternatives considered:**
- **First-message resume-prompt patterns:** Rejected because the pattern list is unbounded — the user named five today (`/exec-session-naming`, `/executing-an-implementation-plan`, `# Execute Implementation Plan`, `@docs/WIP-resume-prompt-*.md`, `Resume:`) and explicitly noted "no pattern is reliable."
- **Time-window heuristic:** Rejected because the user's chain had a 14h sleep gap mid-chain; any time window short enough to reject unrelated work breaks the chain, and any window long enough to keep the chain false-positives on unrelated sessions.
- **Manual-only (no auto):** Rejected because the user explicitly requested auto-stitch.

### DR2: Hook-path promotes singletons in place
**Status:** Accepted
**Confidence:** Medium
**Reevaluation triggers:** Promotion rename causes manifest-pointer drift in practice; users want a "draft cluster" mode where the singleton stays intact until N>=2 is confirmed.

**Decision:** When the Stop hook archives session N and discovers session N-1 (in the same project, with matching `customTitle`) is already an archived singleton, archive() promotes the singleton in place: renames the directory to the cluster name, rewrites the meta to the stitched schema, appends session N's JSONL.

**Consequences:**
- **Enables:** Real-time stitching as the user works; no need to wait for `bulk` to backfill.
- **Prevents:** A "pure" model where directory names never change after creation. Implementations that bookmark archive paths by name (e.g. browser tabs) will see broken links after promotion.

**Alternatives considered:**
- **Singleton stays singleton until bulk runs:** Rejected because the hook fires after every session, so the moment of promotion is the natural one — deferring loses information and adds an explicit "promote" step the user has to remember.
- **Keep singleton, create a separate cluster directory:** Rejected because it produces orphan singletons on disk that confuse audits; the MELICA precedent is one directory per cluster.

### DR3: Rename safety via manifest update before filesystem move
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** Crash mid-promotion observed in practice; rename across filesystem boundaries (worktree → main) fails atomically.

**Decision:** When promoting a singleton, the sequence is: (1) compute new directory name, (2) verify it doesn't collide with another session via existing collision-detection logic (Fix 2), (3) `os.rename` the directory, (4) update manifest with new path for all constituent UUIDs, (5) save manifest, (6) save catalog. If any step fails, abort with a warning; the singleton remains intact at its old name.

**Consequences:**
- **Enables:** Crash-safety — manifest update precedes content rewrite, so a partial promotion leaves the singleton recoverable via the original manifest entry.
- **Prevents:** Promotion across filesystems (rename will fail). For the orphan-branch worktree case, this is fine — both old and new paths are inside `.ai-transcripts/`.

**Alternatives considered:**
- **Copy-then-delete:** Rejected because it doubles disk usage transiently and the rename is sufficient for in-tree moves.
- **Symlink old name to new:** Rejected because git doesn't handle symlinks portably across platforms (Windows users on Adela's machine).

### DR4: Manual `stitch` CLI for edge cases
**Status:** Accepted
**Confidence:** High
**Reevaluation triggers:** Users routinely use `stitch` for non-edge cases — suggesting the auto-detection is too narrow.

**Decision:** A `claude-research-transcript stitch --into <cluster-uuid> <session-uuid> ...` CLI verb force-adds one or more sessions to an existing cluster regardless of `customTitle`. Used to attach pre-`customTitle` sessions (like MELICA's `dad509ba`) to clusters detected from newer sessions.

**Consequences:**
- **Enables:** Recovery of clusters whose first members predate the `customTitle` convention. Idempotent — re-running with already-constituent UUIDs is a no-op.
- **Prevents:** Wholly automated stitching for the legacy case. The user has to know about `stitch` and run it.

**Alternatives considered:**
- **Auto-detect via resume-prompt scanning as a fallback:** Rejected per DR1; pattern list is unbounded.
- **No manual override:** Rejected because MELICA's existing dad509ba case has no automatic path.

### DR5: Stitched schema diverges from singleton statistics shape
**Status:** Accepted
**Confidence:** Low
**Reevaluation triggers:** Researchers actually need token/cost/tool_call aggregates per cluster; tooling that reads `statistics.tokens` breaks on stitched archives.

**Decision:** Stitched `statistics` drops `tokens`, `estimated_cost_usd`, `thinking_blocks`, and `tool_calls.by_type` (which the singleton schema includes). Keeps `turns`, `user_messages` (renamed from `human_messages`), `assistant_messages`, `jsonl_lines`, `raw_transcript_bytes`. This matches the MELICA hand-rolled cluster verbatim.

**Consequences:**
- **Enables:** Verbatim match with the user's hand-rolled artifact; minimal divergence to argue about.
- **Prevents:** Cluster-level cost/token analysis without re-parsing the raw JSONL. Tooling that needs these aggregates must either read constituent metas (singletons before promotion) or re-aggregate from raw.

**Alternatives considered:**
- **Aggregate tokens/cost/tool_calls into stitched statistics:** Rejected (this design) for verbatim match. Could be added in a follow-up.
- **Use the singleton schema unchanged and just add `_constituent_sessions`:** Rejected because the field-rename (`human_messages` → `user_messages`) in the hand-rolled cluster is intentional — the user prefers that vocabulary.

## Existing Patterns

This design follows existing module conventions:
- Discovery logic lives in `discovery.py` (where `discover_sessions`, `resolve_worktrees` already live).
- Metadata extraction in `metadata.py` (`extract_session_stats`, `is_ide_context_message` already there).
- Archive orchestration in `archive.py` (`archive`, `regenerate_outputs` already there).
- CLI verbs in `cli.py` (current 7-verb Typer app).

Cluster-aware logic in `archive.py` follows the same shape as `archive()` and `regenerate_outputs()` — stateless functions taking explicit paths, returning result paths. No new classes or singletons.

`extract_custom_title` follows the same head-of-file scanning pattern `generate_title_from_content` uses.

The promotion sequence (DR3) mirrors the existing `force_retitle` path in `archive()`, which already handles renaming an existing directory atomically.

## Implementation Phases

<!-- START_PHASE_1 -->
### Phase 1: customTitle extraction
**Goal:** Read `customTitle` from a session JSONL.

**Components:**
- `extract_custom_title(content: str) -> str | None` in `metadata.py` — scans the first ~100 JSONL entries for the first non-empty `customTitle` field, returns it or `None`.

**Dependencies:** None.

**Done when:** Tests cover `auto-stitch.AC1.3` (no customTitle returns `None`) and the happy path (returns the stable string). Existing tests pass.
<!-- END_PHASE_1 -->

<!-- START_PHASE_2 -->
### Phase 2: Cluster discovery for bulk
**Goal:** Group unarchived sessions by (project, customTitle).

**Components:**
- `discover_clusters(sessions) -> dict[str, list[tuple[Path, str]]]` in `discovery.py` (contract above).
- `bulk` in `cli.py`: replace the per-session loop with a per-cluster loop. Clusters of size 1 dispatch to the existing `archive()` path. Clusters of size 2+ dispatch to `stitch_cluster()`.

**Dependencies:** Phase 1.

**Done when:** `auto-stitch.AC1.1`, `auto-stitch.AC1.2`, `auto-stitch.AC1.4`, `auto-stitch.AC1.5` pass. `auto-stitch.AC1.3` already passes from Phase 1 (no clustering happens without `customTitle`).
<!-- END_PHASE_2 -->

<!-- START_PHASE_3 -->
### Phase 3: Stitched schema and `stitch_cluster`
**Goal:** Build a fresh stitched archive from N constituent JSONLs.

**Components:**
- `stitch_cluster(session_ids: list[tuple[Path, str]], archive_dir, ...) -> Path` in `archive.py` — concatenates JSONLs in chronological order, computes aggregated stats, writes stitched schema meta, fans manifest entries, runs claude-code-transcripts on concatenated raw, calls `normalise_text_outputs`.
- Stitched meta builder helper in `metadata.py` — parallel to `create_session_metadata` but emits the stitched shape (with `_constituent_sessions`, `archive.stitched: true`, simplified `statistics`).

**Dependencies:** Phase 2.

**Done when:** `auto-stitch.AC2.1`–`auto-stitch.AC2.4`, `auto-stitch.AC3.1`, `auto-stitch.AC3.2` pass. Hand-rolled MELICA cluster meta and an auto-generated cluster meta for the same input differ only in `archive.archived_at` and free-form fields the user supplies (title, tags).
<!-- END_PHASE_3 -->

<!-- START_PHASE_4 -->
### Phase 4: Hook-path promotion
**Goal:** Singleton → cluster promotion when chain-mate arrives.

**Components:**
- `promote_singleton_to_cluster(singleton_dir, new_session_id, new_transcript_path) -> Path` in `archive.py` — implements DR3's rename-and-rewrite sequence.
- `extend_cluster(cluster_dir, new_session_id, new_transcript_path) -> Path` in `archive.py` — adds one constituent to an already-stitched cluster.
- `archive()` modified: after manifest lookup, check for any existing archive in the project with matching `customTitle`. If singleton, promote; if already a cluster, extend.

**Dependencies:** Phase 3.

**Done when:** `auto-stitch.AC4.1`–`auto-stitch.AC4.4` pass. Specifically: an archived singleton + a fresh `archive()` call with matching `customTitle` produces a stitched cluster with both UUIDs in `_constituent_sessions`, manifest fanned, directory renamed.
<!-- END_PHASE_4 -->

<!-- START_PHASE_5 -->
### Phase 5: Manual `stitch` CLI
**Goal:** Force-stitch CLI verb for edge cases.

**Components:**
- `stitch` verb in `cli.py` with `--into <cluster-uuid> <session-uuid> [<session-uuid> ...]` invocation.
- Reuses `extend_cluster` and `promote_singleton_to_cluster` from Phase 4.
- Idempotency: if a passed UUID is already in `_constituent_sessions`, skip and warn (no-op exit 0).

**Dependencies:** Phase 4.

**Done when:** `auto-stitch.AC5.1`–`auto-stitch.AC5.4` pass.
<!-- END_PHASE_5 -->

<!-- START_PHASE_6 -->
### Phase 6: status, regenerate, and /transcript awareness
**Goal:** Surface clusters correctly in the user-facing surface area.

**Components:**
- `status` in `cli.py` — render stitched clusters as one row showing constituent count; suppress duplicate rows from manifest fan-in.
- `regenerate` (already path-agnostic) — verify it works on stitched archives end-to-end; add regression test.
- `/transcript` skill — already operates on archive directories; verify it writes to the cluster's meta when invoked with any constituent UUID. (May only need test coverage; current code may already work.)

**Dependencies:** Phase 4.

**Done when:** `auto-stitch.AC6.1`–`auto-stitch.AC6.3` pass.
<!-- END_PHASE_6 -->

## Additional Considerations

**Backfill on existing archives.** MELICA's existing collided directory `2026-04-13-local-command-caveatcaveat-…` (5 distinct UUIDs, no `customTitle` because they predate the convention) is NOT auto-stitched by this design. Cleanup of pre-existing damage is a separate manual task using the new `stitch` CLI: identify which collided sessions belong together, then `stitch --into <chosen-primary> <other-uuids>`. The bug-fix commits already prevent NEW collisions of this kind.

**Pre-existing damage is out of scope.** A cross-project audit (2026-05-16) found MELICA has 37 on-disk-duplicate cases (one UUID with 2–3 archive directories from successive sweeps), 8 orphan manifest pointers (including 3 Windows paths imported from a collaborator's clone), and 1 intentional manifest fan-in (the hand-rolled stitched arc). PromptGrimoireTool has 3 orphan pointers; temporalQuotes and Lise are clean. None of this is fixed by auto-stitch. Suggested follow-up: extend `clean` to pick the richest-Three-Ps dir as canonical when a UUID has multiple on-disk archives, and prune manifest entries that point at non-existent paths. Today's bug fixes prevent the patterns from recurring.

**Backwards compatibility.** Old singleton archives (current schema) remain readable by `regenerate`, `status`, `update`, `clean`. The catalog and manifest can hold both shapes simultaneously. Code that reads `session.meta.json` must check `archive.stitched` before assuming the stitched-statistics shape.

**Future extensibility (out of scope here):**
- Aggregating tokens/cost/tool_calls into stitched statistics (DR5 follow-up).
- A `transcript unstitch <constituent-uuid>` to detach a session from a cluster.
- Cross-project stitching (when a conversation legitimately moves between projects).

## Glossary

- **Stitching:** Combining multiple Claude Code session JSONLs (each with its own UUID) into a single archive directory representing one logical user conversation.
- **Constituent:** A single Claude Code session JSONL that participates in a stitched cluster. Identified by its UUID (the JSONL filename stem).
- **Cluster:** A stitched archive directory containing N >= 2 constituent JSONLs concatenated into one `raw-transcript.jsonl`.
- **Singleton:** An archive directory for one session. Current default behaviour; remains the format when no stitch-mates exist.
- **`customTitle`:** A per-session string Claude Code writes on every JSONL entry, set by tools like the `/exec-session-naming` skill. Used here as the stitch detection signal.
- **Primary UUID:** The first (earliest-started) constituent's UUID. Used as the cluster's `session.id` and to name the primary JSONL file.
- **Manifest fan-in:** When N constituent UUIDs all map to the same cluster directory in `.session_manifest.json`. This is the intentional version of the bug pattern the Fix 2+3 commit guards against — the discriminator is whether the cluster meta says `stitched: true`.
- **Promotion:** Converting an existing singleton archive into a cluster in place when a new chain-mate arrives via the Stop hook.
- **Three Ps:** Prompt / Process / Provenance — the IDW2025 research metadata framework. In a stitched cluster, Three Ps describe the full conversation arc, not a single context window.
- **`/exec-session-naming`:** A Claude Code skill (in `denubis-plan-and-execute`) that sets the tmux pane title and `customTitle` for the current session. The mechanism that makes `customTitle` clustering reliable across resumed sessions in practice.
