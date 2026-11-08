# 2026-08-17: Consecutive tool reports are grouped

## Status

**Accepted** 2026-08-17 by Brian. Recorded on 2026-11-09 by Claude Opus 5.5 from the
Codex session in which the ruling was made; not in Brian's voice.

## Context

During transcript UAT on the real corpus, one transcript held 1,312 "Ran code" lines, one
per tool-only assistant turn.

## Decision

### Statement (Brian), 2026-08-17

Reviewing that transcript:

> "honestly, the ran-code lines are just... lots? they should be combined."

The agent proposed combining adjacent tool-only turns into one `## Tool activity` block
with source ranges and counts, never across dialogue or context boundaries. After it
was built, the agent asked: "Does this now feel compact enough without losing useful
progress narration?"

> "yeah, this is good"

### What this settles

Adjacent tool-only turns render as one tool-activity block with counts and source
ranges. A dialogue turn or context boundary ends the block.

## Resolvers

Codex session `01a007ae-7f19-7dd1-aef9-603cc1e29513`, started in
`/home/brian/people/Brian/brian-ed3d-plugins` but working on this repository. Its user
messages carry no message ID; each timestamp is unique among them. Print one with the
timestamp in place of `<ts>`:

```
jq -r --arg ts '<ts>' 'select(.type == "response_item" and .payload.role == "user" and .timestamp == $ts) | [.payload.content[].text] | join("")' /home/brian/.codex/sessions/2026/08/16/rollout-2026-08-16T09-07-51-01a007ae-7f19-7dd1-aef9-603cc1e29513.jsonl
```

- "they should be combined": `2026-08-17T10:06:06.098Z`
- "yeah, this is good": `2026-08-17T10:39:56.388Z`

## When to revisit

Brian's word.
