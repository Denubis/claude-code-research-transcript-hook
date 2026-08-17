# Dependency rationale

The runtime has no direct third-party Python dependencies. Provider JSONL
adaptation, Git discovery, deterministic Markdown rendering, and the CLI use the
standard library.

Development-only dependencies provide tests, coverage, linting, formatting,
property testing, and type checking. Gitleaks is an external runtime executable,
not a Python package; generation fails closed when it cannot be resolved.

`claude-code-transcripts` and Typer were removed in 1.0.0 when HTML/PDF output and
the seven-command archive interface were replaced by one Markdown generator and a
small argparse boundary.
