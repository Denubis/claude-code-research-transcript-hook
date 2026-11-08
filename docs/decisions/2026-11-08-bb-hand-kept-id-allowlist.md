# 2026-11-08: A hand-kept allowlist of IDs that are not keys

## Status

**Accepted** 2026-11-08 by Brian. Written by the implementing agent (Claude Opus 5.5),
not in Brian's voice. The interpretation under "What this settles" is the agent's; Brian
may correct it.

## Context

The automatic ID rule recognises only IDs the archiver holds during a run: the archived
session's own record IDs, its sub-agent records' IDs, and the session IDs of the other
sessions in the run. A message ID quoted from another session, which melica's decision
records do often, can still be flagged and block publication. A side agent offered a
lookup across all chat logs as the alternative.

## Decision

### Statement (Brian), 2026-11-08

Replying to that note:

> "blargh, can we just have an allowlist?"

Session `52a1e4a3-2392-4ecb-824d-876b5f57b5dc`, queued-message record
`14c3dbe5-fb2f-4d54-9436-0abcaf3b4be4`, 2026-11-08T09:34:37Z. Resolver:

```
jq -r 'select(.uuid == "14c3dbe5-fb2f-4d54-9436-0abcaf3b4be4") | .attachment.prompt' /home/brian/.claude/projects/-home-brian-people-Brian-claude-code-research-transcript-hook/52a1e4a3-2392-4ecb-824d-876b5f57b5dc.jsonl
```

### What this settles

1. The redaction overlay (`redactions.toml`) gains `[[allowed_ids]]` entries, each an
   exact UUID with a one-line reason. Listed IDs are hidden from the scanner in every
   session, during generation and annotation.
2. The list adds to the automatic rule; it does not replace it. No lookup across all chat
   logs is built.
3. Entries must be UUIDs, so the list cannot wave through a key of another shape.

## When to revisit

If a listed ID turns out to be a real credential, or Brian's word.
