# Decision register

Human rulings for the transcript archiver, each with a resolver to the original message.

## Rulings recorded here

- [2026-08-17: Consecutive tool reports are grouped](2026-08-17-bb-group-tool-activity.md)
- [2026-08-17: Agy annotates transcripts into a permanent sidecar](2026-08-17-bb-agy-annotation-sidecar.md)
- [2026-09-26: Releases reach Claude Code through GitHub main](2026-09-26-bb-release-through-github-main.md)
- [2026-11-08: Annotation steps learn session and message IDs from the source log](2026-11-08-bb-annotation-reads-source-ids.md)
- [2026-11-08: A hand-kept allowlist of IDs that are not keys](2026-11-08-bb-hand-kept-id-allowlist.md)
- [2026-11-08: Messages typed mid-turn are archived as human dialogue](2026-11-08-bb-archive-mid-turn-messages.md)
- [2026-11-09: Only the human's queued messages are archived](2026-11-09-bb-human-queued-messages-only.md)

## Rulings recorded elsewhere

These were made in the melica project session and are recorded there with resolvers.

- IDs are not keys: the scanner treats session and message IDs as IDs.
  `/media/brian/storage/people/Adela/melica/docs/architecture/decisions/2026-11-08-bb-session-ids-are-not-keys.md`
- The archiver accepts records that name a parent session (`fork-context-ref`).
  `/media/brian/storage/people/Adela/melica/docs/architecture/decisions/2026-11-08-bb-archiver-accepts-parent-session-records.md`
