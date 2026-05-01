"""config.py 测试。"""

import json

import pytest


class TestGetSettings:
    def test_reads_json(self, tmp_settings):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test"}})
        from termpilot.config import get_settings
        assert get_settings()["env"]["ANTHROPIC_API_KEY"] == "sk-test"

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("termpilot.config.get_settings_path", lambda: tmp_path / "nonexistent.json")
        from termpilot.config import get_settings
        assert get_settings() == {}

    def test_invalid_json(self, tmp_settings):
        settings_file = tmp_settings.__wrapped__ if hasattr(tmp_settings, '__wrapped__') else None
        # tmp_settings 是一个函数，需要直接写文件
        path = tmp_settings({})
        path.write_text("not json{", encoding="utf-8")
        from termpilot.config import get_settings
        assert get_settings() == {}


class TestGetSettingsEnv:
    def test_extracts_env(self, tmp_settings):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_MODEL": "gpt-4"}})
        from termpilot.config import get_settings_env
        env = get_settings_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["ANTHROPIC_MODEL"] == "gpt-4"

    def test_filters_non_string(self, tmp_settings):
        tmp_settings({"env": {"KEY": "val", "NUM": 42, "FLAG": True}})
        from termpilot.config import get_settings_env
        env = get_settings_env()
        assert "KEY" in env
        assert "NUM" not in env
        assert "FLAG" not in env

    def test_no_env_field(self, tmp_settings):
        tmp_settings({"other": "data"})
        from termpilot.config import get_settings_env
        assert get_settings_env() == {}


class TestApplySettingsEnv:
    def test_injects_env(self, tmp_settings, env_clean):
        tmp_settings({"env": {"ANTHROPIC_API_KEY": "sk-test"}})
        from termpilot.config import apply_settings_env
        import os
        apply_settings_env()
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test"


class TestGetEffectiveApiKey:
    def test_from_env(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key() == "sk-env"

    def test_from_settings(self, tmp_settings, env_clean):
        tmp_settings({"env": {"OPENAI_API_KEY": "sk-settings"}})
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key() == "sk-settings"

    def test_env_priority(self, tmp_settings, env_clean, monkeypatch):
        tmp_settings({"env": {"OPENAI_API_KEY": "sk-settings"}})
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key() == "sk-env"

    def test_anthropic_provider(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("TERMPILOT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key() == "sk-anthropic"

    def test_zhipu_fallback(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu.xxx")
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key("openai_compatible") == "zhipu.xxx"

    def test_none_when_no_key(self, tmp_settings, env_clean):
        from termpilot.config import get_effective_api_key
        assert get_effective_api_key() is None


class TestGetEffectiveProvider:
    def test_default(self, tmp_settings, env_clean):
        from termpilot.config import get_effective_provider
        assert get_effective_provider() == "openai"

    def test_alias_normalization(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("TERMPILOT_PROVIDER", "zhipu")
        from termpilot.config import get_effective_provider
        assert get_effective_provider() == "openai_compatible"

    def test_setup_provider_list_is_curated(self):
        from termpilot.config import _PROVIDERS
        assert list(_PROVIDERS.keys()) == [
            "Anthropic (Claude)",
            "OpenAI",
            "Zhipu GLM",
            "DeepSeek",
            "Seed",
        ]

    def test_deepseek_provider_defaults_use_v4(self):
        from termpilot.config import _MODEL_PRESETS, _PROVIDERS

        assert _PROVIDERS["DeepSeek"]["base_url"] == "https://api.deepseek.com"
        assert _PROVIDERS["DeepSeek"]["default_model"] == "deepseek-v4-pro"
        assert _MODEL_PRESETS["deepseek"][0] == "deepseek-v4-pro"


class TestGetEffectiveModel:
    def test_default(self, tmp_settings, env_clean):
        from termpilot.config import get_effective_model
        assert get_effective_model() == "gpt-4o"

    def test_env_override(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
        from termpilot.config import get_effective_model
        assert get_effective_model() == "gpt-4o-mini"

    def test_settings_fallback(self, tmp_settings, env_clean):
        tmp_settings({"env": {"OPENAI_MODEL": "glm-4-flash"}})
        from termpilot.config import get_effective_model
        assert get_effective_model() == "glm-4-flash"

    def test_anthropic_model(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("TERMPILOT_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        from termpilot.config import get_effective_model
        assert get_effective_model() == "claude-sonnet-4-20250514"


class TestGetEffectiveBaseUrl:
    def test_openai_base_url(self, tmp_settings, env_clean):
        tmp_settings({"env": {"TERMPILOT_PROVIDER": "openai_compatible", "OPENAI_BASE_URL": "https://api.example.com/v1"}})
        from termpilot.config import get_effective_base_url
        assert get_effective_base_url() == "https://api.example.com/v1"

    def test_provider_specific_alias(self, tmp_settings, env_clean):
        tmp_settings({"env": {"TERMPILOT_PROVIDER": "zhipu", "ZHIPU_BASE_URL": "https://open.bigmodel.cn/api/paas/v4"}})
        from termpilot.config import get_effective_base_url
        assert get_effective_base_url() == "https://open.bigmodel.cn/api/paas/v4"

    def test_seed_provider_env_keys(self, tmp_settings, env_clean):
        tmp_settings({
            "provider": "seed",
            "env": {
                "ARK_API_KEY": "ark-test",
                "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
                "ARK_MODEL": "doubao-seed-2-0-code-preview-260215",
            },
        })
        from termpilot.config import get_effective_api_key, get_effective_base_url, get_effective_model
        assert get_effective_api_key() == "ark-test"
        assert get_effective_base_url() == "https://ark.cn-beijing.volces.com/api/v3"
        assert get_effective_model() == "doubao-seed-2-0-code-preview-260215"

    def test_seed_provider_prefers_ark_over_stale_deepseek(self, tmp_settings, env_clean):
        tmp_settings({
            "provider": "seed",
            "env": {
                "DEEPSEEK_API_KEY": "deepseek-stale",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
                "ARK_API_KEY": "ark-current",
                "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
                "ARK_MODEL": "doubao-seed-2-0-code-preview-260215",
            },
        })
        from termpilot.config import get_effective_api_key, get_effective_base_url, get_effective_model
        assert get_effective_api_key() == "ark-current"
        assert get_effective_base_url() == "https://ark.cn-beijing.volces.com/api/v3"
        assert get_effective_model() == "doubao-seed-2-0-code-preview-260215"

    def test_create_client_keeps_raw_provider_for_compatible_base_url(
        self, tmp_settings, env_clean, monkeypatch
    ):
        tmp_settings({
            "provider": "seed",
            "env": {
                "DEEPSEEK_API_KEY": "deepseek-stale",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
                "ARK_API_KEY": "ark-current",
                "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
                "ARK_MODEL": "doubao-seed-2-0-code-preview-260215",
            },
        })

        captured = {}

        class FakeAsyncOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

        from termpilot.api import create_client

        _, client_format = create_client()
        assert client_format == "openai"
        assert captured["api_key"] == "ark-current"
        assert captured["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"


class TestGetContextWindow:
    def test_default(self, tmp_settings, env_clean):
        from termpilot.config import get_context_window
        assert get_context_window() == 200_000

    def test_env_override(self, tmp_settings, env_clean, monkeypatch):
        monkeypatch.setenv("TERMPILOT_CONTEXT_WINDOW", "100000")
        from termpilot.config import get_context_window
        assert get_context_window() == 100_000
