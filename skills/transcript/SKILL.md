---
name: transcript
description: Use when archiving Claude Code or Codex parent sessions as searchable Markdown with reviewed Prompt, Process, and Provenance metadata.
---

# Archive an AI coding transcript

Generate one deterministic Markdown file for each parent session belonging to the
current Git repository. Keep provider source logs read-only.

## Workflow

1. Resolve the repository root and the provider to archive.
2. Run `claude-research-transcript generate --repo <root> --source <claude|codex|all>`.
3. Use the exact parent session ID supplied by the caller or current harness. If it
   is unavailable, ask for it rather than choosing a session by recency.
4. Run `claude-research-transcript annotate --repo <root> --tool <provider>
   --session-id <session-id>`. Agy failure creates no sidecar and must not alter
   the transcript; report the bounded failure so annotation can be retried later.
5. Read `ai_transcripts/sessions/<provider>/<session-id>/transcript.md` and, when
   present, its `annotations.json`. Treat annotations as review candidates, not
   authority and not permission to create `.notes` or ADR files.
6. Draft the Three Ps from the transcript:
   - Prompt: what the user needed.
   - Process: how the session approached the work.
   - Provenance: why the session matters in the wider project or research record.
7. Ask only for context the transcript cannot establish, then obtain human
   confirmation of all three summaries.
8. Create a temporary JSON object with exactly `prompt`, `process`, and
   `provenance` through the structured Write/Edit tool. Do not interpolate
   authored metadata into Bash or use a heredoc.
9. Run `claude-research-transcript update --repo <root> --tool <provider>
   --session-id <session-id> --metadata-file <path>`.
10. After the human has reviewed the annotation itself, run
    `claude-research-transcript review-annotations --repo <root> --tool <provider>
    --session-id <session-id>`.
11. Remove the temporary metadata file only after a successful update and report
   the transcript path.

## Archive boundary

The durable output for every parent session is `transcript.md`. A successful
explicit annotation stage additionally owns `annotations.json`; it is permanent,
digest-bound, and carries an explicit review status. Current annotations render
near the top of the Markdown, while stale sidecars remain unrendered for repair.

Do not create HTML, PDF, raw JSONL copies, catalogues, or provider metadata
sidecars. Subagent files are validated and counted but never rendered as parent
dialogue. Generation must fail if a required provider store is missing or
Gitleaks cannot scan the candidate. Annotation must fail without publication if
Agy output or cited evidence is invalid.
