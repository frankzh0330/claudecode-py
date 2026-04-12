"""hooks.py 测试。"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from cc_python.hooks import (
    HookEvent, HookConfig, HookMatcher, HookResult,
    _parse_hook_config, _parse_hook_matcher,
    _get_matching_hooks, _build_hook_input, _parse_hook_stdout,
    _build_result, dispatch_hooks,
)


class TestParseHookConfig:
    def test_valid(self):
        result = _parse_hook_config({"type": "command", "command": "echo hi", "timeout": 10})
        assert result is not None
        assert result.command == "echo hi"
        assert result.timeout == 10

    def test_defaults(self):
        result = _parse_hook_config({"command": "echo hi"})
        assert result is not None
        assert result.type == "command"
        assert result.timeout == 30
        assert result.is_async is False

    def test_no_command(self):
        assert _parse_hook_config({"type": "command"}) is None
        assert _parse_hook_config({}) is None

    def test_async_flag(self):
        result = _parse_hook_config({"command": "echo", "async": True})
        assert result.is_async is True


class TestParseHookMatcher:
    def test_with_matcher(self):
        result = _parse_hook_matcher({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "validate.sh"}],
        })
        assert result is not None
        assert result.matcher == "Bash"
        assert len(result.hooks) == 1

    def test_without_matcher(self):
        result = _parse_hook_matcher({
            "hooks": [{"type": "command", "command": "check.sh"}],
        })
        assert result is not None
        assert result.matcher is None

    def test_no_hooks(self):
        result = _parse_hook_matcher({"matcher": "Bash", "hooks": []})
        assert result is None

    def test_invalid_hook_entry(self):
        result = _parse_hook_matcher({
            "hooks": ["not a dict", {"command": "valid.sh"}],
        })
        assert result is not None
        assert len(result.hooks) == 1


class TestLoadHooksConfig:
    def test_valid(self, tmp_settings):
        tmp_settings({"hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "validate.sh"}]}
            ],
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "check.sh"}]}
            ],
        }})
        from cc_python.hooks import load_hooks_config
        config = load_hooks_config()
        assert HookEvent.PRE_TOOL_USE in config
        assert HookEvent.USER_PROMPT_SUBMIT in config
        assert len(config[HookEvent.PRE_TOOL_USE]) == 1

    def test_empty(self, tmp_settings):
        tmp_settings({})
        from cc_python.hooks import load_hooks_config
        assert load_hooks_config() == {}

    def test_ignores_unknown_events(self, tmp_settings):
        tmp_settings({"hooks": {"UnknownEvent": [{"hooks": [{"command": "x"}]}]}})
        from cc_python.hooks import load_hooks_config
        assert load_hooks_config() == {}


class TestGetMatchingHooks:
    def test_wildcard(self, tmp_settings):
        tmp_settings({"hooks": {"PreToolUse": [
            {"hooks": [{"command": "global.sh"}]}
        ]}})
        hooks = _get_matching_hooks(HookEvent.PRE_TOOL_USE, "bash")
        assert len(hooks) == 1

    def test_tool_name_match(self, tmp_settings):
        tmp_settings({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"command": "bash.sh"}]}
        ]}})
        hooks = _get_matching_hooks(HookEvent.PRE_TOOL_USE, "bash")
        assert len(hooks) == 1
        # case insensitive
        hooks2 = _get_matching_hooks(HookEvent.PRE_TOOL_USE, "BASH")
        assert len(hooks2) == 1

    def test_no_match(self, tmp_settings):
        tmp_settings({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"command": "bash.sh"}]}
        ]}})
        hooks = _get_matching_hooks(HookEvent.PRE_TOOL_USE, "write_file")
        assert len(hooks) == 0

    def test_no_hooks_for_event(self, tmp_settings):
        tmp_settings({"hooks": {}})
        hooks = _get_matching_hooks(HookEvent.STOP)
        assert len(hooks) == 0


class TestBuildHookInput:
    def test_basic(self):
        data = _build_hook_input(HookEvent.PRE_TOOL_USE, "sess1", "/cwd", "bash", {"command": "ls"})
        assert data["hook_event_name"] == "PreToolUse"
        assert data["session_id"] == "sess1"
        assert data["cwd"] == "/cwd"
        assert data["tool_name"] == "bash"
        assert data["tool_input"] == {"command": "ls"}

    def test_optional_fields(self):
        data = _build_hook_input(HookEvent.STOP)
        assert "tool_name" not in data
        assert "tool_input" not in data


class TestParseHookStdout:
    def test_json(self):
        result = _parse_hook_stdout('{"decision": "deny", "reason": "unsafe"}')
        assert result["decision"] == "deny"

    def test_multiline(self):
        result = _parse_hook_stdout("some log\n{\"decision\": \"allow\"}\nmore output")
        assert result["decision"] == "allow"

    def test_no_json(self):
        result = _parse_hook_stdout("just regular output")
        assert result == {}


class TestBuildResult:
    def test_with_decision(self):
        result = _build_result(0, '{"decision": "deny", "reason": "bad"}', "")
        assert result.exit_code == 0
        assert result.decision == "deny"
        assert result.reason == "bad"

    def test_without_decision(self):
        result = _build_result(0, "ok", "")
        assert result.decision is None


class TestDispatchHooks:
    @pytest.mark.asyncio
    async def test_no_match(self, tmp_settings):
        tmp_settings({"hooks": {}})
        results = await dispatch_hooks(HookEvent.PRE_TOOL_USE)
        assert results == []

    @pytest.mark.asyncio
    async def test_short_circuit_on_exit_2(self, tmp_settings):
        tmp_settings({"hooks": {"PreToolUse": [
            {"hooks": [{"command": "block.sh"}, {"command": "never.sh"}]},
        ]}})
        with patch("cc_python.hooks._execute_command_hook", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = HookResult(exit_code=2, stdout="", stderr="blocked")
            results = await dispatch_hooks(HookEvent.PRE_TOOL_USE, tool_name="bash")
        assert len(results) == 1  # 第二个 hook 不执行
        assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_short_circuit_on_deny(self, tmp_settings):
        tmp_settings({"hooks": {"PreToolUse": [
            {"hooks": [{"command": "deny.sh"}, {"command": "never.sh"}]},
        ]}})
        deny_result = HookResult(exit_code=0, stdout='{"decision": "deny"}', stderr="")
        deny_result.decision = "deny"  # manually set since _parse_hook_stdout is bypassed
        with patch("cc_python.hooks._execute_command_hook", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = deny_result
            results = await dispatch_hooks(HookEvent.PRE_TOOL_USE, tool_name="bash")
        assert len(results) == 1
