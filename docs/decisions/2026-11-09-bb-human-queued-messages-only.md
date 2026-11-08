# 2026-11-09: Only the human's queued messages are archived

## Status

**Accepted** 2026-11-09 by Brian. Written by the implementing agent (Claude Opus 5.5),
not in Brian's voice.

## Context

[Messages typed mid-turn are archived as human dialogue](2026-11-08-bb-archive-mid-turn-messages.md)
left open which queued messages count as the human's. Claude Code stores several kinds of
message as `queued_command` attachments: the human's, messages from other Claude sessions
(`origin.kind == "peer"`), task notifications, a coordinator's, and automatic
continuations. Records written by older Claude Code versions name no origin at all;
about 900 of them are plain prompts.

## Decision

### Statement (Brian), 2026-11-09

Responding to the survey of queued-message sources:

> "um, not messages from other claude sessions, and skip 900 ambigious unless they have some indicator to define them as a group and I can read a sample of 10"

Session `52a1e4a3-2392-4ecb-824d-876b5f57b5dc`, message
`e6a286e7-5533-470a-8706-cbd492d66b0d`, 2026-11-08T21:09:54Z (2026-11-09 in Sydney).
Resolver:

```
jq -r 'select(.type == "user" and .uuid == "e6a286e7-5533-470a-8706-cbd492d66b0d") | .message.content' /home/brian/.claude/projects/-home-brian-people-Brian-claude-code-research-transcript-hook/52a1e4a3-2392-4ecb-824d-876b5f57b5dc.jsonl
```

### Statement (Brian), 2026-11-09, on the unmarked group

The implementing agent reported that 893 of the 894 unmarked queued prompts were written
by Claude Code 2.1.97 to 2.1.179 (2026-04-09 to 2026-06-17), that human-marked records
begin at 2.1.181, and that no later version writes an unmarked prompt except one
automated test probe. It showed ten of them picked at random (`random.seed(20261109)`)
and asked: "Should the transcript archive treat the 893 unmarked queued messages from
Claude Code 2.1.179 and earlier as your own words?"

> "yeah those are mine"

Session `52a1e4a3-2392-4ecb-824d-876b5f57b5dc`, message
`0d30a904-2e7d-4e23-b4d9-7b2d030fefef`, 2026-11-08T21:37:29Z (2026-11-09 in Sydney).
Resolver:

```
jq -r 'select(.type == "user" and .uuid == "0d30a904-2e7d-4e23-b4d9-7b2d030fefef") | .message.content' /home/brian/.claude/projects/-home-brian-people-Brian-claude-code-research-transcript-hook/52a1e4a3-2392-4ecb-824d-876b5f57b5dc.jsonl
```

### What this settles

1. Messages from other Claude sessions are not archived as human turns.
2. Queued prompts that name no origin are left out, unless some indicator defines
   them as a group and Brian has read a sample of ten from that group.
3. That indicator exists for one group: a queued prompt with no origin, written by
   Claude Code 2.1.179 or earlier, is Brian's own words and is archived as a human
   turn. Unmarked prompts from later versions stay out.

## When to revisit

Brian's word.
