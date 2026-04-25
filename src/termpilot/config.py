"""配置管理。

对应 TS: utils/config.ts + utils/managedEnv.ts

核心机制：
1. 读取 ~/.termpilot/settings.json
2. 其中的 env 字段包含环境变量
3. 将 env 注入到 os.environ
4. 后续创建 API 客户端时自动使用这些环境变量

Python 版在此基础上额外支持显式 provider 选择：
- anthropic
- openai
- openai_compatible

并兼容常见第三方 OpenAI-compatible 平台的变量命名：
Zhipu GLM / DeepSeek / Qwen(DashScope) / Moonshot / SiliconFlow /
OpenRouter / Groq / Together / Fireworks / Ollama / vLLM 等。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _esc_ask(question) -> Any:
    """Ask a questionary prompt with ESC to cancel."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    bindings = KeyBindings()

    @bindings.add(Keys.Escape, eager=True)
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    kb = question.application.key_bindings
    if hasattr(kb, "add"):
        kb.add(Keys.Escape, eager=True)(_cancel)
    elif hasattr(kb, "registries"):
        kb.registries.append(bindings)
    return question.ask()


_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
    "openai_compatible": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "compatible": "openai_compatible",
    "custom": "openai_compatible",
    "zhipu": "openai_compatible",
    "glm": "openai_compatible",
    "deepseek": "openai_compatible",
    "qwen": "openai_compatible",
    "dashscope": "openai_compatible",
    "moonshot": "openai_compatible",
    "kimi": "openai_compatible",
    "siliconflow": "openai_compatible",
    "openrouter": "openai_compatible",
    "groq": "openai_compatible",
    "together": "openai_compatible",
    "fireworks": "openai_compatible",
    "ollama": "openai_compatible",
    "vllm": "openai_compatible",
}


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _normalize_provider(provider: str | None) -> str:
    if not provider:
        return "anthropic"
    return _PROVIDER_ALIASES.get(provider.strip().lower(), "openai_compatible")


_SETTINGS_TEMPLATE = """\
{
  "provider": "openai",
  "env": {
    "OPENAI_API_KEY": "sk-your-api-key",
    "OPENAI_MODEL": "gpt-4o"
  }
}
"""

