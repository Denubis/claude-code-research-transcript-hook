"""HTML, markdown, and PDF output generation."""

import html as html_module
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# LaTeX preamble for PDF generation with speaker turn styling
# Adapted from PromptGrimoire's conversation export
PDF_PREAMBLE = r"""
\usepackage{fontspec}
\setmainfont{DejaVu Serif}
\usepackage{xcolor}
\usepackage[a4paper,left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm]{geometry}
\usepackage[framemethod=tikz]{mdframed}
\usepackage{fancyvrb}
\usepackage{longtable}
\usepackage{booktabs}

% Speaker colours
\definecolor{usercolor}{HTML}{4A90D9}
\definecolor{assistantcolor}{HTML}{7B68EE}

% Paragraph formatting (no indent, paragraph spacing)
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5\baselineskip}

% Speaker turn environments with left border
\newmdenv[
  topline=false,
  bottomline=false,
  rightline=false,
  linewidth=3pt,
  linecolor=usercolor,
  innerleftmargin=1em,
  innerrightmargin=0pt,
  innertopmargin=0pt,
  innerbottommargin=0pt,
  skipabove=0pt,
  skipbelow=0pt
]{userturn}
\newmdenv[
  topline=false,
  bottomline=false,
  rightline=false,
  linewidth=3pt,
  linecolor=assistantcolor,
  innerleftmargin=1em,
  innerrightmargin=0pt,
  innertopmargin=0pt,
  innerbottommargin=0pt,
  skipabove=0pt,
  skipbelow=0pt
]{assistantturn}

% Pandoc compatibility
\providecommand{\tightlist}{%
  \setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\setlength{\emergencystretch}{3em}
"""

# Lua filter for pandoc to handle speaker turn markers
# Converts data-speaker attributes to mdframed environments
SPEAKER_LUA_FILTER = r"""
-- Pandoc Lua filter for speaker turn styling
-- Converts Div elements with data-speaker attribute to mdframed environments

local current_speaker = nil

function Div(elem)
  if FORMAT ~= 'latex' then return elem end

  local speaker = elem.attr.attributes['speaker']
  if speaker then
    local result = {}

    -- Close previous speaker turn if open
    if current_speaker then
      local prev_env = current_speaker == 'user' and 'userturn' or 'assistantturn'
      table.insert(result, pandoc.RawBlock('latex', '\\end{' .. prev_env .. '}'))
    end

    -- Emit speaker label
    local label = speaker == 'user' and 'User:' or 'Assistant:'
    local color = speaker == 'user' and 'usercolor' or 'assistantcolor'

    table.insert(result, pandoc.RawBlock('latex', string.format([[

\vspace{0.8\baselineskip}
\noindent{\footnotesize\textcolor{%s}{\textbf{%s}}}
\vspace{0.3\baselineskip}
]], color, label)))

    -- Open new speaker turn environment
    local new_env = speaker == 'user' and 'userturn' or 'assistantturn'
    table.insert(result, pandoc.RawBlock('latex', '\\begin{' .. new_env .. '}'))

    -- Add the actual content of the div
    for _, block in ipairs(elem.content) do
      table.insert(result, block)
    end

    current_speaker = speaker
    return result
  end

  return elem
end

function Pandoc(doc)
  if FORMAT ~= 'latex' then return doc end

  -- Close final speaker turn if open
  if current_speaker then
    local env = current_speaker == 'user' and 'userturn' or 'assistantturn'
    table.insert(doc.blocks, pandoc.RawBlock('latex', '\\end{' .. env .. '}'))
    current_speaker = nil
  end

  return doc
end
"""


def _format_file_path(file_path: str, max_parts: int = 2) -> str:
    """Format a file path for display, keeping only the last components."""
    path = Path(file_path)
    return "/".join(path.parts[-max_parts:]) if len(path.parts) > max_parts else path.name


