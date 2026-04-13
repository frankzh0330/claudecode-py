"""会话持久化。

对应 TS:
- utils/sessionStorage.ts — 核心存储（JSONL 读写、写入队列、会话管理）
- utils/conversationRecovery.ts — 恢复加载
- types/logs.ts — Entry 类型定义

存储格式：JSONL（每行一个 JSON 对象，追加写入）
存储路径：~/.claude/projects/<sanitized-path>/<session-id>.jsonl

每条消息通过 parentUuid 形成链表结构，支持分叉和恢复。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_config_home() -> Path:
    """对应 TS envUtils.ts getClaudeConfigHomeDir()。"""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))


def _sanitize_path(path: str) -> str:
    """对应 TS sessionStoragePortable.ts sanitizePath()。

    将路径中的非字母数字字符替换为 -。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path).strip("-")


def _get_projects_dir() -> Path:
    """获取 projects 目录。"""
    return _get_config_home() / "projects"


def get_project_dir(cwd: str | None = None) -> Path:
    """对应 TS sessionStorage.ts getProjectDir()。

    返回 ~/.claude/projects/<sanitized-cwd>/
    """
    work_dir = cwd or str(Path.cwd())
    return _get_projects_dir() / _sanitize_path(work_dir)


def _datetime_now() -> str:
    """ISO 8601 格式的当前时间。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Entry 类型 — 对应 TS types/logs.ts
# ---------------------------------------------------------------------------

def make_transcript_entry(
    role: str,
    content: Any,
    parent_uuid: str | None,
    session_id: str,
    cwd: str | None = None,
) -> dict[str, Any]:
    """创建一条 transcript entry。

    对应 TS types/logs.ts TranscriptMessage。

    链表结构：每条消息通过 parentUuid 指向上一条。
    """
    entry_uuid = str(uuid.uuid4())
    return {
        "type": "transcript",
        "uuid": entry_uuid,
        "parentUuid": parent_uuid,
        "sessionId": session_id,
        "timestamp": _datetime_now(),
        "cwd": cwd or str(Path.cwd()),
        "message": {
            "role": role,
            "content": content,
        },
    }


def make_metadata_entry(
    entry_type: str,
    value: Any,
    session_id: str,
) -> dict[str, Any]:
    """创建一条 metadata entry（如 summary, custom-title, tag 等）。

    对应 TS types/logs.ts 中的非 transcript entry 类型。
    """
    return {
        "type": entry_type,
        "sessionId": session_id,
        "timestamp": _datetime_now(),
        "value": value,
    }


# ---------------------------------------------------------------------------
# SessionStorage — 对应 TS sessionStorage.ts Project 类
# ---------------------------------------------------------------------------

class SessionStorage:
    """管理单个项目的会话存储。

    对应 TS sessionStorage.ts 的 Project 类，大幅简化：
    - TS 版有 100ms 写入队列（drainWriteQueue）、lazy file creation 等
    - Python 简化版直接同步追加写入（对于 CLI 场景足够）
    """

    def __init__(self, cwd: str | None = None) -> None:
        self._project_dir = get_project_dir(cwd)
        self._session_id: str | None = None
        self._file_path: Path | None = None
        self._last_uuid: str | None = None  # 链表尾节点，用于 parentUuid

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def start_session(self, session_id: str | None = None) -> str:
        """开始新会话。

        Args:
            session_id: 指定 session ID（用于 resume），None 则新建。
        """
        self._session_id = session_id or str(uuid.uuid4())
        self._file_path = self._project_dir / f"{self._session_id}.jsonl"
        self._last_uuid = None
        logger.debug("session started: %s (file=%s)", self._session_id[:8], self._file_path)
        return self._session_id

    def _ensure_dir(self) -> None:
        """确保目录存在。"""
        if self._file_path:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def _append_line(self, entry: dict[str, Any]) -> None:
        """追加一行 JSONL。对应 TS appendToFile()。"""
        if not self._file_path:
            return
        self._ensure_dir()
        line = json.dumps(entry, ensure_ascii=False)
        with open(self._file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def record_user_message(self, content: str) -> None:
        """记录用户消息。"""
        if not self._session_id:
            return
        entry = make_transcript_entry(
            role="user",
            content=content,
            parent_uuid=self._last_uuid,
            session_id=self._session_id,
        )
        self._append_line(entry)
        self._last_uuid = entry["uuid"]

    def record_assistant_message(self, content: str) -> None:
        """记录助手消息。"""
        if not self._session_id:
            return
        entry = make_transcript_entry(
            role="assistant",
            content=content,
            parent_uuid=self._last_uuid,
            session_id=self._session_id,
        )
        self._append_line(entry)
        self._last_uuid = entry["uuid"]

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        tool_result: str,
    ) -> None:
        """记录工具调用（assistant tool_use + user tool_result 两条）。"""
        if not self._session_id:
            return
        # tool_use entry（作为 assistant 消息的一部分）
        tool_use_entry = make_transcript_entry(
            role="assistant",
            content=[{
                "type": "tool_use",
                "name": tool_name,
                "input": tool_input,
            }],
            parent_uuid=self._last_uuid,
            session_id=self._session_id,
        )
        self._append_line(tool_use_entry)
        self._last_uuid = tool_use_entry["uuid"]

        # tool_result entry
        tool_result_entry = make_transcript_entry(
            role="user",
            content=[{
                "type": "tool_result",
                "content": tool_result,
            }],
            parent_uuid=self._last_uuid,
            session_id=self._session_id,
        )
        self._append_line(tool_result_entry)
        self._last_uuid = tool_result_entry["uuid"]

    def save_metadata(self, entry_type: str, value: Any) -> None:
        """保存 metadata entry（summary, title, tag 等）。"""
        if not self._session_id:
            return
        entry = make_metadata_entry(entry_type, value, self._session_id)
        self._append_line(entry)


# ---------------------------------------------------------------------------
# 会话加载 & 列表 — 对应 TS conversationRecovery.ts + sessionStorage.ts
# ---------------------------------------------------------------------------

def _parse_jsonl(file_path: Path) -> list[dict[str, Any]]:
    """解析 JSONL 文件，返回所有 entry。"""
    entries = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return entries


def _extract_metadata(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """从 entries 中提取 metadata。

    对应 TS sessionStorage.ts readLiteMetadata() 的简化版。
    """
    meta: dict[str, Any] = {
        "session_id": "",
        "first_prompt": "",
        "timestamp": "",
        "message_count": 0,
        "title": "",
    }
    for entry in entries:
        if entry.get("type") == "transcript":
            meta["message_count"] += 1
            msg = entry.get("message", {})
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                if not meta["first_prompt"]:
                    meta["first_prompt"] = msg["content"][:100]
                if not meta["session_id"]:
                    meta["session_id"] = entry.get("sessionId", "")
            if not meta["timestamp"]:
                meta["timestamp"] = entry.get("timestamp", "")
        elif entry.get("type") == "custom-title":
            meta["title"] = entry.get("value", "")
    # 最后更新 timestamp 为最后一条 entry 的时间
    if entries:
        meta["last_timestamp"] = entries[-1].get("timestamp", meta["timestamp"])
    return meta


def list_sessions(cwd: str | None = None) -> list[dict[str, Any]]:
    """列出当前项目的所有会话。

    对应 TS sessionStorage.ts 中列出历史会话的逻辑。

    返回列表按最后修改时间倒序（最近的在前）。
    """
    project_dir = get_project_dir(cwd)
    if not project_dir.exists():
        return []

    sessions = []
    for jsonl_file in sorted(
        project_dir.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    ):
        entries = _parse_jsonl(jsonl_file)
        meta = _extract_metadata(entries)
        meta["file_path"] = str(jsonl_file)
        sessions.append(meta)

    return sessions


def load_session(session_id: str, cwd: str | None = None) -> list[dict[str, Any]]:
    """加载指定会话的所有消息，返回 API 格式的消息列表。

    对应 TS conversationRecovery.ts loadConversationForResume()。

    TS 版用 parentUuid 链回溯（buildConversationChain），支持分叉。
    Python 简化版：JSONL 本身按时间追加，直接顺序读取即可。
    如果后续需要支持分叉，再改为链回溯。

    返回格式：[{"role": "user/assistant", "content": "..."}, ...]
    可直接用于 query_with_tools() 的 messages 参数。
    """
    project_dir = get_project_dir(cwd)
    file_path = project_dir / f"{session_id}.jsonl"
    entries = _parse_jsonl(file_path)
    logger.debug("load_session: %s → %d entries from %s", session_id[:8], len(entries), file_path)

    # 从 entries 中提取 transcript，按写入顺序（即时间顺序）
    messages = []
    for entry in entries:
        if entry.get("type") != "transcript":
            continue
        msg = entry.get("message", {})
        role = msg.get("role")
        content = msg.get("content")
        if role and content:
            messages.append({"role": role, "content": content})

    return messages


# ---------------------------------------------------------------------------
# Session Title 生成 — 对应 TS utils/sessionTitle.ts
# ---------------------------------------------------------------------------

_TITLE_PROMPT = """\
Generate a very short (3-7 words) title for this conversation.
Use sentence case. No punctuation. No quotes.