_PROVIDERS: dict[str, dict[str, Any]] = {
    "Anthropic (Claude)": {
        "provider": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": None,
        "default_model": "claude-sonnet-4-20250514",
    },
    "OpenAI": {
        "provider": "openai",
        "env_key": "OPENAI_API_KEY",
        "base_url": None,
        "default_model": "gpt-4o",
    },
    "Zhipu GLM": {
        "provider": "zhipu",
        "env_key": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "default_model": "glm-5.1",
    },
    "DeepSeek": {
        "provider": "deepseek",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "Qwen / DashScope": {
        "provider": "qwen",
        "env_key": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "base_url_env_key": "DASHSCOPE_BASE_URL",
        "model_env_key": "DASHSCOPE_MODEL",
    },
    "Moonshot / Kimi": {
        "provider": "moonshot",
        "env_key": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
    },
    "SiliconFlow": {
        "provider": "siliconflow",
        "env_key": "SILICONFLOW_API_KEY",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
    },
    "OpenRouter": {
        "provider": "openrouter",
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o",
    },
    "Groq": {
        "provider": "groq",
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "Together": {
        "provider": "together",
        "env_key": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3-70b-chat-hf",
    },
    "Fireworks": {
        "provider": "fireworks",
        "env_key": "FIREWORKS_API_KEY",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p1-70b-chat",
    },
    "Ollama (local)": {
        "provider": "ollama",
        "env_key": None,
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
    },
    "vLLM (local)": {
        "provider": "vllm",
        "env_key": None,
        "base_url": "http://localhost:8000/v1",
        "default_model": "",
    },
    "OpenAI-compatible (custom)": {
        "provider": "openai_compatible",
        "env_key": "TERMPILOT_API_KEY",
        "base_url": "",
        "default_model": "",
        "base_url_env_key": "TERMPILOT_BASE_URL",
        "model_env_key": "TERMPILOT_MODEL",
    },
}

_MODEL_PRESETS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.1-mini",
    ],
    "zhipu": [
        "glm-5.1",
        "glm-4.5",
        "glm-4.5-air",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "qwen": [
        "qwen-plus",
        "qwen-max",
        "qwen-turbo",
    ],
    "moonshot": [
        "moonshot-v1-8k",
        "moonshot-v1-32k",
        "moonshot-v1-128k",
    ],
    "siliconflow": [
        "Qwen/Qwen2.5-7B-Instruct",
        "deepseek-ai/DeepSeek-V3",
    ],
    "openrouter": [
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "mixtral-8x7b-32768",
    ],
    "together": [
        "meta-llama/Llama-3-70b-chat-hf",
        "deepseek-ai/DeepSeek-V3",
    ],
    "fireworks": [
        "accounts/fireworks/models/llama-v3p1-70b-chat",
    ],
    "ollama": [
        "llama3",
        "qwen2.5",
        "deepseek-r1",
    ],
    "vllm": [],
    "openai_compatible": [],
}


def get_config_home() -> Path:
    """获取 TermPilot 配置根目录。

    优先级：
    1. TERMPILOT_CONFIG_DIR
    2. ~/.termpilot
    """
    configured = os.environ.get("TERMPILOT_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".termpilot"


def get_settings_path() -> Path:
    """获取 settings.json 路径。"""
    return get_config_home() / "settings.json"


def ensure_settings_template() -> bool:
    """首次启动时创建 settings.json。交互式环境启动 wizard，非 TTY 写模板。"""
    path = get_settings_path()
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.stdin.isatty():
            run_setup_wizard()
        else:
            path.write_text(_SETTINGS_TEMPLATE, encoding="utf-8")
            logger.debug("created settings template at %s", path)
        return True
    except OSError:
        return False


def run_setup_wizard() -> None:
    """交互式 provider 选择向导。

    如果已有配置，默认选中当前 provider 并预填已有值。
    ESC 时优雅退出（不 sys.exit）。
    """
    import questionary

    settings_path = get_settings_path()

    # 检测当前 provider → 作为默认选中项
    raw_provider = _get_raw_provider(default="")
    current_label = None
    if raw_provider:
        label, _ = _find_provider_info(raw_provider)
        current_label = label

    # 读取已有配置 → 预填值
    existing_env = get_settings_env()

    choice = _esc_ask(questionary.select(
        "Select your LLM provider:",
        choices=list(_PROVIDERS.keys()),
        default=current_label,
    ))
    if not choice:
        return

    info = _PROVIDERS[choice]
    env: dict[str, str] = {}

    # Prompt for API key — 预填已有值
    env_key = info.get("env_key")
    if env_key:
        existing_key = existing_env.get(env_key, "")
        api_key = _esc_ask(questionary.text(
            f"Enter your {env_key}:",
            default=existing_key,
        ))
        if not api_key:
            return
        env[env_key] = api_key

    # Prompt for base URL — 预填已有值
    base_url = info.get("base_url", "")
    if base_url == "":
        existing_url = existing_env.get(
            info.get("base_url_env_key") or f"{info['provider'].upper()}_BASE_URL", ""
        )
        base_url = _esc_ask(questionary.text(
            "Enter base URL (e.g. https://api.example.com/v1):",
            default=existing_url,
        )) or ""

    # Prompt for model — 预填已有值或使用 provider 默认
    model_env_key = info.get("model_env_key") or f"{info['provider'].upper()}_MODEL"
    existing_model = existing_env.get(model_env_key, "")
    default_model = existing_model or info.get("default_model", "")
    if not default_model:
        default_model = _esc_ask(questionary.text(
            "Enter model name:",
        )) or ""

    # Build env dict with base URL and model
    provider_name = info["provider"]
    if base_url:
        url_var = info.get("base_url_env_key") or f"{provider_name.upper()}_BASE_URL"
        env[url_var] = base_url
    if default_model:
        model_var = info.get("model_env_key") or f"{provider_name.upper()}_MODEL"
        env[model_var] = default_model

    settings = {
        "provider": info["provider"],
        "env": env,
    }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    console = Console()
    console.print(Panel(
        Text.from_markup(
            f"[green]Configuration saved![/]\n\n"
            f"Provider: [bold]{info['provider']}[/]\n"
            f"Model: [bold]{default_model or 'default'}[/]\n"
            f"Config: [dim]{settings_path}[/]"
        ),
        border_style="green",
    ))


def _get_raw_provider(default: str = "openai") -> str:
    """获取未归一化的 provider 名称。"""
    settings = get_settings()
    env = get_settings_env()
    provider = _first_nonempty(
        os.environ.get("TERMPILOT_PROVIDER"),
        env.get("TERMPILOT_PROVIDER"),
        settings.get("provider"),
        default,
    )
    return str(provider)


def _find_provider_info(raw_provider: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    for label, info in _PROVIDERS.items():
        if info["provider"] == raw_provider:
            return label, info
    return None, None


def _model_env_keys_for_provider(raw_provider: str) -> list[str]:
    """返回当前 provider 可能使用的模型键。"""
    keys: list[str] = []
    _, info = _find_provider_info(raw_provider)
    if info:
        model_env_key = info.get("model_env_key")
        if model_env_key:
            keys.append(model_env_key)
        else:
            keys.append(f"{raw_provider.upper()}_MODEL")

    normalized = _normalize_provider(raw_provider)
    if normalized == "anthropic":
        keys.append("ANTHROPIC_MODEL")
    elif normalized == "openai":
        keys.append("OPENAI_MODEL")
    else:
        keys.append("TERMPILOT_MODEL")

    # 去重，保持顺序
    result: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def save_model_selection(model: str, raw_provider: str | None = None) -> None:
    """保存当前模型选择。"""
    raw_provider = raw_provider or _get_raw_provider()
    settings = get_settings()
    env = settings.setdefault("env", {})
    if not isinstance(env, dict):
        env = {}
        settings["env"] = env

    settings["model"] = model
    for key in _model_env_keys_for_provider(raw_provider):
        env[key] = model

    settings_path = get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_model_picker() -> dict[str, Any]:
    """为当前 provider 选择模型。

    返回:
      {"changed": bool, "model": str, "provider": str}
    """
    import questionary

    raw_provider = _get_raw_provider()
    provider_label, _ = _find_provider_info(raw_provider)
    current_model = get_effective_model()

    presets = list(_MODEL_PRESETS.get(raw_provider, []))

    # 构建 choice 列表：presets 原样展示，cursor 通过 default 定位
    choices: list[Any] = []
    seen: set[str] = set()

    def add_choice(title: str, value: str) -> None:
        if value in seen:
            return
        seen.add(value)
        choices.append(questionary.Choice(title, value=value))

    # 如果当前模型不在 presets 中，加到顶部
    if current_model not in presets:
        add_choice(current_model, current_model)

    for preset in presets:
        add_choice(preset, preset)

    choices.append(questionary.Choice("Custom model…", value="__custom__"))

    choice = _esc_ask(questionary.select(
        f"Select model for {provider_label or raw_provider}:",
        choices=choices,
        default=current_model,
        use_shortcuts=False,
    ))

    if not choice:
        return {"changed": False, "model": current_model, "provider": raw_provider}

    if choice == "__custom__":
        custom_model = _esc_ask(questionary.text(
            "Enter model name:",
            default=current_model,
        ))
        if not custom_model:
            return {"changed": False, "model": current_model, "provider": raw_provider}
        choice = custom_model

    save_model_selection(choice, raw_provider=raw_provider)
    return {"changed": choice != current_model, "model": choice, "provider": raw_provider}


def is_placeholder_key(key: str | None) -> bool:
    """判断 API key 是否为占位符或空值。"""
    if not key or not key.strip():
        return True
    return "your-api-key" in key.lower()


def get_settings() -> dict[str, Any]:
    """读取 settings.json 配置。

    对应 TS 中读取 settings.json 的逻辑。
    settings.json 示例:
    {
      "env": {
        "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
        "ANTHROPIC_API_KEY": "xxx.xxx",
        "ANTHROPIC_MODEL": "glm-4-flash"
      }
    }
    """
    settings_path = get_settings_path()
    if not settings_path.exists():
        logger.debug("settings.json not found at %s", settings_path)
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        logger.debug("loaded settings.json: %d keys (%s)", len(data), ", ".join(data.keys()))
        return data
    except (json.JSONDecodeError, OSError):
        logger.debug("settings.json parse failed")
        return {}


def get_settings_env() -> dict[str, str]:
    """从 settings.json 中读取 env 字段。

    对应 TS utils/managedEnv.ts：
    从 settings.json 的 env 字段读取环境变量配置。
    """
    settings = get_settings()
    env: dict[str, str] = {}
    settings_env = settings.get("env", {})
    if isinstance(settings_env, dict):
        for key, value in settings_env.items():
            if isinstance(value, str):
                env[key] = value
    return env


def get_effective_provider(default: str = "openai") -> str:
    """获取当前有效 provider。

    优先级：
    1. 环境变量 TERMPILOT_PROVIDER
    2. settings.json 中的 env.TERMPILOT_PROVIDER
    3. settings.json 顶层 provider 字段
    4. 默认值

    未知 provider 默认按 openai_compatible 处理，这样便于对接大多数业内兼容接口。
    """
    settings = get_settings()
    env = get_settings_env()
    provider = _first_nonempty(
        os.environ.get("TERMPILOT_PROVIDER"),
        env.get("TERMPILOT_PROVIDER"),
        settings.get("provider"),
        default,
    )
    normalized = _normalize_provider(provider)
    logger.debug("effective provider: %s (raw=%s)", normalized, provider)
    return normalized


def apply_settings_env() -> None:
    """将 settings.json 中的 env 注入到 os.environ。

    对应 TS utils/managedEnv.ts applySafeConfigEnvironmentVariables()。

    把 settings.json 中的 env 字段写入 os.environ，
    使得后续创建 API 客户端时自动使用。
    这就是"只改 settings.json 就能切换 provider"的核心机制。
    """
    settings_env = get_settings_env()
    if settings_env:
        logger.debug("injecting %d env vars from settings: %s",
                     len(settings_env), ", ".join(settings_env.keys()))
    os.environ.update(settings_env)


def get_effective_api_key(provider: str | None = None) -> str | None:
    """获取有效的 API Key。

    对应 TS utils/auth.ts getAnthropicApiKey() +
    services/api/client.ts configureApiKeyHeaders()。

    TS 中 key 来源优先级：
    1. ANTHROPIC_API_KEY 环境变量
    2. ANTHROPIC_AUTH_TOKEN（Bearer token，TermPilot 用这个）
    3. apiKeyHelper（外部命令获取）
    4. OAuth token

    Python 版按 provider 读取不同变量，并保留常见别名兼容。
    """
    provider = _normalize_provider(provider or get_effective_provider())
    env = get_settings_env()
    if provider == "anthropic":
        candidates = [
            ("env:ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY")),
            ("settings:ANTHROPIC_API_KEY", env.get("ANTHROPIC_API_KEY")),
            ("env:ANTHROPIC_AUTH_TOKEN", os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            ("settings:ANTHROPIC_AUTH_TOKEN", env.get("ANTHROPIC_AUTH_TOKEN")),
        ]
    elif provider == "openai":
        candidates = [
            ("env:OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY")),
            ("settings:OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
            ("env:TERMPILOT_API_KEY", os.environ.get("TERMPILOT_API_KEY")),
            ("settings:TERMPILOT_API_KEY", env.get("TERMPILOT_API_KEY")),
        ]
    else:
        candidates = [
            ("env:TERMPILOT_API_KEY", os.environ.get("TERMPILOT_API_KEY")),
            ("settings:TERMPILOT_API_KEY", env.get("TERMPILOT_API_KEY")),
            ("env:OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY")),
            ("settings:OPENAI_API_KEY", env.get("OPENAI_API_KEY")),
            ("env:ZHIPU_API_KEY", os.environ.get("ZHIPU_API_KEY")),
            ("settings:ZHIPU_API_KEY", env.get("ZHIPU_API_KEY")),
            ("env:DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY")),
            ("settings:DEEPSEEK_API_KEY", env.get("DEEPSEEK_API_KEY")),
            ("env:DASHSCOPE_API_KEY", os.environ.get("DASHSCOPE_API_KEY")),
            ("settings:DASHSCOPE_API_KEY", env.get("DASHSCOPE_API_KEY")),
            ("env:MOONSHOT_API_KEY", os.environ.get("MOONSHOT_API_KEY")),
            ("settings:MOONSHOT_API_KEY", env.get("MOONSHOT_API_KEY")),
            ("env:SILICONFLOW_API_KEY", os.environ.get("SILICONFLOW_API_KEY")),
            ("settings:SILICONFLOW_API_KEY", env.get("SILICONFLOW_API_KEY")),
            ("env:OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY")),
            ("settings:OPENROUTER_API_KEY", env.get("OPENROUTER_API_KEY")),
            ("env:GROQ_API_KEY", os.environ.get("GROQ_API_KEY")),
            ("settings:GROQ_API_KEY", env.get("GROQ_API_KEY")),
            ("env:TOGETHER_API_KEY", os.environ.get("TOGETHER_API_KEY")),
            ("settings:TOGETHER_API_KEY", env.get("TOGETHER_API_KEY")),
            ("env:FIREWORKS_API_KEY", os.environ.get("FIREWORKS_API_KEY")),
            ("settings:FIREWORKS_API_KEY", env.get("FIREWORKS_API_KEY")),
            ("env:OLLAMA_API_KEY", os.environ.get("OLLAMA_API_KEY")),
            ("settings:OLLAMA_API_KEY", env.get("OLLAMA_API_KEY")),
            ("env:ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY")),
            ("settings:ANTHROPIC_API_KEY", env.get("ANTHROPIC_API_KEY")),
        ]

    source = None
    key = None
    for candidate_source, candidate_value in candidates:
        if candidate_value:
            source = candidate_source
            key = candidate_value
            break

    if key:
        logger.debug("API key found: source=%s, prefix=%s...", source, key[:6])
    else:
        logger.debug("no API key found in any source for provider=%s", provider)
    return key


