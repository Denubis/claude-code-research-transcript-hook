# 2026-11-08: Messages typed mid-turn are archived as human dialogue

## Status

**Accepted** 2026-11-08 by Brian. Written by the implementing agent (Claude Opus 5.5),
not in Brian's voice.

## Context

Claude Code stores a message the human types while Claude is still working as an
`attachment` record of kind `queued_command`, not as a `user` record. The archiver
treats every `attachment` record as operational and omits it, so these messages never
reach a transcript. Some carry rulings, for example the request that became
[the hand-kept ID allowlist](2026-11-08-bb-hand-kept-id-allowlist.md).

## Decision

### Statement (Brian), 2026-11-08

Responding to the report that mid-turn messages are dropped from transcripts:

> "what? why? No, that needs to be fixed, tomorrow, we need to save state and compact"

Session `52a1e4a3-2392-4ecb-824d-876b5f57b5dc`, message
`2cd3dd11-137d-4236-b69e-8da983c67557`, 2026-11-08T10:00:05Z. Resolver:

```
jq -r 'select(.type == "user" and .uuid == "2cd3dd11-137d-4236-b69e-8da983c67557") | .message.content' /home/brian/.claude/projects/-home-brian-people-Brian-claude-code-research-transcript-hook/52a1e4a3-2392-4ecb-824d-876b5f57b5dc.jsonl
```

### What this settles

1. Messages the human types mid-turn appear in the transcript as human turns.
2. The fix is scheduled for the next working session, not this one.

Which queued records count as the human's (task notifications, messages from other
sessions, and records without an origin also arrive this way) is not settled here; see
[Only the human's queued messages are archived](2026-11-09-bb-human-queued-messages-only.md).

## When to revisit

Brian's word.