Conversation:
{conversation_text}

Title:"""


def _extract_conversation_text(messages: list[dict[str, Any]], max_chars: int = 1000) -> str:
    """从消息列表中提取最近 max_chars 字符的对话文本。"""
    parts = []
    total = 0
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # content blocks 格式，提取 text 部分
            text_parts = [
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = " ".join(text_parts)
        if not isinstance(content, str):
            continue
        line = f"{role}: {content}"
        parts.append(line)
        total += len(line)
        if total >= max_chars:
            break
    parts.reverse()
    return "\n".join(parts)[:max_chars]


async def generate_session_title(
    messages: list[dict[str, Any]],
    client: Any,
    client_format: str,
    model: str,
) -> str:
    """从对话内容生成简短标题。

    对应 TS sessionTitle.ts：
    - 提取最近 1000 字符对话文本
    - 用 prompt 要求 3-7 词 sentence-case 标题
    - 发送一次性 API 调用（无工具，max_tokens=50）
    """
    if not messages:
        return ""

    text = _extract_conversation_text(messages)
    if not text.strip():
        return ""

    prompt = _TITLE_PROMPT.format(conversation_text=text)

    try:
        if client_format == "anthropic":
            response = await client.messages.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            title = response.content[0].text.strip() if response.content else ""
        else:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=50,
                messages=[{"role": "user", "content": prompt}],
            )
            title = response.choices[0].message.content.strip() if response.choices else ""

        # 清理标题：去除引号、多余空格
        title = title.strip('"\'').strip()
        if len(title) > 80:
            title = title[:80]
        return title

    except Exception as e:
        logger.debug("generate_session_title failed: %s", e)
        return ""
