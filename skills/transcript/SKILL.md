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
4. Read `ai_transcripts/sessions/<provider>/<session-id>/transcript.md`.
5. Draft the Three Ps from the transcript:
   - Prompt: what the user needed.
   - Process: how the session approached the work.
   - Provenance: why the session matters in the wider project or research record.
6. Ask only for context the transcript cannot establish, then obtain human
   confirmation of all three summaries.
7. Create a temporary JSON object with exactly `prompt`, `process`, and
   `provenance` through the structured Write/Edit tool. Do not interpolate
   authored metadata into Bash or use a heredoc.
8. Run `claude-research-transcript update --repo <root> --tool <provider>
   --session-id <session-id> --metadata-file <path>`.
9. Remove the temporary metadata file only after a successful update and report
   the transcript path.

## Archive boundary

The only durable output for a parent session is `transcript.md`. It includes the
human/main-agent dialogue, compact visible tool reports, context boundaries,
repository/source evidence, Three-Ps frontmatter, and an omission/redaction
ledger.

Do not create HTML, PDF, raw JSONL copies, summaries, catalogues, or metadata
sidecars. Subagent files are validated and counted but never rendered as parent
dialogue. Generation must fail if a required provider store is missing or
Gitleaks cannot scan the candidate.
