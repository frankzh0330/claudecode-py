"""优先级命令队列。

参考 TS messageQueueManager.ts，使用 asyncio.PriorityQueue 实现。
支持三级优先级（NOW > NEXT > LATER）、批量合并、阻塞/非阻塞出队。

用途：
- REPL 主循环解耦输入收集和命令处理
- 子 agent 异步完成后通过 enqueue 通知主循环
- TaskListWatcher 通过 LATER 优先级注入任务
"""

from __future__ import annotations

import asyncio
import heapq
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """命令优先级，值越小越优先。"""
    NOW = 0   # 中断当前操作（最高）
    NEXT = 1  # 普通用户输入
    LATER = 2  # task notification、系统消息


@dataclass
class QueuedCommand:
    """队列中的命令。"""
    mode: str          # "prompt" | "slash_command" | "task_notification" | "system"
    value: Any         # str | dict（消息内容）
    priority: Priority = Priority.NEXT
    origin: str = ""   # "user" | "agent" | "system" | "task-watcher"
    agent_id: str = ""  # 目标 agent ID；空字符串表示主线程
    uuid: str = ""     # 唯一标识


QueueFilter = Callable[[QueuedCommand], bool]


class MessageQueue:
    """异步优先级命令队列。

    内部使用 asyncio.PriorityQueue，按 (priority, sequence) 排序，
    同优先级 FIFO。支持阻塞/非阻塞出队和批量合并判断。
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, int, QueuedCommand]] = asyncio.PriorityQueue()
        self._counter = 0

    def enqueue(self, command: QueuedCommand) -> None:
        """入队，立即返回。"""
        self._counter += 1
        self._queue.put_nowait((command.priority, self._counter, command))
        logger.debug("enqueue: mode=%s priority=%s origin=%s",
                     command.mode, command.priority.name, command.origin)

    def _find_best_index(self, filter_fn: QueueFilter | None = None) -> int:
        """查找满足 filter 的最高优先级命令索引，不修改队列。"""
        best_idx = -1
        best_key: tuple[int, int] | None = None
        for idx, (priority, sequence, cmd) in enumerate(self._queue._queue):  # type: ignore[attr-defined]
            if filter_fn and not filter_fn(cmd):
                continue
            key = (priority, sequence)
            if best_key is None or key < best_key:
                best_key = key
                best_idx = idx
        return best_idx

    async def dequeue(
        self,
        timeout: float | None = None,
        filter_fn: QueueFilter | None = None,
    ) -> QueuedCommand | None:
        """阻塞等待并返回最高优先级命令。timeout=None 无限等待。

        filter_fn 存在时，只取匹配命令；不匹配命令保留在队列中。
        """
        if filter_fn is not None:
            start = asyncio.get_running_loop().time()
            while True:
                cmd = self.dequeue_nowait(filter_fn=filter_fn)
                if cmd is not None:
                    return cmd
                if timeout is not None and asyncio.get_running_loop().time() - start >= timeout:
                    return None
                await asyncio.sleep(0.05)

        try:
            if timeout is not None:
                _, _, cmd = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                _, _, cmd = await self._queue.get()
            logger.debug("dequeue: mode=%s priority=%s", cmd.mode, cmd.priority.name)
            return cmd
        except asyncio.TimeoutError:
            return None

    def dequeue_nowait(self, filter_fn: QueueFilter | None = None) -> QueuedCommand | None:
        """非阻塞出队，无匹配命令返回 None。"""
        if filter_fn is not None:
            idx = self._find_best_index(filter_fn)
            if idx < 0:
                return None
            _, _, cmd = self._queue._queue.pop(idx)  # type: ignore[attr-defined]
            heapq.heapify(self._queue._queue)  # type: ignore[attr-defined]
            logger.debug("dequeue filtered: mode=%s priority=%s", cmd.mode, cmd.priority.name)
            return cmd

        try:
            _, _, cmd = self._queue.get_nowait()
            return cmd
        except asyncio.QueueEmpty:
            return None

    def peek(self, filter_fn: QueueFilter | None = None) -> QueuedCommand | None:
        """查看最高优先级命令但不移除（不影响 FIFO 顺序）。"""
        idx = self._find_best_index(filter_fn)
        if idx < 0:
            return None
        _, _, cmd = self._queue._queue[idx]  # type: ignore[attr-defined]
        return cmd

    def discard(self, filter_fn: QueueFilter) -> int:
        """Remove queued commands matching filter_fn and return the count."""
        kept = []
        removed = 0
        for item in self._queue._queue:  # type: ignore[attr-defined]
            _, _, cmd = item
            if filter_fn(cmd):
                removed += 1
            else:
                kept.append(item)
        if removed:
            self._queue._queue[:] = kept  # type: ignore[attr-defined]
            heapq.heapify(self._queue._queue)  # type: ignore[attr-defined]
            logger.debug("discarded %d queued commands", removed)
        return removed

    def is_empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()

    def can_batch_with(self, head: QueuedCommand, next_cmd: QueuedCommand | None) -> bool:
        """两个连续命令可否合并（同 mode + 同 origin 的 prompt）。"""
        if next_cmd is None:
            return False
        return (
            head.mode == "prompt"
            and next_cmd.mode == "prompt"
            and head.origin == next_cmd.origin
        )


# ── 全局队列单例 ──────────────────────────────────────

_main_queue: MessageQueue | None = None
_running_agents: set[asyncio.Task] = set()


def get_main_queue() -> MessageQueue:
    """获取全局主队列（懒初始化）。"""
    global _main_queue
    if _main_queue is None:
        _main_queue = MessageQueue()
    return _main_queue


def reset_main_queue() -> None:
    """重置全局队列和运行状态（用于测试）。"""
    global _main_queue
    _main_queue = None
    _running_agents.clear()


def register_running_agent(task: asyncio.Task) -> None:
    """注册一个后台 agent task，完成后自动移除。"""
    _running_agents.add(task)
    task.add_done_callback(_running_agents.discard)
    logger.debug("register_running_agent: total=%d", len(_running_agents))


def has_running_agents() -> bool:
    """是否仍有后台 agent 在运行。"""
    return bool(_running_agents)


def cancel_running_agents() -> int:
    """Cancel all registered background agent tasks and return the count."""
    count = 0
    for task in list(_running_agents):
        if not task.done():
            task.cancel()
            count += 1
    if count:
        logger.debug("cancelled %d running agent task(s)", count)
    return count
