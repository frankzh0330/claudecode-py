"""Persistent agent task runtime state.

This module gives spawned subagents a durable runtime identity. It is separate
from tools/task.py, which is a todo-list style planning tool for the main agent.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


AgentTaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
AgentExecutionMode = Literal["local", "remote"]


@dataclass
class AgentTask:
    """Durable runtime record for one spawned agent."""

    id: str
    agent_type: str
    description: str = ""
    prompt: str = ""
    status: AgentTaskStatus = "pending"
    execution_mode: AgentExecutionMode = "local"
    foreground: bool = True
    parent_session_id: str = ""
    transcript_path: str = ""
    result_path: str = ""
    summary: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_agent_tasks: dict[str, AgentTask] | None = None


def _runtime_dir() -> Path:
    from termpilot.session import get_project_dir

    path = get_project_dir() / "agent-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return _runtime_dir() / "agent_tasks.json"


def _transcript_path(agent_id: str) -> Path:
    return _runtime_dir() / f"{agent_id}.jsonl"


def _load_agent_tasks_from_disk() -> dict[str, AgentTask]:
    path = _index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    tasks: dict[str, AgentTask] = {}
    if not isinstance(raw, dict):
        return tasks
    for task_id, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        payload.setdefault("execution_mode", "local")
        payload.setdefault("foreground", True)
        payload.setdefault("parent_session_id", "")
        payload.setdefault("transcript_path", str(_transcript_path(task_id)))
        payload.setdefault("result_path", "")
        payload.setdefault("summary", "")
        payload.setdefault("error", "")
        payload.setdefault("metadata", {})
        try:
            tasks[task_id] = AgentTask(**payload)
        except TypeError:
            continue
    return tasks


def _get_agent_tasks() -> dict[str, AgentTask]:
    global _agent_tasks
    if _agent_tasks is None:
        _agent_tasks = _load_agent_tasks_from_disk()
    return _agent_tasks


def _save_agent_tasks_to_disk() -> None:
    path = _index_path()
    data = {task_id: task.to_dict() for task_id, task in _get_agent_tasks().items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_agent_tasks() -> None:
    """Reset in-memory agent task state for tests."""

    global _agent_tasks
    _agent_tasks = {}


def create_agent_task(
    agent_type: str,
    prompt: str,
    description: str = "",
    *,
    foreground: bool = True,
    execution_mode: AgentExecutionMode = "local",
    parent_session_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> AgentTask:
    """Create and persist a new agent runtime task."""

    agent_id = f"agent-{uuid4().hex[:8]}"
    task = AgentTask(
        id=agent_id,
        agent_type=agent_type,
        description=description,
        prompt=prompt,
        foreground=foreground,
        execution_mode=execution_mode,
        parent_session_id=parent_session_id,
        transcript_path=str(_transcript_path(agent_id)),
        metadata=metadata or {},
    )
    _get_agent_tasks()[agent_id] = task
    append_agent_message(agent_id, "user", prompt)
    _save_agent_tasks_to_disk()
    return task


def get_agent_task(agent_id: str) -> AgentTask | None:
    return _get_agent_tasks().get(agent_id)


def list_agent_tasks(status: str = "") -> list[AgentTask]:
    tasks = list(_get_agent_tasks().values())
    if status:
        tasks = [task for task in tasks if task.status == status]
    return sorted(tasks, key=lambda task: task.updated_at, reverse=True)


def update_agent_task(agent_id: str, **updates: Any) -> AgentTask | None:
    task = get_agent_task(agent_id)
    if not task:
        return None

    for key, value in updates.items():
        if hasattr(task, key):
            setattr(task, key, value)
    task.updated_at = time.time()
    _save_agent_tasks_to_disk()
    return task


def append_agent_message(agent_id: str, role: str, content: Any) -> None:
    task = get_agent_task(agent_id)
    path = Path(task.transcript_path) if task else _transcript_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.time(),
        "role": role,
        "content": content,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_agent_messages(agent_id: str) -> list[dict[str, Any]]:
    task = get_agent_task(agent_id)
    if not task:
        return []
    path = Path(task.transcript_path)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = entry.get("role")
            content = entry.get("content")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
    return messages
