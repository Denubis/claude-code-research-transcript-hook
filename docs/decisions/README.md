# Decision register

Human rulings for the transcript archiver, each with a resolver to the original message.

## Rulings recorded here

- [2026-11-08: Annotation steps learn session and message IDs from the source log](2026-11-08-bb-annotation-reads-source-ids.md)
- [2026-11-08: A hand-kept allowlist of IDs that are not keys](2026-11-08-bb-hand-kept-id-allowlist.md)

## Rulings recorded elsewhere

These were made in the melica project session and are recorded there with resolvers.

- IDs are not keys: the scanner treats session and message IDs as IDs.
  `/media/brian/storage/people/Adela/melica/docs/architecture/decisions/2026-11-08-bb-session-ids-are-not-keys.md`
- The archiver accepts records that name a parent session (`fork-context-ref`).
  `/media/brian/storage/people/Adela/melica/docs/architecture/decisions/2026-11-08-bb-archiver-accepts-parent-session-records.md`
