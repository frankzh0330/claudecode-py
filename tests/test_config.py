"""config.py 测试。"""

import json

import pytest


class TestGetSettings:
    def test_reads_json(self, tmp_settings):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test"}})
        from cc_python.config import get_settings
        assert get_settings()["env"]["ANTHROPIC_API_KEY"] == "sk-test"

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cc_python.config._get_settings_path", lambda: tmp_path / "nonexistent.json")
        from cc_python.config import get_settings
        assert get_settings() == {}

    def test_invalid_json(self, tmp_settings):
        settings_file = tmp_settings.__wrapped__ if hasattr(tmp_settings, '__wrapped__') else None
        # tmp_settings 是一个函数，需要直接写文件
        path = tmp_settings({})
        path.write_text("not json{", encoding="utf-8")
        from cc_python.config import get_settings
        assert get_settings() == {}


class TestGetSettingsEnv:
    def test_extracts_env(self, tmp_settings):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_MODEL": "gpt-4"}})
        from cc_python.config import get_settings_env
        env = get_settings_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["ANTHROPIC_MODEL"] == "gpt-4"

    def test_filters_non_string(self, tmp_settings):
        tmp_settings({"env": {"KEY": "val", "NUM": 42, "FLAG": True}})
        from cc_python.config import get_settings_env
        env = get_settings_env()
        assert "KEY" in env
        assert "NUM" not in env
        assert "FLAG" not in env

    def test_no_env_field(self, tmp_settings):
        tmp_settings({"other": "data"})
        from cc_python.config import get_settings_env
        assert get_settings_env() == {}


class TestApplySettingsEnv:
    def test_injects_env(self, tmp_settings, env_clean):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test"}})
        from cc_python.config import apply_settings_env
        import os
        apply_settings_env()
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test"


class TestGetEffectiveApiKey:
    def test_from_env(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() == "sk-env"

    def test_from_settings(self, tmp_settings, env_clean):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-settings"}})
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() == "sk-settings"

    def test_env_priority(self, tmp_settings, env_clean, monkeypatch):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-settings"}})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() == "sk-env"

    def test_auth_token_fallback(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-123")
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() == "token-123"

    def test_zhipu_fallback(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu.xxx")
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() == "zhipu.xxx"

    def test_none_when_no_key(self, tmp_settings, env_clean):
        from cc_python.config import get_effective_api_key
        assert get_effective_api_key() is None


class TestGetEffectiveModel:
    def test_default(self, tmp_settings, env_clean):
        from cc_python.config import get_effective_model
        assert get_effective_model() == "claude-sonnet-4-20250514"

    def test_env_override(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_MODEL", "gpt-4o")
        from cc_python.config import get_effective_model
        assert get_effective_model() == "gpt-4o"

    def test_settings_fallback(self, tmp_settings, env_clean):
        tmp_settings({"env": {"ANTHROPIC_MODEL": "glm-4-flash"}})
        from cc_python.config import get_effective_model
        assert get_effective_model() == "glm-4-flash"


class TestGetContextWindow:
    def test_default(self, tmp_settings, env_clean):
        from cc_python.config import get_context_window
        assert get_context_window() == 200_000

    def test_env_override(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONTEXT_WINDOW", "100000")
        from cc_python.config import get_context_window
        assert get_context_window() == 100_000
