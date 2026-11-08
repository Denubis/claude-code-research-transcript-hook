# 2026-11-08: Annotation steps learn session and message IDs from the source log

## Status

**Accepted** 2026-11-08 by Brian. Written by the implementing agent (Claude Opus 5.5),
not in Brian's voice.

## Context

Generation now hides exact session and message IDs from the secret scanner, following
[the ruling that IDs are not keys](README.md#rulings-recorded-elsewhere).
The separate `annotate` and `review-annotations` steps rescan the whole transcript but
never read the source log, so an annotated transcript would still stop on the same ID.

## Decision

### Statement (Brian), 2026-11-08

Asked "Should the annotation and annotation-review steps also re-read the session's
original log so they treat its IDs the same way, as part of this change?":

> "sure?"

Session `52a1e4a3-2392-4ecb-824d-876b5f57b5dc`, message
`d9494739-f40d-4c8a-88f9-4dae1ef55710`, 2026-11-08T09:34:23Z. Resolver:

```
jq -r 'select(.type == "user" and .uuid == "d9494739-f40d-4c8a-88f9-4dae1ef55710") | .message.content' /home/brian/.claude/projects/-home-brian-people-Brian-claude-code-research-transcript-hook/52a1e4a3-2392-4ecb-824d-876b5f57b5dc.jsonl
```

### What this settles

1. `annotate` and `review-annotations` re-read the source log and sub-agent files named
   in the transcript's frontmatter, and treat their IDs as generation does.
2. When a named log no longer exists, those steps scan without its IDs, which is stricter,
   rather than failing outright.

## When to revisit

If reading source logs during annotation proves slow or unsafe, or Brian's word.
