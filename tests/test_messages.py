"""messages.py 测试。"""

import pytest


class TestCreateUserMessage:
    def test_string_content(self):
        from cc_python.messages import create_user_message
        msg = create_user_message("hello")
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_none_content(self):
        from cc_python.messages import create_user_message
        msg = create_user_message(None)
        assert msg["content"] == "(empty message)"

    def test_empty_string(self):
        from cc_python.messages import create_user_message
        msg = create_user_message("")
        assert msg["content"] == "(empty message)"

    def test_list_content(self):
        from cc_python.messages import create_user_message
        blocks = [{"type": "text", "text": "hello"}]
        msg = create_user_message(blocks)
        assert msg["content"] == blocks

    def test_tool_results_priority(self):
        from cc_python.messages import create_user_message
        results = [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]
        msg = create_user_message(content="ignored", tool_results=results)
        assert msg["content"] == results


class TestCreateAssistantMessage:
    def test_simple(self):
        from cc_python.messages import create_assistant_message
        msg = create_assistant_message("response text")
        assert msg["role"] == "assistant"
        assert msg["content"] == "response text"


class TestCreateToolUseAssistantMessage:
    def test_with_text(self):
        from cc_python.messages import create_tool_use_assistant_message
        blocks = [{"type": "tool_use", "id": "1", "name": "bash", "input": {"command": "ls"}}]
        msg = create_tool_use_assistant_message("running ls", blocks)
        assert msg["role"] == "assistant"
        content = msg["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"

    def test_no_text(self):
        from cc_python.messages import create_tool_use_assistant_message
        blocks = [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}]
        msg = create_tool_use_assistant_message("", blocks)
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "tool_use"


class TestCreateToolResultMessage:
    def test_basic(self):
        from cc_python.messages import create_tool_result_message
        results = [{"type": "tool_result", "tool_use_id": "1", "content": "file content"}]
        msg = create_tool_result_message(results)
        assert msg["role"] == "user"
        assert msg["content"] == results


class TestNormalizeMessages:
    def test_empty(self):
        from cc_python.messages import normalize_messages_for_api
        assert normalize_messages_for_api([]) == []

    def test_removes_system(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_removes_empty_content(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "user", "content": ""},
            {"role": "user", "content": None},
            {"role": "assistant", "content": "hi"},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 1

    def test_merges_same_role_string(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 1
        assert "hello" in result[0]["content"]
        assert "world" in result[0]["content"]

    def test_merges_same_role_list(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 1
        assert len(result[0]["content"]) == 2

    def test_merges_string_and_list(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "assistant", "content": "text"},
            {"role": "assistant", "content": [{"type": "text", "text": "block"}]},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2

    def test_preserves_alternating(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
        ]
        result = normalize_messages_for_api(msgs)
        assert len(result) == 3

    def test_does_not_mutate_original(self):
        from cc_python.messages import normalize_messages_for_api
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "world"},
        ]
        normalize_messages_for_api(msgs)
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["content"] == "world"


class TestMessagesToText:
    def test_basic(self):
        from cc_python.messages import messages_to_text
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        text = messages_to_text(msgs)
        assert "[user]: hello" in text
        assert "[assistant]: hi there" in text

    def test_list_content(self):
        from cc_python.messages import messages_to_text
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
            ]},
        ]
        text = messages_to_text(msgs)
        assert "thinking" in text
        assert "Tool call: bash" in text
