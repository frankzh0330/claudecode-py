"""Undo/回退系统 — 文件修改前的快照保存与恢复。

对应 TS: utils/diff.ts（~5K 行，TS 版有完整的 patch/diff 系统）
Python 版简化为内存栈式快照：write_file/edit_file 修改前自动压栈，
/undo 命令弹出最近的快照并恢复文件内容。

设计选择（内存 vs 磁盘）：
- 快照保存在内存中（不持久化），重启后清空
- 栈上限 50 个快照，防止内存膨胀
- 每个快照记录文件路径和修改前的完整内容
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 全局快照栈
_undo_stack: list[dict[str, Any]] = []
_MAX_SNAPSHOTS = 50


def save_snapshot(file_path: str) -> None:
    """修改前调用：读取当前文件内容压栈。

    文件存在则保存其内容，不存在则保存 None（表示文件是新建的）。
    """
    path = Path(file_path).expanduser()
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("save_snapshot: cannot read %s: %s", file_path, e)
            content = None
    else:
        content = None  # 文件不存在（新建场景）

    snapshot = {
        "path": str(path),
        "content": content,
    }
    _undo_stack.append(snapshot)
    logger.debug("snapshot saved: %s (exists=%s, content_len=%s)",
                 file_path, path.exists(),
                 len(content) if content is not None else "N/A")

    # 防止内存膨胀
    if len(_undo_stack) > _MAX_SNAPSHOTS:
        removed = _undo_stack.pop(0)
        logger.debug("snapshot evicted (stack full): %s", removed["path"])


def pop_snapshot() -> dict[str, Any] | None:
    """弹出最近的快照。返回 {"path": ..., "content": ...} 或 None。"""
    if not _undo_stack:
        return None
    return _undo_stack.pop()


def has_snapshots() -> bool:
    """是否有可回退的快照。"""
    return len(_undo_stack) > 0


def get_snapshot_count() -> int:
    """当前快照栈深度。"""
    return len(_undo_stack)


def clear_snapshots() -> None:
    """清空快照栈。"""
    _undo_stack.clear()
