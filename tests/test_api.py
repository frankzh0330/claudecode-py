"""API tool execution helpers."""

import pytest

from termpilot.api import _apply_permission_rule_update, _call_openai_streaming, _tool_result_success
from termpilot.permissions import PermissionBehavior, PermissionContext, PermissionRule


def test_agent_error_result_marks_tool_failed():
    assert _tool_result_success("agent", "Agent API error: timed out") is False
    assert _tool_result_success("agent", "Error: Agent prompt is required.") is False


def test_non_agent_string_result_is_successful():
    assert _tool_result_success("bash", "Error: command returned non-zero") is True


def test_permission_rule_update_replaces_opposite_in_memory_rule():
    ctx = PermissionContext(
        deny_rules=[
            PermissionRule(
                tool_name="bash",
                pattern="*",
                behavior=PermissionBehavior.DENY,
            )
        ]
    )

    _apply_permission_rule_update(ctx, {
        "tool_name": "bash",
        "pattern": "*",
        "behavior": "allow",
    })

    assert ctx.deny_rules == []
    assert len(ctx.allow_rules) == 1
    assert ctx.allow_rules[0].behavior == PermissionBehavior.ALLOW


@pytest.mark.asyncio
async def test_openai_streaming_requests_and_yields_usage():
    class Usage:
        prompt_tokens = 123
        completion_tokens = 45

    class Chunk:
        choices = []
        usage = Usage()

    class Stream:
        async def __aiter__(self):
            yield Chunk()

    class Completions:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return Stream()

    completions = Completions()
    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": completions})()},
    )()

    events = [
        event async for event in _call_openai_streaming(
            client,
            "glm-5.1",
            "system",
            [{"role": "user", "content": "hi"}],
        )
    ]

    assert completions.kwargs["stream_options"] == {"include_usage": True}
    assert events == [{
        "type": "usage",
        "usage": {
            "input_tokens": 123,
            "output_tokens": 45,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }]
