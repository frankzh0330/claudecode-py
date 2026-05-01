"""commands.py 测试。"""

from types import SimpleNamespace

import pytest

from termpilot.commands import (
    CommandResult, Command, parse_slash_command,
    register_command, find_command, get_all_commands, dispatch_command,
)


class TestParseSlashCommand:
    def test_help(self):
        result = parse_slash_command("/help")
        assert result == ("help", "")

    def test_with_args(self):
        result = parse_slash_command("/compact force")
        assert result == ("compact", "force")

    def test_not_slash(self):
        assert parse_slash_command("hello") is None

    def test_empty_slash(self):
        assert parse_slash_command("/") is None

    def test_whitespace(self):
        assert parse_slash_command("  /help  ") == ("help", "")

    def test_case_insensitive(self):
        result = parse_slash_command("/HELP")
        assert result == ("help", "")

    def test_multi_word_args(self):
        result = parse_slash_command("/prompt read the file")
        assert result == ("prompt", "read the file")


class TestCommandRegistration:
    async def _dummy_handler(self, args, ctx):
        return CommandResult(output="dummy")

    def test_register_and_find(self, clean_commands):
        cmd = Command(name="test", description="Test", handler=self._dummy_handler)
        register_command(cmd)
        assert find_command("test") is cmd

    def test_find_by_alias(self, clean_commands):
        cmd = Command(name="test", description="Test", handler=self._dummy_handler, aliases=["t"])
        register_command(cmd)
        assert find_command("t") is cmd

    def test_get_all_commands(self, clean_commands):
        # 内置命令应该存在
        cmds = get_all_commands()
        names = {c.name for c in cmds}
        assert "help" in names
        assert "exit" in names
        assert "compact" in names


class TestDispatchCommands:
    @pytest.mark.asyncio
    async def test_help(self):
        result = await dispatch_command("help", "")
        assert "Available commands" in result.output
        assert "/help" in result.output

    @pytest.mark.asyncio
    async def test_clear(self, clean_tasks):
        from termpilot.tools.task import TaskCreateTool, _get_tasks

        await TaskCreateTool().call(subject="Old task", description="Should be cleared")
        assert _get_tasks()

        result = await dispatch_command("clear", "")
        assert result.new_messages == []
        assert "cleared" in result.output.lower()
        assert _get_tasks() == {}

    @pytest.mark.asyncio
    async def test_exit(self):
        result = await dispatch_command("exit", "")
        assert result.exit_repl is True

    @pytest.mark.asyncio
    async def test_quit_alias(self):
        result = await dispatch_command("quit", "")
        assert result.exit_repl is True

    @pytest.mark.asyncio
    async def test_unknown(self):
        result = await dispatch_command("nonexistent", "")
        assert "Unknown command" in result.output

    @pytest.mark.asyncio
    async def test_config(self, tmp_settings, env_clean):
        tmp_settings({"env": {"ANTHROPIC_MODEL": "test-model"}})
        result = await dispatch_command("config", "")
        assert "test-model" in result.output
        assert "Model" in result.output

    @pytest.mark.asyncio
    async def test_skills_empty(self, clean_skills):
        result = await dispatch_command("skills", "")
        assert "No skills" in result.output or "no skills" in result.output.lower()

    @pytest.mark.asyncio
    async def test_mcp_no_manager(self):
        result = await dispatch_command("mcp", "")
        assert "not initialized" in result.output.lower()

    @pytest.mark.asyncio
    async def test_model(self, monkeypatch):
        called = {"wizard": False, "refresh": False}
        call_count = {"n": 0}

        def fake_wizard():
            called["wizard"] = True

        def fake_refresh():
            called["refresh"] = True

        def fake_get_model():
            call_count["n"] += 1
            return "gpt-4o" if call_count["n"] > 1 else "glm-5.1"

        monkeypatch.setattr("termpilot.config.run_setup_wizard", fake_wizard)
        monkeypatch.setattr("termpilot.config.get_effective_model", fake_get_model)
        result = await dispatch_command("model", "", {"refresh_runtime": fake_refresh})

        assert called["wizard"] is True
        assert called["refresh"] is True
        assert result.output == "Switched model to gpt-4o"

    @pytest.mark.asyncio
    async def test_model_cancelled(self, monkeypatch):
        def fake_wizard():
            pass

        monkeypatch.setattr("termpilot.config.run_setup_wizard", fake_wizard)
        monkeypatch.setattr("termpilot.config.get_effective_model", lambda: "glm-5.1")
        result = await dispatch_command("model", "", {})

        assert result.output == "Kept model as glm-5.1"

    @pytest.mark.asyncio
    async def test_compact_no_meaningful_reduction(self, monkeypatch):
        async def fake_auto_compact(messages, system_prompt, client, model, **kwargs):
            return messages

        monkeypatch.setattr("termpilot.compact.auto_compact_if_needed", fake_auto_compact)
        monkeypatch.setattr("termpilot.compact.estimate_tokens", lambda messages, system_prompt: 12_547)

        result = await dispatch_command(
            "compact",
            "",
            {
                "messages": [{"role": "user", "content": "hi"}],
                "system_prompt": "",
                "client": SimpleNamespace(),
                "model": "gpt-4o",
                "client_format": "openai",
            },
        )

        assert result.should_query is False
        assert result.new_messages is None
        assert "Context not compacted" in result.output

    @pytest.mark.asyncio
    async def test_rewind_uses_numeric_choice(self, monkeypatch):
        class FakeStorage:
            session_id = "session-1"

            def __init__(self):
                self.last_uuid = None

            def set_last_uuid(self, uuid):
                self.last_uuid = uuid

        turns = [
            {"uuid": "u1", "preview": "first request"},
            {"uuid": "u2", "preview": "second request"},
        ]
        messages = [{"role": "user", "content": "second request"}]
        storage = FakeStorage()

        monkeypatch.setattr("termpilot.session.list_session_turns", lambda session_id: turns)
        monkeypatch.setattr("termpilot.session.load_session_at_point", lambda session_id, uuid: messages)
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")

        result = await dispatch_command("rewind", "", {"storage": storage})

        assert result.new_messages == messages
        assert storage.last_uuid == "u2"
        assert result.output == "Rewound to 1 messages."

    @pytest.mark.asyncio
    async def test_rewind_blank_choice_cancels(self, monkeypatch):
        class FakeStorage:
            session_id = "session-1"

            def set_last_uuid(self, uuid):
                raise AssertionError("set_last_uuid should not be called")

        monkeypatch.setattr(
            "termpilot.session.list_session_turns",
            lambda session_id: [{"uuid": "u1", "preview": "first request"}],
        )
        monkeypatch.setattr("builtins.input", lambda prompt="": "")

        result = await dispatch_command("rewind", "", {"storage": FakeStorage()})

        assert result.output == "Cancelled."
        assert result.new_messages is None
