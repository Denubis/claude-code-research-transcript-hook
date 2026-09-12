# Windowed transcript annotations

**Status:** Draft

## Purpose

Replace the current one-prompt annotation pass with the smallest staged pipeline that
keeps late-session material visible. The 2026-08-19 UAT produced a valid, digest-bound
sidecar, but its eight findings described only the early plugin migration work and omitted
material later work on the token estimator, code-quality guard, provider refresh,
heredoc-writing failures, bytecode caches, and the transcript archive itself.

The redesign must improve whole-session coverage without giving Agy raw provider logs,
mounting the repository or archive into its workspace, publishing model scratch files, or
weakening the existing all-or-nothing publication boundary.

## Authority sources

| Decision or instruction | Exact source | Resolver | Resolution condition |
|---|---|---|---|
| Treat the single-pass result as failed UAT and redesign annotation as windowed extraction followed by synthesis. | Codex session `01a007ae-7f19-7dd1-aef9-603cc1e29513`, rollout line 9916, human turn `01a0170f-1394-72d1-bfbe-9f4154e11751` | `sed -n '9909,9916p' /home/brian/.codex/sessions/2026/08/16/rollout-2026-08-16T09-07-51-01a007ae-7f19-7dd1-aef9-603cc1e29513.jsonl` | The assistant asks whether to treat the UAT as failed and use windowed extraction plus synthesis; the next `role=user` message is exactly `yes`. |
| Preserve the existing annotation security, sidecar, validation, review, and failure contracts. | `.notes/project_annotation-sidecar-wip.md` | `sed -n '1,91p' .notes/project_annotation-sidecar-wip.md` | The project note identifies the rendered transcript as Agy's only source, requires isolated stdin execution and local locator validation, and leaves files unchanged on any annotation failure. |

## Universe of discourse

The design covers the `annotate` command, the annotation functional core in
`src/claude_transcript_archive/annotations.py`, its process orchestration in
`src/claude_transcript_archive/cli.py`, the permanent `annotations.json` sidecar, and the
compact block rendered into `transcript.md`.

The input is the annotation-free, already-rendered and Gitleaks-scanned Markdown
transcript. Agy remains an external process invoked sequentially through stdin from an
isolated temporary directory. Downstream consumers are the Markdown reader, the sidecar
reader, transcript regeneration, Three-Ps updates, and `review-annotations`.

Raw Claude or Codex JSONL, subagent dialogue, private reasoning, raw tool output,
automatic `.notes` or ADR creation, automatic review, parallel model calls, and persisted
intermediate annotations remain outside this design.

## Current state

`_request_annotation` currently strips any rendered annotation, creates one dynamic
locator-constrained schema, and sends the complete transcript to one Agy invocation.
`parse_agy_envelope` validates the returned structured output against the full rendered
locator set. `_run_annotate` scans the completed Markdown and JSON candidates and publishes
both atomically. Existing tests cover the stdin-only isolated invocation, invented
locators, quota failure, stale sidecars, review transition, and Three-Ps rebinding.

The failed UAT input had an annotation-free SHA-256 of
`518dedd48e966267aa2d5bf268ad7a1c3a9a313477f34e4185f9a3cec5491f1c`. Its rendered
dialogue contained 448 assistant records and 101 user records totalling about 297,000
characters; 387 generic tool-activity records added about 73,000 characters. The
single-pass sidecar was structurally valid but its findings stopped before material that
is visibly present in the later half of the transcript. This proves schema validity and
locator validation do not establish whole-session semantic coverage.

## Goals and non-goals

### Goals

- Present every complete user and assistant record to exactly one extraction call.
- Use one call for ordinary transcripts and the fewest bounded windows needed for long
  transcripts, because Agy has substantial fixed per-call context cost.
- Make synthesis account for every extracted finding so it cannot silently discard a
  later window.
- Preserve strict locator provenance from window extraction through final synthesis.
- Keep all intermediate results temporary and publish only one final sidecar and one
  rendered annotation block.
- Preserve failure atomicity, digest binding, human review, stale-sidecar behavior, and
  compatibility with existing version-1 sidecars.
- Record enough per-call provenance and observed usage to audit the cost of a staged run.

### Non-goals

- Guarantee that a model identifies every semantically interesting fact. Window ownership
  is deterministic; semantic completeness remains a human UAT judgment.
- Infer over generic `Tool activity` blocks. The current renderer deliberately reduces
  them to reports such as `Ran code`; user and assistant records own the searchable
  semantic account.
- Add a user-tunable token budget or tokenizer dependency.
- Resume a partially completed inference run after failure.
- Change transcript generation, provider discovery, Three-Ps semantics, or review policy.

