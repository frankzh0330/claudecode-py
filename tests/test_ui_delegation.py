"""Quiet UI delegation card helpers."""

import json

import pytest

pytest.importorskip("rich")

from termpilot.ui import _preview_lines, _status_for_tool, _tool_summary


def test_batch_agent_status_and_summary():
    input_data = {
        "tasks": [
            {"subagent_type": "Explore", "description": "Inspect commands", "prompt": "Find commands."},
            {"subagent_type": "Plan", "description": "Plan redo", "prompt": "Plan redo."},
        ]
    }

    assert _status_for_tool("agent", input_data) == "Running 2 delegated agents…"
    assert _tool_summary("agent", input_data, "") == "Delegated 2 subagents"


def test_batch_agent_preview_summarizes_each_subtask():
    result = json.dumps({
        "delegated_tasks": [
            {
                "index": 1,
                "subagent_type": "Explore",
                "description": "Inspect commands",
                "success": True,
            },
            {
                "index": 2,
                "subagent_type": "Nope",
                "description": "Bad agent",
                "success": False,
            },
        ],
        "summary": {"total": 2, "succeeded": 1, "failed": 1},
    })

    preview = _preview_lines("agent", result, True)

    assert "1. Explore - Inspect commands (completed)" in preview
    assert "2. Nope - Bad agent (failed)" in preview
    assert "Summary: 1/2 succeeded" in preview


def test_single_agent_preview_is_short_and_compact():
    long_line = "x" * 200
    result = "\n".join([
        "# Full analysis",
        long_line,
        "Finding 1",
        "Finding 2",
        "Finding 3",
    ])

    preview = _preview_lines("agent", result, True)

    assert len(preview) == 3
    assert len(preview[1]) <= 121
