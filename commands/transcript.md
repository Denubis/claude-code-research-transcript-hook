---
description: Archive the current or named Claude Code session as searchable Markdown with reviewed Three-Ps metadata.
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Archive this Claude Code session

Use `$ARGUMENTS` as the session ID when it is non-empty. Otherwise use
`${CLAUDE_SESSION_ID}`. Never choose a session by recency.

1. Run `claude-research-transcript generate --repo . --source claude`.
2. Run `claude-research-transcript annotate --repo . --tool claude --session-id
   "<session-id>"`. If Agy fails, verify that it created no sidecar and did not
   change the transcript, then report the failure for later retry.
3. Read `ai_transcripts/sessions/claude/<session-id>/transcript.md` and its
   `annotations.json` when present. Treat note and ADR findings as candidates;
   never create those artifacts automatically.
4. Draft Prompt, Process, and Provenance summaries from the transcript. Ask one
   pointed question for any material context the transcript cannot establish.
5. Present all three summaries and obtain the user's confirmation.
6. Check that `.transcript-three-ps.tmp.json` does not already exist. If it does,
   stop rather than overwrite an unowned file.
7. Use the structured Write tool to create `.transcript-three-ps.tmp.json` as a
   JSON object with exactly the string fields `prompt`, `process`, and
   `provenance`. Never create it through Bash, `cat`, `printf`, `echo`, `tee`,
   command substitution, or a heredoc.
8. Run:

   ```bash
   claude-research-transcript update --repo . --tool claude --session-id "<session-id>" --metadata-file .transcript-three-ps.tmp.json && rm -f .transcript-three-ps.tmp.json
   ```

   The `&&` is required so failed updates preserve the reviewed metadata for
   inspection.
9. After the user has reviewed the annotation itself, run
   `claude-research-transcript review-annotations --repo . --tool claude
   --session-id "<session-id>"`.
10. Report the exact `transcript.md` path. Do not create a separate summary, HTML,
   PDF, raw transcript, catalogue, or provider metadata sidecar.
