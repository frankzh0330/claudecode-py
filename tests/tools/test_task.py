"""task 工具测试。"""

import pytest

from cc_python.tools.task import (
    TaskCreateTool, TaskUpdateTool, TaskListTool, TaskGetTool,
    _tasks,
)


@pytest.fixture(autouse=True)
def clean():
    _tasks.clear()
    yield
    _tasks.clear()


class TestTaskCreate:
    @pytest.mark.asyncio
    async def test_create(self):
        tool = TaskCreateTool()
        result = await tool.call(
            subject="Test task",
            description="A test task description",
        )
        assert "created" in result.lower() or "task" in result.lower()
        assert len(_tasks) == 1

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
    @pytest.mark.asyncio
    async def test_update_status(self):
        # 先创建
        create_tool = TaskCreateTool()
        create_result = await create_tool.call(subject="T1", description="D1")
        # 提取 task id
        task_id = list(_tasks.keys())[0]

        tool = TaskUpdateTool()
        result = await tool.call(taskId=task_id, status="in_progress")
        assert "in_progress" in result or "updated" in result.lower()

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        tool = TaskUpdateTool()
        result = await tool.call(taskId="nonexistent", status="completed")
        assert "not found" in result.lower()


class TestTaskList:
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
        task_id = list(_tasks.keys())[0]

        tool = TaskGetTool()
        result = await tool.call(taskId=task_id)
        assert "GetTest" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        tool = TaskGetTool()
        result = await tool.call(taskId="nonexistent")
        assert "not found" in result.lower()
