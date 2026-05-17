# Resume: auto-stitch — pick up at Phase 4

**Last session:** 2026-05-17. Phases 2 and 3 shipped (cluster discovery + bulk dispatch + `stitch_cluster` orchestration). 289 tests passing.

## What landed since the last resume prompt

```
c17c836 feat(archive): stitch_cluster for auto-stitch (Phase 3)
b6c63f6 feat(bulk): cluster discovery for auto-stitch (Phase 2)
fba4aa2 docs(wip): resume prompt for auto-stitch Phase 2  ← previous resume
```

Phase 2 added `discover_clusters` in `discovery.py:92` and the per-cluster dispatch in `bulk` (`cli.py:558`). Phase 3 added `create_stitched_metadata` in `metadata.py:421` and `stitch_cluster` in `archive.py:333`, wired `bulk` to call `stitch_cluster` for multi-session clusters, and produces the MELICA-verbatim stitched schema. 24 new tests across `test_metadata.py`, `test_stitch.py`, `test_bulk.py`.

## Where the design lives

**Read this first:** `docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md`

Phases 1, 2, 3 are now done. Phase 4 (hook-path promotion) is next. DR2 and DR3 are the load-bearing decisions for Phase 4 — read them carefully before designing.

## Next action: Phase 4 — `promote_singleton_to_cluster` + `extend_cluster`

**Goal of Phase 4** (from the design doc): When `archive()` runs for a session whose `customTitle` matches an already-archived singleton (or cluster) in the same project, promote/extend in place rather than creating a new singleton.

Two new functions in `archive.py`:

1. **`extend_cluster(cluster_dir, new_session_id, new_transcript_path) -> Path`** — adds one constituent to an already-stitched cluster. Append JSONL, update statistics, append `_constituent_sessions` entry with `rank=N+1`, set `archive.extended_at`, fan manifest. The user's `scripts/stitch_archive_extend.py` at `/media/brian/storage/people/Adela/melica/scripts/stitch_archive_extend.py` is the reference implementation — read it; it's well-commented and shows the exact field-update pattern.

2. **`promote_singleton_to_cluster(singleton_dir, new_session_id, new_transcript_path) -> Path`** — implements DR3's rename-and-rewrite sequence. Computes new directory name `YYYY-MM-DD-<customTitle>-stitched`, collision-checks via `_resolve_collision`, renames the singleton directory, rebuilds the meta in stitched-schema (via `create_stitched_metadata`), appends the new constituent. Both the old singleton's UUID and the new UUID end up in `_constituent_sessions` and in the manifest pointing at the renamed cluster directory.

Then modify `archive()`: after manifest lookup, scan the project's archives for any with matching `customTitle`. If a singleton matches → promote. If a cluster matches → extend. If nothing matches → existing singleton-archive flow.

### TDD entry point

1. **First** add tests in `tests/test_stitch.py` (extend the existing patterns there):
   - `TestExtendCluster`: AC4.2 (cluster extension); AC4.3 (constituent data preserved); AC4.4 (same-UUID re-archive is no-op, does NOT duplicate `_constituent_sessions` entry — this is the idempotency contract `stitch_archive_extend.py` enforces).
   - `TestPromoteSingletonToCluster`: AC4.1 (singleton → cluster promotion: JSONL appended, schema rewritten, new UUID added to manifest, directory renamed); AC4.3 (raw content preserved).
   - `TestArchivePromotionPath`: integration — `archive()` called twice in sequence for two sessions sharing `customTitle` produces one cluster.

2. **Then** implement `extend_cluster` (simpler — operates on a known cluster dir).