def get_effective_base_url(provider: str | None = None) -> str | None:
    """获取有效的 API Base URL。

    支持 provider 专属变量、通用变量和 settings.json 顶层字段。
    对 openai_compatible 来说，base URL 通常是必需的。
    """
    provider = _normalize_provider(provider or get_effective_provider())
    settings = get_settings()
    env = get_settings_env()

    if provider == "anthropic":
        return _first_nonempty(
            os.environ.get("ANTHROPIC_BASE_URL"),
            env.get("ANTHROPIC_BASE_URL"),
            settings.get("base_url"),
        )
    if provider == "openai":
        return _first_nonempty(
            os.environ.get("OPENAI_BASE_URL"),
            env.get("OPENAI_BASE_URL"),
            os.environ.get("TERMPILOT_BASE_URL"),
            env.get("TERMPILOT_BASE_URL"),
            settings.get("base_url"),
        )
    return _first_nonempty(
        os.environ.get("TERMPILOT_BASE_URL"),
        env.get("TERMPILOT_BASE_URL"),
        os.environ.get("OPENAI_BASE_URL"),
        env.get("OPENAI_BASE_URL"),
        os.environ.get("ZHIPU_BASE_URL"),
        env.get("ZHIPU_BASE_URL"),
        os.environ.get("DEEPSEEK_BASE_URL"),
        env.get("DEEPSEEK_BASE_URL"),
        os.environ.get("DASHSCOPE_BASE_URL"),
        env.get("DASHSCOPE_BASE_URL"),
        os.environ.get("MOONSHOT_BASE_URL"),
        env.get("MOONSHOT_BASE_URL"),
        os.environ.get("SILICONFLOW_BASE_URL"),
        env.get("SILICONFLOW_BASE_URL"),
        os.environ.get("OPENROUTER_BASE_URL"),
        env.get("OPENROUTER_BASE_URL"),
        os.environ.get("GROQ_BASE_URL"),
        env.get("GROQ_BASE_URL"),
        os.environ.get("TOGETHER_BASE_URL"),
        env.get("TOGETHER_BASE_URL"),
        os.environ.get("FIREWORKS_BASE_URL"),
        env.get("FIREWORKS_BASE_URL"),
        os.environ.get("OLLAMA_BASE_URL"),
        env.get("OLLAMA_BASE_URL"),
        os.environ.get("ANTHROPIC_BASE_URL"),
        env.get("ANTHROPIC_BASE_URL"),
        settings.get("base_url"),
    )


