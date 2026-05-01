"""agent delegation tool tests."""

import json

import pytest

from termpilot.tools.agent import AgentTool, MAX_BATCH_TASKS


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
        assert launched["agent_type"] == "Explore"
        assert launched["prompt"] == "Find command registration."
        assert launched["description"] == "Inspect commands"

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
