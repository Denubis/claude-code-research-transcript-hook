"""Tests for the text-output normaliser.

Generated archive files must satisfy the same rules pre-commit-hooks'
``trailing-whitespace`` and ``end-of-file-fixer`` enforce, so committing an
in-tree archive (``target: here``) does not bounce on every release.
"""

from pathlib import Path

import pytest

from claude_transcript_archive.archive import normalise_text_outputs


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    return tmp_path


class TestNormaliseTextOutputs:
    def test_strips_trailing_whitespace_from_lines(self, out_dir: Path):
        target = out_dir / "conversation.md"
        target.write_text("# Title  \n\nbody line   \nanother line\n", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 1
        assert target.read_text(encoding="utf-8") == "# Title\n\nbody line\nanother line\n"

    def test_adds_trailing_newline_when_missing(self, out_dir: Path):
        target = out_dir / "session.meta.json"
        target.write_text('{"a": 1}', encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 1
        assert target.read_text(encoding="utf-8") == '{"a": 1}\n'

    def test_collapses_multiple_trailing_newlines(self, out_dir: Path):
        target = out_dir / "conversation.md"
        target.write_text("body\n\n\n\n", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 1
        assert target.read_text(encoding="utf-8") == "body\n"

    def test_normalises_html(self, out_dir: Path):
        target = out_dir / "index.html"
        target.write_text("<html>  \n<body>x</body>\n</html>", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 1
        assert target.read_text(encoding="utf-8") == "<html>\n<body>x</body>\n</html>\n"

    def test_normalises_marker_files(self, out_dir: Path):
        title = out_dir / ".title"
        size = out_dir / ".last_size"
        title.write_text("My Title  ", encoding="utf-8")
        size.write_text("1024", encoding="utf-8")
        normalise_text_outputs(out_dir)
        assert title.read_text(encoding="utf-8") == "My Title\n"
        assert size.read_text(encoding="utf-8") == "1024\n"

    def test_normalises_jsonl(self, out_dir: Path):
        target = out_dir / "raw-transcript.jsonl"
        target.write_text('{"a": 1}\n{"b": 2}', encoding="utf-8")
        normalise_text_outputs(out_dir)
        assert target.read_text(encoding="utf-8") == '{"a": 1}\n{"b": 2}\n'

    def test_skips_binary_files(self, out_dir: Path):
        pdf = out_dir / "conversation.pdf"
        # Bytes that would be unsafe to round-trip as UTF-8 text
        pdf.write_bytes(b"%PDF-1.4\n\x00\x01\x02trailing  ")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 0
        assert pdf.read_bytes() == b"%PDF-1.4\n\x00\x01\x02trailing  "

    def test_no_op_on_clean_files(self, out_dir: Path):
        target = out_dir / "conversation.md"
        target.write_text("body\n", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 0
        assert target.read_text(encoding="utf-8") == "body\n"

    def test_idempotent(self, out_dir: Path):
        target = out_dir / "conversation.md"
        target.write_text("a  \nb\n\n\n", encoding="utf-8")
        first = normalise_text_outputs(out_dir)
        assert first == 1
        second = normalise_text_outputs(out_dir)
        assert second == 0
        assert target.read_text(encoding="utf-8") == "a\nb\n"

    def test_empty_file_left_empty(self, out_dir: Path):
        target = out_dir / "conversation.md"
        target.write_text("", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 0
        assert target.read_text(encoding="utf-8") == ""

    def test_recurses_into_subdirectories(self, out_dir: Path):
        sub = out_dir / "nested"
        sub.mkdir()
        target = sub / "deep.md"
        target.write_text("deep  \n", encoding="utf-8")
        rewritten = normalise_text_outputs(out_dir)
        assert rewritten == 1
        assert target.read_text(encoding="utf-8") == "deep\n"
