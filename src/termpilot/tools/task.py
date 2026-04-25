"""Task 工具组（任务管理）。

对应 TS: tools/TaskCreateTool/ + TaskUpdateTool/ + TaskListTool/ + TaskGetTool/
+ hooks/useTaskListWatcher.ts（自动取任务）
+ utils/tasks.ts（持久化 + 依赖图）

Python 版合并为单文件，支持：
- 文件持久化（~/.termpilot/projects/<cwd>/tasks.json）
- 依赖图（blocks / blockedBy）
- 自动取下一个可执行任务
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 数据模型 ──────────────────────────────────────────

@dataclass
class Task:
    """任务数据。"""
    id: str
    subject: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed | deleted
    owner: str = ""
    active_form: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 持久化 ────────────────────────────────────────────

def _tasks_file() -> Path:
    """获取当前项目的 tasks.json 路径。"""
    from termpilot.session import get_project_dir
    return get_project_dir() / "tasks.json"


def _load_tasks_from_disk() -> dict[str, Task]:
    """从磁盘加载所有任务。"""
    path = _tasks_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks = {}
        for k, v in data.items():
            # 兼容旧格式：确保新字段有默认值
            v.setdefault("blocks", [])
            v.setdefault("blocked_by", [])
            v.setdefault("metadata", {})
            tasks[k] = Task(**v)
        logger.debug("loaded %d tasks from %s", len(tasks), path)
        return tasks
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.debug("failed to load tasks: %s", e)
        return {}


def _save_tasks_to_disk() -> None:
    """将所有任务保存到磁盘。"""
    path = _tasks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v.to_dict() for k, v in _get_tasks().items()
            if v.status != "deleted"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 全局任务存储（懒加载）────────────────────────────

_tasks: dict[str, Task] | None = None
_task_counter: int = 0


def _get_tasks() -> dict[str, Task]:
    """获取任务存储，首次访问时从磁盘加载。"""
    global _tasks
    if _tasks is None:
        _tasks = _load_tasks_from_disk()
        if _tasks:
            try:
                global _task_counter
                _task_counter = max(int(k) for k in _tasks)
            except (ValueError, StopIteration):
                pass
    return _tasks


def _next_task_id() -> str:
    global _task_counter
    _task_counter += 1
    return str(_task_counter)


def _reset_tasks() -> None:
    """重置任务存储（用于测试）。"""
    global _tasks, _task_counter
    _tasks = {}
    _task_counter = 0


# ── 辅助函数 ──────────────────────────────────────────

def get_next_available_task(owner: str = "") -> Task | None:
    """获取下一个可执行的任务：pending + 无 owner 或 owner 匹配 + 不被阻塞。"""
    tasks = _get_tasks()
    for task in tasks.values():
        if task.status != "pending":
            continue
        if task.owner and task.owner != owner:
            continue
        blocked = any(
            tasks.get(tid) and tasks[tid].status != "completed"
            for tid in task.blocked_by
        )
        if not blocked:
            return task
    return None


def claim_task(task_id: str, owner: str) -> bool:
    """Claim a task for execution."""
    tasks = _get_tasks()
    task = tasks.get(task_id)
    if not task or task.status != "pending":
        return False
    task.owner = owner
    task.status = "in_progress"
    task.updated_at = time.time()
    _save_tasks_to_disk()
    return True


# ── TaskCreate ────────────────────────────────────────

class TaskCreateTool:
    """创建任务。"""

    @property
    def name(self) -> str:
        return "task_create"

    @property
    def description(self) -> str:
        return "Create a structured task for tracking progress on multi-step work."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "A brief title for the task (imperative form, e.g. 'Fix authentication bug').",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be done.",
                },
                "activeForm": {
                    "type": "string",
                    "description": "Present continuous form shown when task is in_progress (e.g. 'Running tests').",
                },
                "metadata": {
                    "type": "object",
                    "description": "Arbitrary metadata to attach to the task.",
                },
            },
            "required": ["subject", "description"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        subject = kwargs.get("subject", "")
        description = kwargs.get("description", "")
        active_form = kwargs.get("activeForm", "")
        metadata = kwargs.get("metadata", {})

        if not subject:
            return "Error: subject is required."

        task = Task(
            id=_next_task_id(),
            subject=subject,
            description=description,
            active_form=active_form,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        _get_tasks()[task.id] = task
        _save_tasks_to_disk()

        return json.dumps({"task": {"id": task.id, "subject": task.subject}}, ensure_ascii=False)


# ── TaskUpdate ────────────────────────────────────────

class TaskUpdateTool:
    """更新任务状态。"""

    @property
    def name(self) -> str:
        return "task_update"

    @property
    def description(self) -> str:
        return "Update a task's status, subject, description, dependencies, or owner."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "taskId": {
                    "type": "string",
                    "description": "The ID of the task to update.",
                },
                "status": {
                    "type": "string",
                    "description": "New status: 'pending', 'in_progress', 'completed', or 'deleted'.",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                },
                "subject": {
                    "type": "string",
                    "description": "New title for the task.",
                },
                "description": {
                    "type": "string",
                    "description": "New description.",
                },
                "activeForm": {
                    "type": "string",
                    "description": "New present continuous form.",
                },
                "addBlocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that cannot start until this task completes.",
                },
                "addBlockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that must complete before this task can start.",
                },
                "owner": {
                    "type": "string",
                    "description": "Set the task owner (agent name).",
                },
                "metadata": {
                    "type": "object",
                    "description": "Merge metadata into the task. Set a key to null to delete it.",
                },
            },
            "required": ["taskId"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        task_id = kwargs.get("taskId", "")
        tasks = _get_tasks()
        task = tasks.get(task_id)

        if not task:
            return f"Error: Task '{task_id}' not found."

        if "status" in kwargs:
            new_status = kwargs["status"]
            old_status = task.status
            task.status = new_status
            # 完成时自动解除下游阻塞（更新 block 记录）
            if new_status == "completed" and old_status != "completed":
                for other in tasks.values():
                    if task_id in other.blocked_by and other.status == "pending":
                        logger.debug("task %s unblocked by completing %s", other.id, task_id)

        if "subject" in kwargs:
            task.subject = kwargs["subject"]
        if "description" in kwargs:
            task.description = kwargs["description"]
        if "activeForm" in kwargs:
            task.active_form = kwargs["activeForm"]

        if "addBlocks" in kwargs:
            for tid in kwargs["addBlocks"]:
                if tid not in task.blocks and tid in tasks:
                    task.blocks.append(tid)
                    # 双向：在目标 task 上添加 blocked_by
                    other = tasks[tid]
                    if task_id not in other.blocked_by:
                        other.blocked_by.append(task_id)

        if "addBlockedBy" in kwargs:
            for tid in kwargs["addBlockedBy"]:
                if tid not in task.blocked_by and tid in tasks:
                    task.blocked_by.append(tid)
                    # 双向：在目标 task 上添加 blocks
                    other = tasks[tid]
                    if task_id not in other.blocks:
                        other.blocks.append(task_id)

        if "owner" in kwargs:
            task.owner = kwargs["owner"]

        if "metadata" in kwargs:
            meta = kwargs["metadata"]
            if isinstance(meta, dict):
                for k, v in meta.items():
                    if v is None:
                        task.metadata.pop(k, None)
                    else:
                        task.metadata[k] = v

        task.updated_at = time.time()
        _save_tasks_to_disk()

        result = {"id": task.id, "subject": task.subject, "status": task.status}
        if task.blocked_by:
            result["blocked_by"] = task.blocked_by
        if task.blocks:
            result["blocks"] = task.blocks
        return json.dumps({"task": result}, ensure_ascii=False)


# ── TaskList ──────────────────────────────────────────

class TaskListTool:
    """列出所有任务。"""

    @property
    def name(self) -> str:
        return "task_list"

    @property
    def description(self) -> str:
        return "List all tasks with their current status. Optionally filter by status or owner."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: pending, in_progress, completed.",
                },
                "owner": {
                    "type": "string",
                    "description": "Filter by owner.",
                },
            },
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        tasks = _get_tasks()
        filter_status = kwargs.get("status", "")
        filter_owner = kwargs.get("owner", "")

        if not tasks:
            return "No tasks."

        lines = []
        for task in tasks.values():
            if task.status == "deleted":
                continue
            if filter_status and task.status != filter_status:
                continue
            if filter_owner and task.owner != filter_owner:
                continue

            status_icon = {"pending": " ", "in_progress": "*", "completed": "x"}.get(task.status, "?")
            line = f"[{status_icon}] {task.id}: {task.subject} ({task.status})"
            if task.owner:
                line += f" [owner: {task.owner}]"
            if task.blocked_by:
                blocked_by_active = [t for t in task.blocked_by
                                     if tasks.get(t) and tasks[t].status != "completed"]
                if blocked_by_active:
                    line += f" [blocked by: {', '.join(blocked_by_active)}]"
            lines.append(line)
            if task.description:
                lines.append(f"    {task.description[:100]}")

        return "\n".join(lines) if lines else "No matching tasks."


# ── TaskGet ───────────────────────────────────────────

class TaskGetTool:
    """获取单个任务详情。"""

    @property
    def name(self) -> str:
        return "task_get"

    @property
    def description(self) -> str:
        return "Get full details of a specific task by ID."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "taskId": {
                    "type": "string",
                    "description": "The ID of the task to retrieve.",
                },
            },
            "required": ["taskId"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        task_id = kwargs.get("taskId", "")
        task = _get_tasks().get(task_id)

        if not task:
            return f"Error: Task '{task_id}' not found."

        return json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