def format_tool_summary(tool_name: str, tool_input: dict) -> str:  # noqa: PLR0911
    """Format a tool call as a one-line summary.

    Examples:
        Read: src/cli.py
        Write: output.txt
        Edit: src/main.py
        Bash: git status
        Grep: pattern in *.py
    """
    match tool_name:
        case "Read":
            display = _format_file_path(tool_input.get("file_path", "unknown"))
            return f"Read: {display}"
        case "Write":
            return f"Write: {Path(tool_input.get('file_path', 'unknown')).name}"
        case "Edit":
            return f"Edit: {Path(tool_input.get('file_path', 'unknown')).name}"
        case "Bash":
            command = tool_input.get("command", "")
            if len(command) > 60:
                command = command[:57] + "..."
            return f"Bash: `{command}`"
        case "Grep":
            pattern = tool_input.get("pattern", "")
            path = tool_input.get("path", "")
            return f"Grep: '{pattern}' in {path or '.'}"
        case "Glob":
            return f"Glob: {tool_input.get('pattern', '')}"
        case "Task":
            return f"Task: {tool_input.get('description', '')}"
        case "WebFetch":
            url = tool_input.get("url", "")
            return f"WebFetch: {url[:50]}..." if len(url) > 50 else f"WebFetch: {url}"
        case "WebSearch":
            return f"WebSearch: '{tool_input.get('query', '')}'"
        case _:
            return tool_name


def extract_conversation_messages(content: str) -> list[dict]:
    """Extract user and assistant messages from transcript JSONL.

    Returns a list of message dicts:
    {
        "role": "user" | "assistant",
        "text": str,
        "tool_calls": [{"name": str, "summary": str}, ...]
    }

    Filters out:
    - Thinking blocks
    - System messages (unless they contain user-visible content)
    - Tool results (incorporated into tool_call summaries)
    """
    messages = []

    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        if entry_type == "file-history-snapshot":
            continue

        message = entry.get("message", {})
        role = message.get("role", entry_type or "")
        msg_content = message.get("content", [])

        if role == "user" or entry_type == "user":
            # Extract text from user message, skip tool results
            text_parts = []
            if isinstance(msg_content, str):
                text_parts.append(msg_content)
            elif isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                        # Skip tool_result blocks - they're responses to assistant

            text = "\n".join(text_parts).strip()
            if text:
                # Skip system reminder messages and IDE context
                if text.startswith("<system-reminder>"):
                    continue
                if text.startswith("<ide_"):
                    continue
                # Skip skill injections (loaded skill content appears as user message)
                if text.startswith("# ") and len(text) > 500:
                    # Long markdown content starting with heading is likely a skill
                    continue
                if "<command-name>" in text or "<command-message>" in text:
                    # Command invocation metadata
                    continue
                if "Base directory for this skill:" in text:
                    # Skill loading metadata
                    continue
                if text.startswith("Launching skill:"):
                    # Skill launch confirmation
                    continue
                messages.append({
                    "role": "user",
                    "text": text,
                    "tool_calls": [],
                })

        elif role == "assistant" or entry_type == "assistant":
            text_parts = []
            tool_calls = []

            if isinstance(msg_content, list):
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type")

                    if block_type == "text":
                        text_parts.append(block.get("text", ""))

                    elif block_type == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})
                        summary = format_tool_summary(tool_name, tool_input)
                        tool_calls.append({"name": tool_name, "summary": summary})

                    # Skip thinking blocks - user requested no thinking

            text = "\n".join(text_parts).strip()
            if text or tool_calls:
                messages.append({
                    "role": "assistant",
                    "text": text,
                    "tool_calls": tool_calls,
                })

    return messages


