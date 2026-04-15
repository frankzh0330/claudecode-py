"""工具结果磁盘存储。

对应 TS: utils/toolResultStorage.ts（1040 行）
Python 简化版保留核心：大型工具结果持久化到磁盘，上下文中只保留引用 + 预览。

解决的问题：
- 大型工具结果（如 grep 匹配几千行）会快速消耗上下文窗口
- 将完整结果写入文件，上下文中保留前 N 字符预览 + 文件路径引用
- 模型需要时可以通过 read_file 工具查看完整内容
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 预览大小限制（字符数）
PREVIEW_SIZE = 2000

# 持久化阈值：超过此大小的结果写入磁盘
PERSIST_THRESHOLD = 50_000  # 50K 字符

# 持久化结果的引用标签
PERSISTED_TAG = "<persisted-output>"
PERSISTED_CLOSING_TAG = "</persisted-output>"

# 清空消息（用于 micro-compact 替换旧结果）
CLEARED_MESSAGE = "[Old tool result content cleared]"


def _get_storage_dir() -> Path:
    """获取工具结果存储目录。

    对应 TS getToolResultsDir()。
    使用会话临时目录下的 tool-results 子目录。
    """
    # 优先使用项目目录下的 .claude/tool-results
    storage_dir = Path.cwd() / ".claude" / "tool-results"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _get_result_path(tool_use_id: str) -> Path:
    """获取工具结果文件路径。"""
    # 清理 id 中的特殊字符
    safe_id = tool_use_id.replace("/", "_").replace("\\", "_")
    return _get_storage_dir() / f"{safe_id}.txt"


def should_persist(content: str) -> bool:
    """判断工具结果是否需要持久化。"""
    return len(content) > PERSIST_THRESHOLD


def persist_tool_result(content: str, tool_use_id: str) -> dict[str, Any]:
    """将大型工具结果持久化到磁盘。

    对应 TS persistToolResult()。

    返回包含文件路径和预览的信息。
    """
    filepath = _get_result_path(tool_use_id)

    # 避免重复写入
    if not filepath.exists():
        try:
            filepath.write_text(content, encoding="utf-8")
            logger.debug("Persisted tool result to %s (%d chars)", filepath, len(content))
        except OSError as e:
            logger.warning("Failed to persist tool result: %s", e)

    # 生成预览
    preview = content[:PREVIEW_SIZE]
    has_more = len(content) > PREVIEW_SIZE

    return {
        "filepath": str(filepath),
        "original_size": len(content),
        "preview": preview,
        "has_more": has_more,
    }


def build_large_result_message(
    tool_use_id: str,
    content: str,
    tool_name: str = "",
) -> str:
    """为大型工具结果构建包含持久化引用的消息。

    对应 TS buildLargeToolResultMessage()。

    在上下文中只保留预览 + 文件引用，完整内容在磁盘文件中。
    """
    info = persist_tool_result(content, tool_use_id)

    preview = info["preview"]
    if info["has_more"]:
        preview += f"\n... ({info['original_size'] - PREVIEW_SIZE} more characters)"

    return (
        f"{preview}\n\n"
        f"{PERSISTED_TAG}\n"
        f"Full output saved to: {info['filepath']}\n"
        f"Use read_file to view the complete result.\n"
        f"{PERSISTED_CLOSING_TAG}"
    )


def truncate_tool_result(content: str, max_chars: int = 10_000) -> str:
    """截断过长的工具结果。

    对应 TS 中的工具结果截断逻辑。
    当不需要完整持久化但结果仍然很长时使用。
    """
    if len(content) <= max_chars:
        return content

    return content[:max_chars] + f"\n\n... (truncated {len(content) - max_chars} characters)"


def process_tool_result(content: str, tool_use_id: str, tool_name: str = "") -> str:
    """处理工具结果：大结果持久化，长结果截断。

    这是工具结果处理的主入口。
    """
    if should_persist(content):
        return build_large_result_message(tool_use_id, content, tool_name)

    return truncate_tool_result(content)


def cleanup_storage() -> None:
    """清理存储目录中的旧文件。"""
    storage_dir = _get_storage_dir()
    if not storage_dir.exists():
        return

    for f in storage_dir.glob("*.txt"):
        try:
            f.unlink()
        except OSError:
            pass
