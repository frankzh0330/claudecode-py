"""compact.py 测试。"""

import pytest

from termpilot.compact import (
    estimate_tokens, micro_compact,
    _find_split_index, _extract_summary, _messages_to_text,
    CONTEXT_WINDOW_DEFAULT, COMPACT_THRESHOLD_RATIO, TOKEN_CHARS_RATIO,
)


class TestEstimateTokens:
    def test_string_content(self):
        msg = {"role": "user", "content": "a" * 300}
        tokens = estimate_tokens([msg])
        assert tokens == 300 // TOKEN_CHARS_RATIO + 4  # +4 per message

    def test_list_content(self):
        msg = {"role": "assistant", "content": [
            {"type": "text", "text": "a" * 300},
            {"type": "tool_use", "input": {"command": "a" * 60}},
        ]}
        tokens = estimate_tokens([msg])
        assert tokens > 0

    def test_with_system_prompt(self):
        sp = "x" * 3000
        tokens = estimate_tokens([], sp)
        assert tokens == 3000 // TOKEN_CHARS_RATIO

    def test_empty_messages(self):
        assert estimate_tokens([]) == 0


class TestMicroCompact:
    def test_no_truncation(self):
        """少量 tool_result 不会被截断。"""
        messages = []
        for i in range(5):
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "content": f"result {i}" * 100}],
            })
        result = micro_compact(messages)
        assert result == messages

    def test_truncates_old(self):
        """超过 10 个 tool_result 时截断旧的。"""
        messages = []
        for i in range(15):
            tool_id = f"tool_{i}"
            # assistant 消息含 tool_use（让 compact 知道是哪个工具）
            messages.append({
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_id, "name": "read_file", "input": {}}],
            })
            # user 消息含 tool_result
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": f"result {i}" * 100}],
            })
        result = micro_compact(messages)
        # 前 5 个被截断（查找第一个 user 消息的 tool_result）
        first_user_result = None
        for msg in result:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                first_user_result = msg["content"][0]["content"]
                break
        assert first_user_result is not None
        assert "truncated" in first_user_result
        # 最后一个保留
        last_user = [m for m in result if m["role"] == "user"][-1]
        assert "result 14" in last_user["content"][0]["content"]

    def test_preserves_non_tool_content(self):
        messages = [
            {"role": "user", "content": "plain text"},
            {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
        ]
        result = micro_compact(messages)
        assert result[0]["content"] == "plain text"


class TestFindSplitIndex:
    def test_basic(self):
        messages = [
            {"role": "user", "content": "a" * 1000},
            {"role": "user", "content": "b" * 1000},
            {"role": "user", "content": "c" * 100},
        ]
        idx = _find_split_index(messages, 500)
        assert idx <= len(messages)

    def test_all_within_budget(self):
        messages = [
            {"role": "user", "content": "short"},
        ]
        idx = _find_split_index(messages, 10000)
        assert idx == 0


class TestExtractSummary:
    def test_with_tags(self):
        text = "analysis here\n<summary>\nkey points\n</summary>\nmore text"
        assert _extract_summary(text) == "key points"

    def test_without_tags(self):
        text = "just a plain summary"
        assert _extract_summary(text) == "just a plain summary"


class TestMessagesToText:
    def test_basic(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
            ]},
        ]
        text = _messages_to_text(messages)
        assert "[user]: hello" in text
        assert "[assistant]:" in text
        assert "thinking" in text
        assert "Tool call: bash" in text


class TestAutoCompactIfNeeded:
    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """未超阈值不压缩。"""
        from termpilot.compact import auto_compact_if_needed
        messages = [{"role": "user", "content": "short"}]
        result = await auto_compact_if_needed(
            messages, "", None, "model",
            context_window=200_000,
        )
        assert result == messages

    @pytest.mark.asyncio
    async def test_force(self):
        """force=True 但消息很少时触发 micro_compact（可能无变化）。"""
        from termpilot.compact import auto_compact_if_needed
        messages = [{"role": "user", "content": "short"}]
        result = await auto_compact_if_needed(
            messages, "", None, "model",
            context_window=200_000, force=True,
        )
        # 短消息经过 micro_compact 后不变
        assert len(result) >= 1
