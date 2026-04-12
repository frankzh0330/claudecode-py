"""commands.py 测试。"""

import pytest

from cc_python.commands import (
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
    async def test_clear(self):
        result = await dispatch_command("clear", "")
        assert result.new_messages == []
        assert "cleared" in result.output.lower()

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
