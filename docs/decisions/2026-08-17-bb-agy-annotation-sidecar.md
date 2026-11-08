# 2026-08-17: Agy annotates transcripts into a permanent sidecar

## Status

**Accepted** 2026-08-17 by Brian. Recorded on 2026-11-09 by Claude Opus 5.5 from the
Codex session in which the rulings were made; not in Brian's voice.

## Context

Brian asked whether generation should call Agy to pull annotations and keywords to the
top of each transcript. The agent recommended against making deterministic generation
depend on a model, and proposed a separate annotation step.

## Decision

### Statements (Brian), 2026-08-17

1. Asked "Should Agy enrichment be an explicit post-generation step rather than
   mandatory during `generate`?":

   > "yes"

2. Asked whether the annotation schema should be title, keywords, key decisions,
   affected artifacts and the Three Ps:

   > "yep, basically anything that qualifies as a note or ADR"

3. Asked "Should enrichment only record those candidates in the transcript—never create
   `.notes` or ADR files automatically?":

   > "yes"

4. Asked whether `annotate` should only emit a JSON draft for confirmation:

   > "eh, just make it a sidecar"

5. Asked "Should the sidecar remain permanently after human review, rather than being
   temporary staging?":

   > "yes"

6. On what the annotation is for:

   > "basically it's agy (probably flash-3.7 this week) annotating the conversation for anything interesting"

   > "specifically, feedback, or dropped things, or miscommunication, or indications where user frustration means something isn't tuned right"

7. Asked "Should resolved incidents remain in the sidecar with both the failure and
   later repair cited?":

   > "yes"

8. When Agy was quota-blocked:

   > "um... that is a risk, and if it fails to fire then no sidecar (and maybe repair later) I think we just need to take notes for tonight and compact and I'll pick this up tomorrow"

### What this settles

1. Annotation is an explicit step after generation; `generate` never calls Agy.
2. Agy writes a permanent `annotations.json` beside `transcript.md`, kept after review.
3. Annotations cover anything that qualifies as a note or ADR, and particularly
   feedback, dropped threads, miscommunication and frustration that signals something
   is not tuned right. Resolved incidents stay, citing the failure and the repair.
4. Annotation flags note and ADR candidates but never creates `.notes` or ADR files.
5. If Agy fails, no sidecar is written; a later run can repair the absence.

The default model `gemini-3.7-flash-medium` was the agent's reading of statement 6,
not a separate ruling. Feeding Agy the rendered transcript on stdin from an isolated
directory, and citing source locators rather than Markdown line numbers, were the
agent's design choices; no ruling covers them.

## Resolvers

Codex session `01a007ae-7f19-7dd1-aef9-603cc1e29513`, started in
`/home/brian/people/Brian/brian-ed3d-plugins` but working on this repository. Its user
messages carry no message ID; each timestamp is unique among them. Print one with the
timestamp in place of `<ts>`:

```
jq -r --arg ts '<ts>' 'select(.type == "response_item" and .payload.role == "user" and .timestamp == $ts) | [.payload.content[].text] | join("")' /home/brian/.codex/sessions/2026/08/16/rollout-2026-08-16T09-07-51-01a007ae-7f19-7dd1-aef9-603cc1e29513.jsonl
```

1. `2026-08-17T10:08:11.335Z`
2. `2026-08-17T10:41:24.042Z`
3. `2026-08-17T10:42:28.955Z`
4. `2026-08-17T10:47:19.360Z`
5. `2026-08-17T10:47:39.462Z`
6. `2026-08-17T10:48:01.532Z` and `2026-08-17T10:48:37.121Z`
7. `2026-08-17T10:50:14.573Z`
8. `2026-08-17T10:54:56.103Z`

## When to revisit

Brian's word. A draft redesign for long transcripts,
`docs/design-plans/2026-08-19-windowed-transcript-annotations.md`, has not been accepted.
