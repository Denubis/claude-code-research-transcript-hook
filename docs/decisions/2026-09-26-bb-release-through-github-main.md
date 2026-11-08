# 2026-09-26: Releases reach Claude Code through GitHub main

## Status

**Accepted** 2026-09-26 by Brian. Recorded on 2026-11-09 by Claude Opus 5.5 from the
open-questions tickets a Codex agent raised; not in Brian's voice.

## Context

The plugin update reported version drift. Asked for the exact error, Brian pasted:

> "
> === transcript-archive-marketplace ===
>   transcript-archive-marketplace/Claude plugins: FAILED: Claude post-install version drift: transcript-archive: installed '0.7.2', expected '1.0.0'
>   Codex plugins: ready
>   Antigravity plugins: ready"

Claude Code's marketplace installs from GitHub `main`, which was still at `cda2ed1`
(0.7.2). The tested 1.0.0 work sat four commits ahead on `provider-neutral-archive-uat`.
The Codex agent asked: "Should Claude use this local 1.0.0 checkout?", offering a local
marketplace source instead of publishing.

## Decision

### Statement (Brian), 2026-09-26

> "um, ... so... howabout we fucking merge to main?"

After the agent agreed and proposed merging the committed branch into `main` and
pushing it:

> "do so"

### What this settles

1. A release reaches installed Claude Code plugins by landing on GitHub `main`, not
   through a local marketplace source.
2. On 2026-09-26 `main` was fast-forwarded from `cda2ed1` to `b9a8d7b` and pushed.
   The windowed annotation redesign stayed a draft design; this ruling does not accept it.

## Resolvers

All three messages are in Codex session `01a0db22-1f3b-7ac2-99d2-717b8fa880bc`
(`/home/brian/.codex/sessions/2026/09/26/rollout-2026-09-26T10-34-01-01a0db22-1f3b-7ac2-99d2-717b8fa880bc.jsonl`).
Print each with the message ID in place of `<id>`:

```
jq -r --arg id '<id>' 'select(.type == "response_item" and .payload.id == $id) | [.payload.content[].text] | join("")' /home/brian/.codex/sessions/2026/09/26/rollout-2026-09-26T10-34-01-01a0db22-1f3b-7ac2-99d2-717b8fa880bc.jsonl
```

- Error text: `msg_01a0dc01-38e2-79e1-b7f3-3752c279997f`, 2026-09-26T04:37:42Z.
- "howabout we fucking merge to main?": `msg_01a0dc05-438d-7df0-86fc-f70dfb328dfc`,
  2026-09-26T04:42:07Z.
- "do so": `msg_01a0dc13-736a-7762-8d4e-a46b89090685`, 2026-09-26T04:57:37Z.

## When to revisit

Brian's word.