def get_effective_model(default: str = "gpt-4o", provider: str | None = None) -> str:
    """获取默认模型。

    会按 provider 读取最自然的模型变量，同时保留旧变量名兼容。
    """
    provider = _normalize_provider(provider or get_effective_provider())
    settings = get_settings()
    env = get_settings_env()
    if provider == "anthropic":
        model = _first_nonempty(
            os.environ.get("ANTHROPIC_MODEL"),
            env.get("ANTHROPIC_MODEL"),
            os.environ.get("TERMPILOT_MODEL"),
            env.get("TERMPILOT_MODEL"),
            settings.get("model"),
            default,
        )
    else:
        model = _first_nonempty(
            os.environ.get("OPENAI_MODEL"),
            env.get("OPENAI_MODEL"),
            os.environ.get("TERMPILOT_MODEL"),
            env.get("TERMPILOT_MODEL"),
            os.environ.get("ZHIPU_MODEL"),
            env.get("ZHIPU_MODEL"),
            os.environ.get("DEEPSEEK_MODEL"),
            env.get("DEEPSEEK_MODEL"),
            os.environ.get("DASHSCOPE_MODEL"),
            env.get("DASHSCOPE_MODEL"),
            os.environ.get("MOONSHOT_MODEL"),
            env.get("MOONSHOT_MODEL"),
            os.environ.get("SILICONFLOW_MODEL"),
            env.get("SILICONFLOW_MODEL"),
            os.environ.get("OPENROUTER_MODEL"),
            env.get("OPENROUTER_MODEL"),
            os.environ.get("GROQ_MODEL"),
            env.get("GROQ_MODEL"),
            os.environ.get("TOGETHER_MODEL"),
            env.get("TOGETHER_MODEL"),
            os.environ.get("FIREWORKS_MODEL"),
            env.get("FIREWORKS_MODEL"),
            os.environ.get("OLLAMA_MODEL"),
            env.get("OLLAMA_MODEL"),
            os.environ.get("ANTHROPIC_MODEL"),
            env.get("ANTHROPIC_MODEL"),
            settings.get("model"),
            default,
        )
    logger.debug("effective model: %s (default=%s)", model, default)
    return model


def get_context_window() -> int:
    """获取上下文窗口大小（tokens）。

    用于上下文压缩的阈值计算。
    默认 200000。
    """
    return int(
        os.environ.get("TERMPILOT_CONTEXT_WINDOW", "")
        or get_settings_env().get("TERMPILOT_CONTEXT_WINDOW", "")
        or "200000"
    )
