"""Shared test fixtures for claude_transcript_archive tests."""

import json

import pytest


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def sample_transcript_content():
    """Sample transcript JSONL content for testing."""
    entries = [
        {
            "type": "user",
            "timestamp": "2026-01-14T10:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello, can you help me with Python?"}],
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-14T10:00:30.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "text", "text": "Of course! I'd be happy to help with Python."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/home/user/project/main.py"},
                    },
                ],
            },
            "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-14T10:01:00.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me analyze this..."},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/home/user/project/main.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/home/user/project/new_file.py"},
                    },
                ],
            },
            "usage": {"input_tokens": 200, "output_tokens": 100, "cache_read_input_tokens": 50},
        },
        {
            "type": "user",
            "timestamp": "2026-01-14T10:05:00.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Thanks! This continues from session "
                        "abc12345-1234-1234-1234-123456789abc",
                    }
                ],
            },
        },
    ]
    return "\n".join(json.dumps(entry) for entry in entries)


@pytest.fixture
def sample_transcript_file(temp_dir, sample_transcript_content):
    """Create a sample transcript file."""
    transcript_path = temp_dir / "test-transcript.jsonl"
    transcript_path.write_text(sample_transcript_content)
    return transcript_path