def generate_conversation_markdown(
    messages: list[dict],
    title: str,
    metadata: dict | None = None,
) -> str:
    """Generate readable markdown from conversation messages.

    Args:
        messages: List of message dicts from extract_conversation_messages
        title: Title for the document
        metadata: Optional session metadata dict for header info
    """
    lines = [f"# {title}", ""]

    # Add metadata header if available
    if metadata:
        session = metadata.get("session", {})
        stats = metadata.get("statistics", {})
        model_info = metadata.get("model", {})

        if session.get("started_at"):
            lines.append(f"**Date**: {session['started_at'][:10]}")
        if model_info.get("model_id"):
            lines.append(f"**Model**: {model_info['model_id']}")
        if model_info.get("claude_code_version"):
            lines.append(f"**Claude Code**: v{model_info['claude_code_version']}")
        if session.get("duration_minutes"):
            lines.append(f"**Duration**: {session['duration_minutes']} minutes")
        if stats.get("turns"):
            lines.append(f"**Turns**: {stats['turns']}")
        if stats.get("estimated_cost_usd") and stats["estimated_cost_usd"] > 0:
            lines.append(f"**Estimated cost**: ${stats['estimated_cost_usd']:.2f}")
        lines.append("")

        # Add Three Ps if available
        three_ps = metadata.get("three_ps", {})
        if three_ps:
            prompt_summary = three_ps.get("prompt_summary", "")
            process_summary = three_ps.get("process_summary", "")
            provenance_summary = three_ps.get("provenance_summary", "")

            if prompt_summary or process_summary or provenance_summary:
                lines.append("## Three Ps (IDW2025)")
                lines.append("")
                if prompt_summary:
                    lines.append(f"**Prompt**: {prompt_summary}")
                    lines.append("")
                if process_summary:
                    lines.append(f"**Process**: {process_summary}")
                    lines.append("")
                if provenance_summary:
                    lines.append(f"**Provenance**: {provenance_summary}")
                    lines.append("")

        lines.append("---")
        lines.append("")

    for msg in messages:
        role = msg["role"]
        text = msg["text"]
        tool_calls = msg.get("tool_calls", [])

        # Speaker header
        if role == "user":
            lines.append("## User")
        else:
            lines.append("## Assistant")
        lines.append("")

        # Message text
        if text:
            lines.append(text)
            lines.append("")

        # Tool calls as bullet list
        if tool_calls:
            lines.append("**Tools used:**")
            for tc in tool_calls:
                lines.append(f"- {tc['summary']}")
            lines.append("")

    return "\n".join(lines)


def sanitize_for_pdf(text: str) -> str:
    """Remove characters that cause issues in LaTeX/PDF generation.

    Removes control characters and other problematic Unicode while
    preserving normal whitespace and printable characters.
    """
    if not text:
        return text

    # Remove control characters except tab, newline, carriage return
    # Control chars are U+0000-U+001F and U+007F-U+009F
    result = []
    for char in text:
        code = ord(char)
        # Keep tab (9), newline (10), carriage return (13), and normal printable
        if (code in {9, 10, 13} or code >= 32) and code != 127 and not (128 <= code <= 159):
            result.append(char)

    return "".join(result)


