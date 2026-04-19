"""会话持久化。

对应 TS:
- utils/sessionStorage.ts — 核心存储（JSONL 读写、写入队列、会话管理）
- utils/conversationRecovery.ts — 恢复加载
- types/logs.ts — Entry 类型定义

存储格式：JSONL（每行一个 JSON 对象，追加写入）
存储路径：~/.termpilot/projects/<sanitized-path>/<session-id>.jsonl

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

from termpilot.config import get_config_home

logger = logging.getLogger(__name__)


def _sanitize_path(path: str) -> str:
    """对应 TS sessionStoragePortable.ts sanitizePath()。

    将路径中的非字母数字字符替换为 -。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path).strip("-")


def _get_projects_dir() -> Path:
    """获取 projects 目录。"""
    return get_config_home() / "projects"


def get_project_dir(cwd: str | None = None) -> Path:
    """对应 TS sessionStorage.ts getProjectDir()。

    返回 ~/.termpilot/projects/<sanitized-cwd>/
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

    使用 parentUuid 链回溯（buildConversationChain），支持分叉会话。
    找到最新的叶节点，沿 parentUuid 回溯到根，再反转得到完整链。
    同时恢复被链回溯遗漏的并行工具结果。

    返回格式：[{"role": "user/assistant", "content": "..."}, ...]
    可直接用于 query_with_tools() 的 messages 参数。
    """
    project_dir = get_project_dir(cwd)
    file_path = project_dir / f"{session_id}.jsonl"
    entries = _parse_jsonl(file_path)
    logger.debug("load_session: %s → %d entries from %s", session_id[:8], len(entries), file_path)

    if not entries:
        return []

    # 过滤 transcript entries
    transcript_entries = [e for e in entries if e.get("type") == "transcript"]

    if not transcript_entries:
        return []

    # 构建链
    chain = _build_conversation_chain(transcript_entries)

    # 转换为 API 消息格式
    messages = []
    for entry in chain:
        msg = entry.get("message", {})
        role = msg.get("role")
        content = msg.get("content")
        if role and content:
            messages.append({"role": role, "content": content})

    return messages


def _build_conversation_chain(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """通过 parentUuid 链回溯构建对话链。

    对应 TS conversationRecovery.ts buildConversationChain()。

    算法：
    1. 构建 uuid → entry 的映射
    2. 找到最新的叶节点（没有子节点的 entry）
    3. 从叶节点沿 parentUuid 回溯到根
    4. 反转得到时间正序的对话链
    5. 恢复并行工具结果中被链回溯遗漏的孤儿 entry
    """
    if not entries:
        return []

    # 1. 构建 uuid → entry 映射
    uuid_map: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_uuid = entry.get("uuid")
        if entry_uuid:
            uuid_map[entry_uuid] = entry

    if not uuid_map:
        # 没有 uuid 的旧格式 — fallback 到顺序读取
        return list(entries)

    # 2. 找到叶节点：uuid 不被任何其他 entry 的 parentUuid 引用
    child_uuids: set[str] = set()
    for entry in entries:
        parent = entry.get("parentUuid")
        if parent and parent in uuid_map:
            child_uuids.add(parent)

    # 候选叶节点 = 不在任何 parentUuid 指向中的 entry
    leaf_candidates = [
        uuid for uuid in uuid_map
        if uuid not in child_uuids
    ]

    if not leaf_candidates:
        # 所有人都被引用了（可能是循环）— fallback 顺序读取
        logger.debug("no leaf candidates found, falling back to sequential read")
        return list(entries)

    # 选择最新的叶节点（在 entries 中出现最晚的）
    entry_order = {e.get("uuid"): i for i, e in enumerate(entries) if e.get("uuid")}
    leaf_candidates.sort(key=lambda u: entry_order.get(u, 0), reverse=True)
    leaf_uuid = leaf_candidates[0]

    # 3. 从叶节点沿 parentUuid 回溯到根
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current_uuid: str | None = leaf_uuid

    while current_uuid and current_uuid in uuid_map:
        if current_uuid in seen:
            # 循环检测
            logger.debug("cycle detected at %s, breaking chain", current_uuid[:8])
            break
        seen.add(current_uuid)
        entry = uuid_map[current_uuid]
        chain.append(entry)
        current_uuid = entry.get("parentUuid")

    # 4. 反转得到时间正序
    chain.reverse()

    # 5. 恢复孤儿并行工具结果
    chain = _recover_orphaned_entries(entries, chain, seen)

    logger.debug("chain built: %d entries from %d total (leaf=%s)",
                 len(chain), len(entries), leaf_uuid[:8])
    return chain


def _recover_orphaned_entries(
        all_entries: list[dict[str, Any]],
        chain: list[dict[str, Any]],
        chain_uuids: set[str],
) -> list[dict[str, Any]]:
    """恢复被链回溯遗漏的孤儿 entry。

    对应 TS recoverOrphanedParallelToolResults()。

    场景：并行工具调用时，多个 tool_result entry 指向同一个 parentUuid
    （即同一个 assistant tool_use 消息）。链回溯只会沿着一条路径走，
    其他并行的 tool_result 会被遗漏。

    算法：
    1. 找到所有不在 chain 中的 transcript entry
    2. 检查它们的 parentUuid 是否在 chain 中
    3. 如果是，将其插入到 parentUuid 对应 entry 的后面
    """
    if not chain:
        return chain

    # 没有孤儿则快速返回
    orphans = [e for e in all_entries if e.get("uuid") not in chain_uuids]
    if not orphans:
        return chain

    # 建立 chain 中每个 uuid → 索引位置的映射
    chain_index: dict[str, int] = {}
    for i, entry in enumerate(chain):
        uuid = entry.get("uuid")
        if uuid:
            chain_index[uuid] = i

    # 找到有意义的孤儿：parentUuid 在 chain 中，且是工具结果类型的消息
    # 只恢复并行工具结果，不恢复对话分支（分支节点应该通过链选择）
    insertions: list[tuple[int, dict[str, Any]]] = []  # (insert_after_index, entry)
    for orphan in orphans:
        parent = orphan.get("parentUuid")
        if parent and parent in chain_index:
            # 只恢复 user 角色且内容是 tool_result 类型的 entry
            # （并行工具调用产生的多个 tool_result 共享同一个 parentUuid）
            msg = orphan.get("message", {})
            content = msg.get("content")
            is_tool_result = (
                    msg.get("role") == "user"
                    and isinstance(content, list)
                    and any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
            )
            if is_tool_result:
                insertions.append((chain_index[parent], orphan))

    if not insertions:
        return chain

    # 按 index 排序，从后往前插入避免索引偏移
    insertions.sort(key=lambda x: x[0], reverse=True)

    result = list(chain)
    for idx, orphan in insertions:
        result.insert(idx + 1, orphan)

    logger.debug("recovered %d orphaned entries", len(insertions))
    return result


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
        model: str,
        client_format: str = "openai",
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
            title = ""
            for block in response.content:
                if hasattr(block, "text"):
                    title = block.text.strip()
                    break
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
