"""task 工具测试。"""

import pytest

from termpilot.tools.task import (
    TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool,
    _get_tasks, _reset_tasks,
)
import termpilot.tools.task as task_module


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(task_module, "_tasks_file", lambda: tmp_path / "tasks.json")
    _reset_tasks()
    yield
    _reset_tasks()


class TestTaskCreate:
    def test_description_guides_todo_style_use(self):
        description = TaskCreateTool().description
        assert "3+ steps" in description
        assert "multi-file" in description
        assert "tested/verified" in description

    @pytest.mark.asyncio
    async def test_create(self):
        tool = TaskCreateTool()
        result = await tool.call(
            subject="Test task",
            description="A test task description",
        )
        assert "created" in result.lower() or "task" in result.lower()
        assert len(_get_tasks()) == 1

    @pytest.mark.asyncio
    async def test_create_with_active_form(self):
        tool = TaskCreateTool()
        result = await tool.call(
            subject="Task",
            description="desc",
            activeForm="Creating task",
        )
        assert "task" in result.lower()


class TestTaskUpdate:
    def test_description_guides_single_in_progress_task(self):
        description = TaskUpdateTool().description
        assert "one task" in description
        assert "in_progress" in description
        assert "completed" in description

    @pytest.mark.asyncio
    async def test_update_status(self):
        # 先创建
        create_tool = TaskCreateTool()
        await create_tool.call(subject="T1", description="D1")
        # 提取 task id
        task_id = list(_get_tasks().keys())[0]

        tool = TaskUpdateTool()
        result = await tool.call(taskId=task_id, status="in_progress")
        assert "in_progress" in result or "updated" in result.lower()

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        tool = TaskUpdateTool()
        result = await tool.call(taskId="nonexistent", status="completed")
        assert "not found" in result.lower()


class TestTaskList:
    def test_description_guides_focus_recovery(self):
        description = TaskListTool().description
        assert "current todo plan" in description
        assert "regain focus" in description

    @pytest.mark.asyncio
    async def test_list_empty(self):
        tool = TaskListTool()
        result = await tool.call()
        assert "no task" in result.lower() or "0" in result

    @pytest.mark.asyncio
    async def test_list_with_tasks(self):
        await TaskCreateTool().call(subject="T1", description="D1")
        await TaskCreateTool().call(subject="T2", description="D2")

        tool = TaskListTool()
        result = await tool.call()
        assert "T1" in result
        assert "T2" in result


class TestTaskGet:
    @pytest.mark.asyncio
    async def test_get(self):
        await TaskCreateTool().call(subject="GetTest", description="desc")
        task_id = list(_get_tasks().keys())[0]

        tool = TaskGetTool()
        result = await tool.call(taskId=task_id)
        assert "GetTest" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        tool = TaskGetTool()
        result = await tool.call(taskId="nonexistent")
        assert "not found" in result.lower()