## Design

### Annotation view and record ownership

The functional core derives an annotation view from the annotation-free Markdown. It
scans Markdown fences and recognizes only unfenced top-level `## User`, `## Assistant`,
and `## Tool activity` record headings. Session/frontmatter context is retained as a small
common prelude. Complete user and assistant records are eligible inference units; tool
activity records are omitted from model input.

Each eligible record belongs to exactly one window, in source order, with no overlap.
Headings quoted inside fenced dialogue are content, not boundaries. A record larger than
the target remains intact in its own oversized window rather than being silently cut or
discarded.

### Minimal deterministic windowing

The default target is 150,000 Unicode characters of eligible records per window. Records
are greedily packed without crossing the target when the current window is non-empty. A
transcript at or below the target uses one extraction call and no synthesis call. A long
transcript uses the fewest windows produced by that rule, followed by one synthesis call.

Character count is a dependency-free deterministic proxy, not a token guarantee. The
target is invalidated if real UAT again shows material within-window omission, or if Agy
rejects an individual window for context size. The smallest correction is to lower the
target and rerun the same acceptance surface; no schema or public CLI change should be
needed.

### Window extraction

Each extraction call receives the common session prelude and one ordered record window.
It runs with the existing model, sandbox, disabled slash commands, strict JSON output,
stdin-only input, timeout, and isolated working-directory constraints. Its dynamic schema
allows only source locators present in that window.

The extraction shape contains partial title/keyword/Prompt/Process/Provenance material,
decisions, affected artifacts, and structured findings. After local validation, the core
assigns each finding a deterministic candidate identifier such as `w02-f003`. These
identifiers are local pipeline metadata, not model-generated evidence.

### Coverage-preserving synthesis

For more than one window, one final Agy call receives only the ordered validated window
results and their deterministic candidate identifiers. It never receives raw provider
data or filesystem access. The synthesis schema returns the final annotation fields and
requires each final finding to list one or more candidate identifiers.

Local validation requires every extracted candidate identifier to appear exactly once
across the final findings. Multiple candidates may be merged into one finding, including
a failure in one window and its repair in a later window. A final finding may cite only
locators cited by the candidates it represents. Missing, repeated, unknown, or
cross-candidate locators reject the synthesis. Candidate identifiers are removed from the
human-facing finding after this validation.

This contract prevents synthesis from silently dropping an extracted late-session
finding. It does not prove that the per-window extractor recognized every interesting
event; the real transcript UAT owns that judgment.

### Sidecar version and provenance

New annotations use sidecar schema version 2. The final annotation and review fields keep
their current meaning. Generator provenance records:

- Agy version and requested model;
- the deterministic window target and actual window count;
- one ordered call record per extraction and synthesis stage;
- each call's stage, window index when applicable, conversation identifier when present,
  and exact integer token-usage counters supplied by Agy when present; and
- extracted and represented candidate counts.

Version-1 sidecars remain readable, renderable, reviewable, and stale-preserved. A
successful new `annotate` run replaces a current version-1 sidecar with version 2; a
failed run leaves the old sidecar and transcript byte-for-byte unchanged.

### Publication and state transitions

All extraction and synthesis calls complete and validate before either permanent file is
prepared. The final sidecar remains bound to the SHA-256 of the full annotation-free
transcript, not the reduced annotation view. Gitleaks scans the final Markdown and JSON
candidates. Existing atomic replacement publishes both artifacts together.

The public command and model option remain unchanged. A successful staged run ends in
`pending` review. Only the existing explicit human-review command may transition it to
`reviewed`.

## Failure and recovery

Any process failure, quota exhaustion, timeout, malformed envelope, schema error,
invented locator, missing or duplicate candidate coverage, invalid cross-candidate
locator, or Gitleaks finding aborts the entire run. Temporary schemas and intermediate
results disappear with their isolated directories. The command publishes no partial
window record and does not alter the current transcript or sidecar.

Recovery is an explicit rerun of `annotate`. No availability probe is part of this
workflow; the real annotation call is the only valid quota test. A failed rerun against an
existing pending or reviewed sidecar preserves that sidecar exactly.

## Decisions

### Use bounded extraction plus one synthesis, not a stronger single prompt

The single-prompt design already produced a valid but materially incomplete result.
Adding stronger prose cannot prove that late content was considered and risks becoming a
phrase-level change detector. Window ownership plus candidate accounting provides
observable coverage boundaries.

### Exclude generic tool-activity records from inference

