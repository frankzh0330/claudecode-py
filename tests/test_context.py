"""context.py 测试。"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestGetSystemContext:
    def test_returns_required_keys(self):
        from termpilot.context import get_system_context
        ctx = get_system_context()
        assert "os" in ctx
        assert "osVersion" in ctx
        assert "shell" in ctx
        assert "cwd" in ctx

    def test_cwd_is_string(self):
        from termpilot.context import get_system_context
        ctx = get_system_context()
        assert isinstance(ctx["cwd"], str)


class TestGetGitStatus:
    def test_in_git_repo(self):
        """在 git 仓库中运行，应该返回非 None。"""
        from termpilot.context import get_git_status
        status = get_git_status()
        # 本项目是 git 仓库
        assert status is not None
        assert "Current branch" in status

    @patch("termpilot.context.subprocess.run")
    def test_not_git_repo(self, mock_run):
        mock_run.return_value = MagicMock(stdout="false\n")
        from termpilot.context import get_git_status
        assert get_git_status() is None

    @patch("termpilot.context.subprocess.run", side_effect=FileNotFoundError)
    def test_git_not_installed(self, mock_run):
        from termpilot.context import get_git_status
        assert get_git_status() is None


class TestBuildSystemPrompt:
    def test_contains_intro(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "interactive agent" in prompt
        assert "CYBER_RISK" in prompt or "security testing" in prompt

    def test_contains_system_section(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# System" in prompt
        assert "permission mode" in prompt.lower()

    def test_contains_identity_framing(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# Identity and framing" in prompt
        assert "Present this project as TermPilot" in prompt
        assert "Do not mention reference implementations" in prompt

    def test_contains_doing_tasks(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# Doing tasks" in prompt

    def test_contains_tool_usage(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "Using your tools" in prompt

    def test_contains_tone_style(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# Tone and style" in prompt

    def test_contains_output_efficiency(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# Output efficiency" in prompt

    def test_contains_environment(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt()
        assert "# Environment" in prompt

    def test_with_model(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt(model="test-model-v1")
        assert "test-model-v1" in prompt

    def test_with_language(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt(language="Chinese")
        assert "Chinese" in prompt
        assert "# Language" in prompt

    def test_without_language(self):
        from termpilot.context import build_system_prompt
        prompt = build_system_prompt(language=None)
        assert "# Language" not in prompt


class TestSessionGuidance:
    def test_with_agent(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section({"agent"})
        assert result is not None
        assert "Agent" in result
        assert "subagent_type=Plan" in result
        assert "subagent_type=Explore" in result
        assert "subagent_type=Verification" in result
        assert "tasks array" in result
        assert "one Explore task per file/module" in result

    def test_with_task_tools(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section({"task_create", "task_update", "task_list"})
        assert result is not None
        assert "create a task list" in result
        assert "one task in_progress" in result

    def test_with_ask_user(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section({"ask_user_question"})
        assert result is not None
        assert "AskUserQuestion" in result

    def test_with_skill(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section({"skill"})
        assert result is not None
        assert "skill" in result.lower()

    def test_empty_tools(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section(set())
        # 仍然有 shell 命令建议
        assert result is not None
        assert "! <command>" in result

    def test_none_tools(self):
        from termpilot.context import get_session_guidance_section
        result = get_session_guidance_section(None)
        assert result is not None


class TestLanguageSection:
    def test_none(self):
        from termpilot.context import get_language_section
        assert get_language_section(None) is None

    def test_empty(self):
        from termpilot.context import get_language_section
        assert get_language_section("") is None

    def test_with_language(self):
        from termpilot.context import get_language_section
        result = get_language_section("Japanese")
        assert "Japanese" in result


class TestLoadMemoryPrompt:
    def test_no_memory_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.context.Path.cwd", lambda: tmp_path / "nonexistent")
        monkeypatch.setattr("termpilot.context.Path.home", lambda: tmp_path)
        from termpilot.context import load_memory_prompt
        assert load_memory_prompt() is None

    def test_with_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.context.Path.cwd", lambda: tmp_path / "project")
        monkeypatch.setattr("termpilot.context.Path.home", lambda: tmp_path)

        # 创建 memory 目录和文件
        cwd = str(tmp_path / "project")
        encoded = cwd.replace("/", "-")
        memory_dir = tmp_path / ".termpilot" / "projects" / encoded / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("- test memory entry", encoding="utf-8")

        from termpilot.context import load_memory_prompt
        result = load_memory_prompt()
        assert result is not None
        assert "test memory entry" in result
        assert "persistent" in result.lower()
