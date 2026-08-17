---
description: Archive the current or named Claude Code session as searchable Markdown with reviewed Three-Ps metadata.
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Archive this Claude Code session

Use `$ARGUMENTS` as the session ID when it is non-empty. Otherwise use
`${CLAUDE_SESSION_ID}`. Never choose a session by recency.

1. Run `claude-research-transcript generate --repo . --source claude`.
2. Read `ai_transcripts/sessions/claude/<session-id>/transcript.md`.
3. Draft Prompt, Process, and Provenance summaries from the transcript. Ask one
   pointed question for any material context the transcript cannot establish.
4. Present all three summaries and obtain the user's confirmation.
5. Check that `.transcript-three-ps.tmp.json` does not already exist. If it does,
   stop rather than overwrite an unowned file.
6. Use the structured Write tool to create `.transcript-three-ps.tmp.json` as a
   JSON object with exactly the string fields `prompt`, `process`, and
   `provenance`. Never create it through Bash, `cat`, `printf`, `echo`, `tee`,
   command substitution, or a heredoc.
7. Run:

   ```bash
   claude-research-transcript update --repo . --tool claude --session-id "<session-id>" --metadata-file .transcript-three-ps.tmp.json && rm -f .transcript-three-ps.tmp.json
   ```

   The `&&` is required so failed updates preserve the reviewed metadata for
   inspection.
8. Report the exact `transcript.md` path. Do not create a separate summary, HTML,
   PDF, raw transcript, catalogue, or metadata sidecar.