3. **Then** implement `promote_singleton_to_cluster` (the load-bearing risky one — DR3's rename sequence).

4. **Then** wire `archive()` to detect customTitle matches and dispatch.

After Phase 4: tests green, commit as `feat(archive): hook-path promotion for auto-stitch (Phase 4)`.

## DR3's rename sequence — read this twice before coding

DR3 says: rename the directory, THEN update the manifest, THEN save catalog. If anything fails, the singleton remains intact at its old name. The intent is crash-safety — manifest update precedes the content-rewrite (meta schema change), so a partial promotion leaves the singleton recoverable via the original manifest entry.

Recommended sequence:
1. Compute new dir name (`YYYY-MM-DD-<customTitle>-stitched`).
2. Collision-check via `_resolve_collision` against the new name.
3. Build new stitched meta in memory (don't write yet).
4. `os.rename(singleton_dir, new_cluster_dir)`.
5. Append new JSONL content to `raw-transcript.jsonl` AND to `<primary-uuid>.jsonl` (mirror).
6. Write new stitched meta to disk (overwrites old singleton meta).
7. Update manifest: add new UUID, repoint old singleton UUID, both → new cluster path. Save.
8. Update catalog.

If step 4 (rename) fails: nothing changed on disk; abort with warning.
If step 5 fails: directory moved but JSONLs not appended — log warning, leave state for user to recover (rare; only happens on disk-full).
If step 6 fails: meta is gone or partial — same recovery situation.
If step 7-8 fails: manifest/catalog stale — user sees broken pointers; `clean` can rebuild from sidecars (`rebuild_indexes` in `catalog.py`).

**Wrap the promotion in try/except** and log warnings at every failure point. Do NOT raise — `archive()` is called from hooks and must remain best-effort.

## Load-bearing decisions to keep in mind (DR1–DR5)

Same table as the previous resume — DR2 (hook-path promotion in place) and DR3 (rename-before-rewrite) are the relevant ones for Phase 4. DR5 (statistics schema) is settled and you'll be consuming `create_stitched_metadata` not redesigning it.

| DR | Decision | Phase 4 implication |
|---|---|---|
| DR1 | `customTitle` equality | The match signal for promotion/extension lookup. `extract_custom_title` on the incoming session, compare against archived sessions' meta `auto_generated.title` (singleton) or constituents' source customTitle (cluster). |
| DR2 | Hook-path promotes in place | Phase 4 IS this. Don't defer promotion to a separate explicit step. |
| DR3 | Rename-before-rewrite | See above. |
| DR4 | Manual `stitch` CLI for legacy | Phase 5 territory; can share `extend_cluster` infrastructure. |
| DR5 | Stitched stats schema | Reuse `create_stitched_metadata` verbatim. |

## Pitfalls observed last session (2026-05-17)

1. **The schema oracle is the MELICA hand-rolled meta.** Re-read it before writing Phase 4 tests — `extend_cluster` must produce a meta indistinguishable from the hand-rolled one after N extensions (modulo `archive.archived_at`, `archive.extended_at`, and free-form fields). Path: `/media/brian/storage/people/Adela/melica/ai_transcripts/2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/session.meta.json`. The hand-rolled cluster has `archive.extended_at` because it was extended manually — `extend_cluster` should set this on every call.

2. **`stitch_archive_extend.py` is the de facto contract for extension.** Path: `/media/brian/storage/people/Adela/melica/scripts/stitch_archive_extend.py`. Read it; the field-update pattern in `_update_meta()` and `_update_manifest()` is exactly what `extend_cluster` should do. The idempotency check at line 189-192 (`if any(c["id"] == uuid for c in constituent): no-op`) is AC4.4's contract.

3. **`extract_session_stats` returns `human_messages`, stitched schema uses `user_messages`.** The rename happens at `create_stitched_metadata`'s boundary, not in `extract_session_stats`. When extending a cluster, you'll be adding *one* session's stats — re-extract via `extract_session_stats` then aggregate into the existing cluster's `user_messages`/`assistant_messages` counts in the meta.

4. **The user's archive() function is already complex.** Modifying it to add the customTitle-match dispatch will make it more complex. Consider extracting the dispatch logic into a helper `_find_matching_cluster_or_singleton(archive_dir, project_dir, custom_title) -> Path | None` that `archive()` consults early.

5. **Test fixture pattern from Phase 3 worked well.** `_write_session_jsonl(path, sid, customTitle, started_at, ended_at, user_messages, assistant_messages)` and the `_Cluster` helper in `tests/test_stitch.py` are clean — extend them for Phase 4 rather than building new fixtures.

6. **Trivial classification for clusters:** Phase 3 made a judgment call — a cluster is trivial only if EVERY constituent is individually trivial. I flagged this for review. The user has not objected; if you want to revisit it, do so explicitly rather than silently changing behaviour.

7. **The user wants progress, not process.** Same as last session. Commit to sensible defaults; surface only load-bearing ambiguities. Don't run AskUserQuestion unless the answer would actually change the design.

## Verify the resume

```bash
git pull
uv run pytest --no-cov  # should report 289 passed
cat docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md
cat /media/brian/storage/people/Adela/melica/scripts/stitch_archive_extend.py
```

Then start Phase 4 TDD per "Next action" above.

## Decisions owed (don't get stuck — make a sensible call and flag it)

1. **Promotion atomicity granularity.** DR3 says abort on failure. Should `archive()` return None on partial promotion, or return the partially-promoted directory? My instinct: return the directory if the rename succeeded but later steps failed (so the user sees the new path) plus a stderr warning naming what failed. Flag for review.

2. **`extend_cluster` idempotency message.** When the new UUID is already a constituent, `stitch_archive_extend.py` prints "UUID X already in _constituent_sessions — no-op." and exits 0. Phase 4 should match. Also need to verify the file size hasn't grown (resumed session) — if it has, append the new content and update statistics, but don't add a duplicate constituent entry. This is AC4.4's edge case.

3. **Multi-cluster collision.** What if two different singletons in the same project have customTitle matching the incoming session? (Shouldn't happen if auto-stitch worked from session 1, but could exist in legacy archives.) Suggested call: promote the most recently archived one; log warning naming the other(s) so the user can manually stitch via the Phase 5 CLI.

4. **DR5 revisit.** Still hanging from last session. No new pressure; defer.

## Phase status snapshot

- [x] Phase 1: `extract_custom_title` (e62ee79)
- [x] Phase 2: `discover_clusters` + bulk dispatch (b6c63f6)
- [x] Phase 3: `stitch_cluster` + stitched schema builder (c17c836)
- [ ] **Phase 4: hook-path promotion ← NEXT**
- [ ] Phase 5: manual `stitch` CLI
- [ ] Phase 6: status/regenerate/`/transcript` cluster-awareness; CLAUDE.md update
