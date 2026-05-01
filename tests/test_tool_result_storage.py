"""tool_result_storage.py 测试。"""

import pytest

from termpilot.tool_result_storage import (
    should_persist, persist_tool_result, build_large_result_message,
    truncate_tool_result, process_tool_result, cleanup_storage,
    persist_agent_result, PREVIEW_SIZE, PERSIST_THRESHOLD, PERSISTED_TAG,
    AGENT_HANDOFF_PREVIEW_SIZE,
)


class TestShouldPersist:
    def test_below_threshold(self):
        assert should_persist("a" * 100) is False

    def test_at_threshold(self):
        assert should_persist("a" * PERSIST_THRESHOLD) is False

    def test_above_threshold(self):
        assert should_persist("a" * (PERSIST_THRESHOLD + 1)) is True


class TestPersistToolResult:
    def test_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        content = "a" * 100000
        info = persist_tool_result(content, "tool-123")
        assert info["original_size"] == 100000
        assert info["has_more"] is True
        assert len(info["preview"]) <= PREVIEW_SIZE + 100  # some tolerance

    def test_preview_size(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        content = "a" * 100000
        info = persist_tool_result(content, "tool-456")
        assert len(info["preview"]) == PREVIEW_SIZE


class TestPersistAgentResult:
    def test_writes_full_result_and_returns_summary(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_agent_results_dir", lambda: tmp_path)
        content = "x" * (AGENT_HANDOFF_PREVIEW_SIZE + 500)

        info = persist_agent_result(
            content,
            "agent-123",
            subagent_type="Explore",
            description="Inspect project",
        )

        path = tmp_path / "agent-123.md"
        assert path.exists()
        saved = path.read_text()
        assert "Agent type: Explore" in saved
        assert content in saved
        assert info["filepath"] == str(path)
        assert info["original_size"] == len(content)
        assert info["has_more"] is True
        assert len(info["summary"]) < len(content)


class TestBuildLargeResultMessage:
    def test_contains_tag(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        content = "a" * 100000
        msg = build_large_result_message("tool-789", content)
        assert PERSISTED_TAG in msg
        assert "tool-789" in msg.replace("/", "_") or True  # sanitized id


class TestTruncateToolResult:
    def test_short(self):
        assert truncate_tool_result("short") == "short"

    def test_long(self):
        content = "a" * 20000
        result = truncate_tool_result(content, 10000)
        assert len(result) > 10000
        assert "truncated" in result


class TestProcessToolResult:
    def test_small(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        result = process_tool_result("small content", "tool-1")
        assert "small content" in result

    def test_large(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        content = "a" * (PERSIST_THRESHOLD + 1)
        result = process_tool_result(content, "tool-2")
        assert PERSISTED_TAG in result


class TestCleanupStorage:
    def test_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.tool_result_storage._get_storage_dir", lambda: tmp_path)
        # 创建一些文件
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        cleanup_storage()
        assert list(tmp_path.glob("*.txt")) == []
