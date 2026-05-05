"""agent delegation tool tests."""

import json

import pytest

import termpilot.agent_tasks as agent_tasks
from termpilot.agent_tasks import (
    create_agent_task,
    get_agent_task,
    list_agent_tasks,
    load_agent_messages,
    reset_agent_tasks,
    update_agent_task,
)
from termpilot.tools.agent import (
    AgentTool, AgentSendTool, AgentTaskGetTool, AgentTaskListTool, MAX_BATCH_TASKS,
)


@pytest.fixture(autouse=True)
def clean_agent_runtime(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "agent-runtime"
    runtime_dir.mkdir()
    monkeypatch.setattr(agent_tasks, "_runtime_dir", lambda: runtime_dir)
    reset_agent_tasks()
    yield
    reset_agent_tasks()


class TestAgentToolSchema:
    def test_schema_supports_single_and_batch_delegation(self):
        schema = AgentTool().input_schema

        properties = schema["properties"]
        assert "subagent_type" in properties
        assert "prompt" in properties
        assert "tasks" in properties
        assert "run_in_background" in properties
        assert properties["tasks"]["maxItems"] == MAX_BATCH_TASKS
        assert schema.get("required", []) == []

    def test_description_uses_delegation_language(self):
        description = AgentTool().description
        assert "delegate_task" in description
        assert "tasks array" in description
        assert "Plan" in description
        assert "Explore" in description
        assert "Verification" in description
        assert "one Explore task per file/module" in description
        assert "AgentTask" in description
        assert "agent_send" in description

    def test_builtin_agent_prompts_keep_termpilot_framing(self):
        from termpilot.tools.agent import BUILTIN_AGENTS

        for config in BUILTIN_AGENTS.values():
            prompt = config["prompt"]
            assert "TermPilot perspective" in prompt
            assert "Claude Code" in prompt
            assert "unless the user explicitly asks" in prompt


class TestAgentToolCall:
    @pytest.mark.asyncio
    async def test_single_task_path_remains_compatible(self, monkeypatch):
        async def fake_run_agent(self, subagent_type, config, prompt):
            return f"{subagent_type}: {prompt}"

        monkeypatch.setattr(AgentTool, "_run_agent", fake_run_agent)

        result = await AgentTool().call(
            subagent_type="Explore",
            description="Inspect commands",
            prompt="Find command registration.",
        )

        assert result == "Explore: Find command registration."
        tasks = list_agent_tasks()
        assert len(tasks) == 1
        assert tasks[0].agent_type == "Explore"
        assert tasks[0].status == "completed"
        assert load_agent_messages(tasks[0].id)[-1]["content"] == result

    @pytest.mark.asyncio
    async def test_background_agent_returns_launch_notification(self, monkeypatch):
        launched = {}

        def fake_launch(self, agent_id, agent_type, config, prompt, description=""):
            launched.update({
                "agent_id": agent_id,
                "agent_type": agent_type,
                "prompt": prompt,
                "description": description,
            })
            return None

        monkeypatch.setattr(AgentTool, "_launch_async_agent", fake_launch)

        result = await AgentTool().call(
            subagent_type="Explore",
            description="Inspect commands",
            prompt="Find command registration.",
            run_in_background=True,
        )
        data = json.loads(result)

        assert data["status"] == "async_launched"
        assert data["subagent_type"] == "Explore"
        assert data["agent_id"].startswith("agent-")
        assert launched["agent_type"] == "Explore"
        assert launched["prompt"] == "Find command registration."
        assert launched["description"] == "Inspect commands"
        runtime_task = get_agent_task(data["agent_id"])
        assert runtime_task is not None
        assert runtime_task.status == "running"
        assert runtime_task.foreground is False

    @pytest.mark.asyncio
    async def test_batch_delegation_runs_each_task(self, monkeypatch):
        async def fake_run_agent(self, subagent_type, config, prompt):
            return f"{subagent_type}: {prompt}"

        monkeypatch.setattr(AgentTool, "_run_agent", fake_run_agent)

        result = await AgentTool().call(tasks=[
            {
                "subagent_type": "Explore",
                "description": "Inspect commands",
                "prompt": "Find command registration.",
            },
            {
                "subagent_type": "Plan",
                "description": "Plan redo",
                "prompt": "Plan a /redo command.",
            },
        ])
        data = json.loads(result)

        assert data["summary"] == {"total": 2, "succeeded": 2, "failed": 0}
        assert data["delegated_tasks"][0]["subagent_type"] == "Explore"
        assert data["delegated_tasks"][0]["agent_id"].startswith("agent-")
        assert data["delegated_tasks"][0]["success"] is True
        assert data["delegated_tasks"][1]["result"] == "Plan: Plan a /redo command."

    @pytest.mark.asyncio
    async def test_batch_unknown_agent_does_not_stop_other_tasks(self, monkeypatch):
        async def fake_run_agent(self, subagent_type, config, prompt):
            return f"{subagent_type}: {prompt}"

        monkeypatch.setattr(AgentTool, "_run_agent", fake_run_agent)

        result = await AgentTool().call(tasks=[
            {"subagent_type": "Nope", "description": "Bad agent", "prompt": "Try it."},
            {"subagent_type": "Explore", "description": "Good agent", "prompt": "Search."},
        ])
        data = json.loads(result)

        assert data["summary"] == {"total": 2, "succeeded": 1, "failed": 1}
        assert data["delegated_tasks"][0]["success"] is False
        assert "Unknown agent type" in data["delegated_tasks"][0]["error"]
        assert data["delegated_tasks"][1]["success"] is True

    @pytest.mark.asyncio
    async def test_batch_size_limit(self, monkeypatch):
        async def fake_run_agent(self, subagent_type, config, prompt):
            return "should not run"

        monkeypatch.setattr(AgentTool, "_run_agent", fake_run_agent)

        result = await AgentTool().call(tasks=[
            {"subagent_type": "Explore", "prompt": "one"},
            {"subagent_type": "Explore", "prompt": "two"},
            {"subagent_type": "Explore", "prompt": "three"},
            {"subagent_type": "Explore", "prompt": "four"},
        ])

        assert "at most 3" in result


class TestAgentSendTool:
    @pytest.mark.asyncio
    async def test_send_continues_existing_agent_task(self, monkeypatch):
        runtime_task = create_agent_task(
            "Explore",
            "Initial prompt.",
            "Inspect commands",
            foreground=True,
        )
        update_agent_task(runtime_task.id, status="completed")

        async def fake_run_messages(self, agent_type, config, messages, parent_on_event=None):
            assert agent_type == "Explore"
            assert messages[-1] == {"role": "user", "content": "Follow up."}
            return "continued result"

        monkeypatch.setattr(AgentTool, "_run_agent_messages", fake_run_messages)

        result = await AgentSendTool().call(
            agent_id=runtime_task.id,
            message="Follow up.",
        )

        assert result == "continued result"
        refreshed = get_agent_task(runtime_task.id)
        assert refreshed is not None
        assert refreshed.status == "completed"
        messages = load_agent_messages(runtime_task.id)
        assert messages[-2]["content"] == "Follow up."
        assert messages[-1]["content"] == "continued result"

    @pytest.mark.asyncio
    async def test_send_rejects_running_agent_task(self):
        runtime_task = create_agent_task("Explore", "Initial prompt.")
        update_agent_task(runtime_task.id, status="running")

        result = await AgentSendTool().call(
            agent_id=runtime_task.id,
            message="Follow up.",
        )

        assert "already running" in result


class TestAgentTaskTools:
    @pytest.mark.asyncio
    async def test_list_and_get_agent_tasks(self):
        runtime_task = create_agent_task(
            "Plan",
            "Plan a feature.",
            "Plan feature",
            foreground=False,
        )
        update_agent_task(runtime_task.id, status="completed", summary="done")

        listed = await AgentTaskListTool().call()
        assert runtime_task.id in listed
        assert "Plan feature" in listed

        payload = json.loads(await AgentTaskGetTool().call(agent_id=runtime_task.id))
        assert payload["id"] == runtime_task.id
        assert payload["status"] == "completed"
