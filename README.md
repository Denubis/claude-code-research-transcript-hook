# Claude Code and Codex Transcript Archive

Archive AI coding sessions as deterministic, searchable Markdown with reviewed
Prompt, Process, and Provenance metadata.

The archive is repository-local. Discovery includes Claude Code and Codex parent
sessions that can be tied to the current Git repository, including deleted
worktrees when repository evidence can recover their membership.

## Output contract

Each parent session produces exactly one file:

```text
ai_transcripts/sessions/<claude|codex>/<session-id>/transcript.md
```

The Markdown frontmatter records source identity, repository evidence, the Three
Ps, and whether those summaries still need human review. The body contains the
human/main-agent dialogue, concise visible tool reports, context boundaries, and
an explicit omission/redaction ledger.

Subagent transcripts are validated and counted but are not copied into the
dialogue. Private reasoning, raw JSONL, HTML, PDFs, and metadata sidecars are not
archived. The provider's local JSONL remains the source of record named in the
Markdown frontmatter.

## Install

Install or refresh the CLI with uv:

```bash
uv tool install --force git+https://github.com/Denubis/claude-code-research-transcript-hook
```

The repository also provides the same `transcript` skill for Claude Code, Codex,
and Antigravity through their native plugin manifests.

## Generate transcripts

From the repository being archived:

```bash
claude-research-transcript generate --repo . --source all
```

Use `--source claude` or `--source codex` on a machine that only has one provider
store. Generation is incremental; unchanged source sessions are skipped.

Gitleaks is required and resolved from `--gitleaks`, `GITLEAKS`, `PATH`, or the
configured pre-commit environment. Generation fails closed if no scanner is
available or a rendered candidate contains a secret finding.

## Review the Three Ps

Create a JSON file through a structured file-edit tool, not shell interpolation:

```json
{
  "prompt": "What the user needed",
  "process": "How the session approached it",
  "provenance": "Why this session matters in the wider work"
}
```

Then update the one archived parent session:

```bash
claude-research-transcript update \
  --repo . \
  --tool claude \
  --session-id <session-id> \
  --metadata-file <three-ps.json>
```

Direct `--prompt`, `--process`, and `--provenance` options remain available for
trusted manual invocations. Supplying all three non-empty values changes
`needs_review` to `false`.

## Development

```bash
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ty check src tests
```

Python 3.12 or later is required. Runtime code uses the standard library.

## License

MIT