def generate_conversation_html_for_pdf(
    messages: list[dict],
    title: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Generate HTML with speaker markers for pandoc PDF conversion.

    Uses data-speaker attributes that the Lua filter converts to
    mdframed environments with colored borders.
    """
    safe_title = sanitize_for_pdf(title)
    lines = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>{html_module.escape(safe_title)}</title>",
        "</head>",
        "<body>",
        # Note: Title comes from pandoc metadata, not <h1>, to avoid duplication
    ]

    # Add metadata header if available
    if metadata:
        session = metadata.get("session", {})
        model_info = metadata.get("model", {})
        stats = metadata.get("statistics", {})

        date_str = (session.get("started_at") or "")[:10]
        model_id = model_info.get("model_id") or "unknown"
        version = model_info.get("claude_code_version") or ""
        duration = session.get("duration_minutes", 0)
        turns = stats.get("turns", 0)

        lines.append("<p><strong>Date</strong>: " + html_module.escape(date_str) + "<br>")
        lines.append("<strong>Model</strong>: " + html_module.escape(model_id) + "<br>")
        if version:
            lines.append("<strong>Claude Code</strong>: " + html_module.escape(version) + "<br>")
        lines.append(f"<strong>Duration</strong>: {duration} minutes<br>")
        lines.append(f"<strong>Turns</strong>: {turns}</p>")

        # Add Three Ps if available
        three_ps = metadata.get("three_ps", {})
        if three_ps:
            prompt_summary = three_ps.get("prompt_summary", "")
            process_summary = three_ps.get("process_summary", "")
            provenance_summary = three_ps.get("provenance_summary", "")

            if prompt_summary or process_summary or provenance_summary:
                lines.append("<h2>Three Ps (IDW2025)</h2>")
                for label, value in [
                    ("Prompt", prompt_summary),
                    ("Process", process_summary),
                    ("Provenance", provenance_summary),
                ]:
                    if value:
                        escaped = html_module.escape(sanitize_for_pdf(value))
                        lines.append(f"<p><strong>{label}</strong>: {escaped}</p>")

        lines.append("<hr>")

    for msg in messages:
        role = msg["role"]
        text = sanitize_for_pdf(msg["text"])
        tool_calls = msg.get("tool_calls", [])

        # Open speaker div with data-speaker attribute
        lines.append(f'<div data-speaker="{role}">')

        # Message text as paragraphs
        if text:
            for raw_para in text.split("\n\n"):
                para = raw_para.strip()
                if para:
                    # Preserve single newlines within paragraphs as <br>
                    para_lines = para.split("\n")
                    para_html = "<br>\n".join(html_module.escape(line) for line in para_lines)
                    lines.append(f"<p>{para_html}</p>")

        # Tool calls as list
        if tool_calls:
            lines.append("<p><strong>Tools used:</strong></p>")
            lines.append("<ul>")
            for tc in tool_calls:
                summary = sanitize_for_pdf(tc["summary"])
                lines.append(f"<li>{html_module.escape(summary)}</li>")
            lines.append("</ul>")

        lines.append("</div>")

    lines.extend(["</body>", "</html>"])
    return "\n".join(lines)


def generate_conversation_pdf(
    messages: list[dict],
    title: str,
    output_path: Path,
    quiet: bool = False,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Generate PDF from conversation using pandoc with speaker styling.

    Returns True on success, False on failure.
    """
    # Sanitize title for LaTeX
    safe_title = sanitize_for_pdf(title)

    # Generate HTML with speaker markers
    html_content = generate_conversation_html_for_pdf(messages, safe_title, metadata)

    # Write temporary files for pandoc
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Write HTML input
        html_path = tmpdir_path / "input.html"
        html_path.write_text(html_content, encoding="utf-8")

        # Write Lua filter
        filter_path = tmpdir_path / "speaker.lua"
        filter_path.write_text(SPEAKER_LUA_FILTER, encoding="utf-8")

        # Write LaTeX header
        header_path = tmpdir_path / "header.tex"
        header_path.write_text(PDF_PREAMBLE, encoding="utf-8")

        # Run pandoc
        cmd = [
            "pandoc",
            str(html_path),
            "-f", "html+native_divs",
            "-t", "pdf",
            "--pdf-engine=lualatex",
            f"--include-in-header={header_path}",
            f"--lua-filter={filter_path}",
            "-V", "documentclass=article",
            "-V", "papersize=a4",
            f"--metadata=title:{safe_title}",
            "-o", str(output_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True, encoding="utf-8",
                check=False,
                timeout=120,  # 2 minutes should be plenty for PDF generation
            )
            if result.returncode != 0:
                if not quiet:
                    print(f"Warning: PDF generation failed: {result.stderr}", file=sys.stderr)
                return False
            return True
        except subprocess.TimeoutExpired:
            if not quiet:
                print("Warning: PDF generation timed out", file=sys.stderr)
            return False
        except FileNotFoundError:
            if not quiet:
                print("Warning: pandoc not found, skipping PDF generation", file=sys.stderr)
            return False


def update_html_titles(output_dir: Path, title: str):
    """Update HTML file titles to use the conversation title."""
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        # Replace generic title
        content = re.sub(
            r"<title>Claude Code transcript[^<]*</title>",
            f"<title>{title}</title>",
            content,
        )
        # Add title as h1 at top of body if index.html
        if html_file.name == "index.html":
            content = re.sub(
                r"(<body[^>]*>)",
                f'\\1\n<h1 style="margin: 20px; font-family: system-ui;">{title}</h1>',
                content,
                count=1,
            )
        html_file.write_text(content, encoding="utf-8")
