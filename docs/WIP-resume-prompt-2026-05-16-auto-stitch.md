# Resume: auto-stitch — pick up at Phase 2

**Last session:** 2026-05-16. Bug fixes shipped, design plan committed, Phase 1 (extract_custom_title) complete.

## What landed (origin/main, 6 commits ahead of where we started)

```
373a7e3 fix(title): skip command boilerplate envelopes in title generation
318c168 fix(bulk): honour target=="here" defaults — mirror status behaviour
4b32889 fix(archive): silent-clobber protection — collision detect + curated-Three-Ps preservation
ac17fb4 fix(update,regenerate): normalise session.meta.json on write and verify clean output end-to-end
707a564 docs(design): auto-stitch via customTitle clustering
e62ee79 feat(metadata): add extract_custom_title for auto-stitch detection (Phase 1)
```

256 tests passing.

## Where the design lives

**Read this first:** `docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md`

It has the full Definition of Done, 6 AC groups (`auto-stitch.AC1` through `auto-stitch.AC6`), 5 Decision Records (DR1–DR5), and 6 implementation phases with `<!-- START_PHASE_N -->` markers. Phase 1 is done; Phase 2 is next.

## Next action: Phase 2 — `discover_clusters` + bulk dispatch

**Goal of Phase 2** (from the design doc): group unarchived sessions by (project, customTitle); `bulk` dispatches singletons to existing `archive()` and clusters of size ≥2 to a new `stitch_cluster()` (which Phase 3 will write — Phase 2 can stub it).

**TDD entry point:**

1. Add `TestDiscoverClusters` in `tests/test_discovery.py`. Failing tests for:
   - `auto-stitch.AC1.1`: two same-`customTitle` sessions in one project → one cluster of size 2
   - `auto-stitch.AC1.3`: sessions with no `customTitle` → singletons (separate keys)
   - `auto-stitch.AC1.4`: sessions with different `customTitle` → separate clusters
   - `auto-stitch.AC1.5`: same `customTitle` in different projects → separate clusters
2. Implement `discover_clusters(sessions: list[tuple[Path, str]]) -> dict[str, list[tuple[Path, str]]]` in `discovery.py`. Contract is in the design doc's Architecture section.
3. Modify `bulk` in `cli.py`: replace the per-session loop with a per-cluster loop. For clusters of size 1, call existing `archive()`. For clusters of size ≥2, raise `NotImplementedError("stitch_cluster (Phase 3) not yet implemented")` — Phase 3 wires this up.
4. Add `TestBulkClustersDispatch` in `tests/test_bulk.py` verifying the dispatch (singletons still archive normally; multi-session groups error with the Phase 3 stub message).

After Phase 2: tests green, run full suite, commit as `feat(bulk): cluster discovery for auto-stitch (Phase 2)`.

## Load-bearing decisions to keep in mind (DR1–DR5)

| DR | Decision | Why it matters in Phase 2+ |
|---|---|---|
| DR1 | `customTitle` equality is the signal | Don't fall back to pattern-matching first-message resume prompts — user explicitly rejected this. |
| DR2 | Hook-path promotes singletons in place | Phase 4 territory; Phase 2 doesn't promote, just groups for bulk. |
| DR3 | Rename via manifest-update-before-rename | Phase 4 territory; flag if Phase 2 needs to rename. |
| DR4 | Manual `stitch` CLI for legacy edge cases | Phase 5 territory; out of scope for Phase 2. |
| DR5 | Stitched stats schema drops tokens/cost/tool_calls (matches MELICA hand-rolled verbatim) | Phase 3 territory. Confidence Low — user may revisit. |

## Pitfalls observed this session

1. **The user uses "uuid" loosely.** They sometimes mean session_id, sometimes the logical conversation, sometimes the archive directory's identity. When in doubt, ground in actual filesystem state (read JSONLs, list manifests) rather than asking another clarifying question. Today I spent ~6 turns asking what "same uuid" meant before just running the audit.

2. **The user wants progress, not process.** They rejected multiple AskUserQuestion invocations and said "fucking whatever. Go for it." Commit to a sensible decision when one exists and proceed. Surface only the load-bearing ambiguities.

3. **MELICA reference data is invaluable.** The hand-rolled cluster at `/media/brian/storage/people/Adela/melica/ai_transcripts/2026-04-24-dvc-sciencedata-archive-phase5-to-pr49-stitched/` is the schema spec. The user's `scripts/stitch_archive_extend.py` shows how they extend a cluster manually. Read both before designing.

4. **The user's installed CLI is v0.5.0 (pre-normalise).** They need `uv tool install /home/brian/people/Brian/claude-code-research-transcript-hook --force` to get today's bug fixes active in MELICA workflows. The trailing-whitespace ping-pong stops once they upgrade.

5. **Cross-project audit at 2026-05-16:** MELICA has 38 collision events on disk (37 on-disk duplicates + 1 intentional manifest fan-in for the hand-rolled cluster) plus 8 orphan pointers (3 Windows paths from a collaborator's clone). PromptGrimoireTool has 3 orphan pointers, otherwise clean. temporalQuotes and Lise are clean. Pre-existing damage is out of scope for the auto-stitch design; cleanup is the manual `stitch` CLI's job (Phase 5) and/or a `clean`-extension follow-up.

6. **Three Ps loss in MELICA is being remade manually** — the user told me they'll re-curate the 3 confirmed cases (66368cd6, 25bb361f, 986491f2) rather than asking me to salvage them. Don't add salvage logic.

## Verify the resume

To pick up cleanly, the next session should:

```bash
git pull
uv run pytest --no-cov  # should report 256 passed
cat docs/design-plans/2026-05-16-auto-stitch-customtitle-clustering.md  # the entry point
```

Then start Phase 2 TDD per the "Next action" section above.

## Decisions still owed by the user (don't get stuck — make a sensible call and flag it)

- **Phase 6 status output format.** Should clusters show as `"3 sessions stitched: <title>"` or differently? Pick a format, document, flag for review at end of Phase 6.
- **Phase 5 idempotency edge cases.** `stitch --into <cluster> <already-constituent-uuid>` — exit code 0 with warning, per AC5.4. But what about `stitch --into <singleton> <uuid-currently-pointed-at-a-different-archive>`? Specify on encountering it.
- **DR5 revisit.** Aggregating tokens/cost/tool_calls into stitched statistics would close a real gap (cluster-level cost analysis). Hold this as a follow-up unless the user surfaces it before Phase 3 wraps.