On the failed UAT corpus, generic tool records contributed about one fifth of the input
while carrying only reduced reports such as `Ran code`. Excluding them lets the current
long transcript fit into two extraction windows instead of three, avoiding another fixed
Agy context charge. If the renderer later gives these records substantive semantic
content, that architecture change invalidates this exclusion and requires a new UAT.

### Run sequentially and publish only the final result

Parallel calls could shorten elapsed time but create quota bursts and nondeterministic
failure timing. Persisted window artifacts would complicate the one-sidecar consumer
contract. Sequential temporary calls retain simple recovery and one review surface.

### Require exact candidate accounting

A final free-form synthesis could repeat the original failure by dropping an entire
window. Candidate identifiers and exact-once local validation make every extracted
finding visible to synthesis while still permitting cross-window merging.

### Use a deterministic character target

The package intentionally has no runtime dependencies, and provider tokenization is an
external moving contract. A character target is stable and testable. It trades exact
token prediction for dependency-free behavior; real UAT, recorded call usage, and the
explicit invalidation trigger constrain that risk.

## Acceptance criteria

- **WA-1 — Record ownership:** A positive behavioral test proves every unfenced user and
  assistant record appears in exactly one ordered window, generic tool records appear in
  none, fenced heading text does not split a record, and an oversized record is preserved
  intact.
- **WA-2 — Minimal calls:** A transcript within the target causes one extraction and no
  synthesis. A synthetic long transcript causes the expected minimal extraction windows
  and exactly one synthesis, with no probe call.
- **WA-3 — Late-window preservation:** A fake runner returns distinct candidates from
  early and late windows; the published final annotation represents both. Synthesis that
  omits, repeats, invents, or misattributes a candidate fails for the specific coverage
  reason.
- **WA-4 — Security boundary:** Every Agy call receives only stdin, runs from its own
  isolated temporary directory with the existing sandbox and disabled-command flags, and
  receives neither repository/archive paths nor raw provider JSONL.
- **WA-5 — Atomic failure:** Focused tests fail extraction in each window position, fail
  synthesis, fail coverage validation, and fail final scanning; each proves the existing
  transcript and absent or existing sidecar remain byte-for-byte unchanged.
- **WA-6 — Provenance and compatibility:** A version-2 sidecar records ordered stages,
  window count, candidate counts, conversation identifiers, and available exact usage.
  Version-1 fixtures still load, render, preserve stale behavior, and transition through
  explicit review.
- **WA-7 — Existing consumers:** Generation, Three-Ps update, review, stale-sidecar
  preservation, Markdown rendering, and sidecar fingerprinting pass their current
  behavioral tests with version 2.
- **WA-8 — Mechanical gates:** The focused and full project suites, coverage threshold,
  Ruff format/check, Ty, CLI help, and `git diff --check` pass after the final change.
- **WA-9 — Human UAT:** One real run against the failed UAT transcript is reviewed in
  `transcript.md` and `annotations.json`. The human judges whether both early and late
  material, especially feedback, dropped work, miscommunication, and frustration, are
  usefully represented. Exact per-call usage is inspected for proportionality before the
  annotation is marked reviewed.

## Implementation phases

### 1. Deterministic annotation view and extraction windows

Add the pure Markdown record parser, tool-record exclusion, window ownership, extraction
schema, and local candidate assignment under red-green tests. This phase leaves a usable
one-window annotation path with unchanged publication behavior and owns WA-1, the
one-window portion of WA-2, and the input portion of WA-4.

### 2. Coverage-preserving synthesis and atomic orchestration

Add multi-window sequential execution, dynamic synthesis schema, exact candidate and
locator validation, version-2 call provenance, usage capture, and all-stage failure
handling. This phase leaves long transcripts capable of producing one validated pending
sidecar and owns WA-2 through WA-6.

### 3. Consumer compatibility, documentation, and real acceptance

Bring regeneration, Three-Ps update, review, current documentation, and version-1
compatibility to the new sidecar contract. Run the complete mechanical gates, then repeat
the real failed-transcript annotation once and stop for human review. This phase owns WA-7
through WA-9.

## Verification and human judgment

Automation can prove deterministic record ownership, exact call counts, candidate
accounting, locator provenance, schema compatibility, atomic failure, and consumer
regression safety. It cannot prove that the model recognized every meaningful event or
that its compression is useful rather than ornate.

Human UAT therefore reviews the rendered annotation and permanent sidecar against both
halves of the known long transcript. Approval requires useful late-session coverage and a
proportional call/usage record. Only after that judgment may `review-annotations` mark the
sidecar reviewed or implementation history be normalized.
