"""session.py 测试。"""

import json
from pathlib import Path

import pytest

from cc_python.session import (
    _sanitize_path, make_transcript_entry, make_metadata_entry,
    SessionStorage, list_sessions, load_session,
)


class TestSanitizePath:
    def test_replaces_special_chars(self):
        result = _sanitize_path("/Users/frank/project")
        assert "/" not in result
        assert result.startswith("Users")

    def test_strips_dashes(self):
        result = _sanitize_path("///")
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestMakeTranscriptEntry:
    def test_structure(self):
        entry = make_transcript_entry(
            role="user",
            content="hello",
            parent_uuid=None,
            session_id="sess-123",
        )
        assert entry["type"] == "transcript"
        assert entry["parentUuid"] is None
        assert entry["sessionId"] == "sess-123"
        assert entry["message"]["role"] == "user"
        assert entry["message"]["content"] == "hello"
        assert "uuid" in entry
        assert "timestamp" in entry

    def test_with_parent(self):
        entry = make_transcript_entry("assistant", "hi", "parent-uuid", "sess-1")
        assert entry["parentUuid"] == "parent-uuid"


class TestMakeMetadataEntry:
    def test_structure(self):
        entry = make_metadata_entry("summary", "test summary", "sess-1")
        assert entry["type"] == "summary"
        assert entry["value"] == "test summary"
        assert entry["sessionId"] == "sess-1"


class TestSessionStorage:
    def test_start_new(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        monkeypatch.setattr("cc_python.session.Path.cwd", lambda: tmp_path / "project")

        storage = SessionStorage(cwd=str(tmp_path / "project"))
        sid = storage.start_session()
        assert sid is not None
        assert storage.session_id == sid

    def test_start_resume(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)

        storage = SessionStorage(cwd=str(tmp_path / "project"))
        sid = storage.start_session("fixed-session-id")
        assert sid == "fixed-session-id"

    def test_record_messages(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)

        storage = SessionStorage(cwd=str(tmp_path / "project"))
        storage.start_session("test-sess")

        storage.record_user_message("hello")
        storage.record_assistant_message("hi there")

        # 验证 JSONL 文件
        file_path = tmp_path / "projects" / _sanitize_path(str(tmp_path / "project")) / "test-sess.jsonl"
        assert file_path.exists()
        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        assert entry1["message"]["role"] == "user"
        assert entry1["message"]["content"] == "hello"

        entry2 = json.loads(lines[1])
        assert entry2["message"]["role"] == "assistant"

    def test_record_tool_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)

        storage = SessionStorage(cwd=str(tmp_path / "project"))
        storage.start_session("test-sess")

        storage.record_tool_call("bash", {"command": "ls"}, "file1.py\nfile2.py")

        file_path = tmp_path / "projects" / _sanitize_path(str(tmp_path / "project")) / "test-sess.jsonl"
        lines = file_path.read_text().strip().split("\n")
        assert len(lines) == 2

        tool_use = json.loads(lines[0])
        assert tool_use["message"]["content"][0]["type"] == "tool_use"

        tool_result = json.loads(lines[1])
        assert tool_result["message"]["content"][0]["type"] == "tool_result"

    def test_parent_uuid_chain(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)

        storage = SessionStorage(cwd=str(tmp_path / "project"))
        storage.start_session("test-sess")

        storage.record_user_message("first")
        uuid1 = storage._last_uuid
        storage.record_assistant_message("second")
        uuid2 = storage._last_uuid

        file_path = tmp_path / "projects" / _sanitize_path(str(tmp_path / "project")) / "test-sess.jsonl"
        lines = file_path.read_text().strip().split("\n")
        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])

        assert entry1["parentUuid"] is None
        assert entry2["parentUuid"] == uuid1

    def test_no_session_id_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        storage = SessionStorage(cwd=str(tmp_path / "project"))
        # 没有 start_session，record 应该是无操作
        storage.record_user_message("hello")
        # 不应创建文件


class TestListSessions:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        monkeypatch.setattr("cc_python.session.Path.cwd", lambda: tmp_path / "project")
        assert list_sessions(cwd=str(tmp_path / "project")) == []

    def test_with_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        cwd = str(tmp_path / "project")

        storage = SessionStorage(cwd=cwd)
        storage.start_session("sess-1")
        storage.record_user_message("first prompt")

        sessions = list_sessions(cwd=cwd)
        assert len(sessions) == 1
        assert sessions[0]["first_prompt"] == "first prompt"


class TestLoadSession:
    def test_returns_messages(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        cwd = str(tmp_path / "project")

        storage = SessionStorage(cwd=cwd)
        storage.start_session("sess-1")
        storage.record_user_message("hello")
        storage.record_assistant_message("hi")

        messages = load_session("sess-1", cwd=cwd)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_skips_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.session._get_config_home", lambda: tmp_path)
        cwd = str(tmp_path / "project")

        storage = SessionStorage(cwd=cwd)
        storage.start_session("sess-1")
        storage.record_user_message("hello")
        storage.save_metadata("summary", "test summary")

        messages = load_session("sess-1", cwd=cwd)
        # metadata entry 被跳过
        assert len(messages) == 1
