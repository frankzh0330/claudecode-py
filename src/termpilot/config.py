"""配置管理。

对应 TS: utils/config.ts + utils/managedEnv.ts

核心机制：
1. 读取 ~/.termpilot/settings.json
2. 其中的 env 字段包含环境变量
3. 将 env 注入到 os.environ
4. 后续创建 API 客户端时自动使用这些环境变量

Python 版在此基础上额外支持显式 provider 选择。

交互式配置向导只展示少量常用 provider：Anthropic、OpenAI、
Zhipu GLM、DeepSeek、Seed。底层仍保留常见 OpenAI-compatible
变量名兼容，避免已有配置立即失效。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from termpilot.prompt_utils import ask_with_esc

logger = logging.getLogger(__name__)


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
    "seed": "openai_compatible",
    "doubao": "openai_compatible",
    "volcengine": "openai_compatible",
    "ark": "openai_compatible",
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


def _raw_provider_for_lookup(provider: str | None = None) -> str:
    """Return the unnormalized provider so compatible env vars can be ordered."""
    if provider:
        return str(provider).strip().lower()
    settings = get_settings()
    env = get_settings_env()
    return str(
        os.environ.get("TERMPILOT_PROVIDER")
        or env.get("TERMPILOT_PROVIDER")
        or settings.get("provider")
        or ""
    ).strip().lower()


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
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
    },
    "Seed": {
        "provider": "seed",
        "env_key": "ARK_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-2-0-code-preview-260215",
        "base_url_env_key": "ARK_BASE_URL",
        "model_env_key": "ARK_MODEL",
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
        "deepseek-v4-pro",
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "qwen": [
        "qwen-plus",
        "qwen-max",
        "qwen-turbo",
    ],
    "seed": [
        "doubao-seed-2-0-code-preview-260215",
        "doubao-seed-2.0-pro",
        "doubao-seed-2.0-lite",
        "doubao-seed-code",
        "ark-code-latest",
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


_COMPAT_API_KEY_CANDIDATES: dict[str, list[str]] = {
    "zhipu": ["ZHIPU_API_KEY"],
    "glm": ["ZHIPU_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "qwen": ["DASHSCOPE_API_KEY"],
    "dashscope": ["DASHSCOPE_API_KEY"],
    "seed": ["ARK_API_KEY", "VOLCANO_ENGINE_API_KEY", "SEED_API_KEY"],
    "doubao": ["ARK_API_KEY", "VOLCANO_ENGINE_API_KEY", "SEED_API_KEY"],
    "volcengine": ["ARK_API_KEY", "VOLCANO_ENGINE_API_KEY", "SEED_API_KEY"],
    "ark": ["ARK_API_KEY", "VOLCANO_ENGINE_API_KEY", "SEED_API_KEY"],
    "moonshot": ["MOONSHOT_API_KEY"],
    "kimi": ["MOONSHOT_API_KEY"],
    "siliconflow": ["SILICONFLOW_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "ollama": ["OLLAMA_API_KEY"],
    "vllm": ["VLLM_API_KEY"],
}

_COMPAT_BASE_URL_CANDIDATES: dict[str, list[str]] = {
    "zhipu": ["ZHIPU_BASE_URL"],
    "glm": ["ZHIPU_BASE_URL"],
    "deepseek": ["DEEPSEEK_BASE_URL"],
    "qwen": ["DASHSCOPE_BASE_URL"],
    "dashscope": ["DASHSCOPE_BASE_URL"],
    "seed": ["ARK_BASE_URL", "VOLCANO_ENGINE_BASE_URL", "SEED_BASE_URL"],
    "doubao": ["ARK_BASE_URL", "VOLCANO_ENGINE_BASE_URL", "SEED_BASE_URL"],
    "volcengine": ["ARK_BASE_URL", "VOLCANO_ENGINE_BASE_URL", "SEED_BASE_URL"],
    "ark": ["ARK_BASE_URL", "VOLCANO_ENGINE_BASE_URL", "SEED_BASE_URL"],
    "moonshot": ["MOONSHOT_BASE_URL"],
    "kimi": ["MOONSHOT_BASE_URL"],
    "siliconflow": ["SILICONFLOW_BASE_URL"],
    "openrouter": ["OPENROUTER_BASE_URL"],
    "groq": ["GROQ_BASE_URL"],
    "together": ["TOGETHER_BASE_URL"],
    "fireworks": ["FIREWORKS_BASE_URL"],
    "ollama": ["OLLAMA_BASE_URL"],
    "vllm": ["VLLM_BASE_URL"],
}

_COMPAT_MODEL_CANDIDATES: dict[str, list[str]] = {
    "zhipu": ["ZHIPU_MODEL"],
    "glm": ["ZHIPU_MODEL"],
    "deepseek": ["DEEPSEEK_MODEL"],
    "qwen": ["DASHSCOPE_MODEL"],
    "dashscope": ["DASHSCOPE_MODEL"],
    "seed": ["ARK_MODEL", "VOLCANO_ENGINE_MODEL", "SEED_MODEL"],
    "doubao": ["ARK_MODEL", "VOLCANO_ENGINE_MODEL", "SEED_MODEL"],
    "volcengine": ["ARK_MODEL", "VOLCANO_ENGINE_MODEL", "SEED_MODEL"],
    "ark": ["ARK_MODEL", "VOLCANO_ENGINE_MODEL", "SEED_MODEL"],
    "moonshot": ["MOONSHOT_MODEL"],
    "kimi": ["MOONSHOT_MODEL"],
    "siliconflow": ["SILICONFLOW_MODEL"],
    "openrouter": ["OPENROUTER_MODEL"],
    "groq": ["GROQ_MODEL"],
    "together": ["TOGETHER_MODEL"],
    "fireworks": ["FIREWORKS_MODEL"],
    "ollama": ["OLLAMA_MODEL"],
    "vllm": ["VLLM_MODEL"],
}


def _ordered_compat_env_keys(raw_provider: str, mapping: dict[str, list[str]], common: list[str]) -> list[str]:
    """Prefer env keys for the selected compatible provider, then fall back."""
    ordered: list[str] = []
    for key in mapping.get(raw_provider, []):
        if key not in ordered:
            ordered.append(key)
    for key in common:
        if key not in ordered:
            ordered.append(key)
    for keys in mapping.values():
        for key in keys:
            if key not in ordered:
                ordered.append(key)
    return ordered


def _env_candidates(keys: list[str], env: dict[str, Any]) -> list[tuple[str, str | None]]:
    candidates: list[tuple[str, str | None]] = []
    for key in keys:
        candidates.append((f"env:{key}", os.environ.get(key)))
        candidates.append((f"settings:{key}", env.get(key)))
    return candidates


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

    choice = ask_with_esc(questionary.select(
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
        api_key = ask_with_esc(questionary.text(
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
        base_url = ask_with_esc(questionary.text(
            "Enter base URL (e.g. https://api.example.com/v1):",
            default=existing_url,
        )) or ""

    # Prompt for model — 预填已有值或使用 provider 默认
    model_env_key = info.get("model_env_key") or f"{info['provider'].upper()}_MODEL"
    existing_model = existing_env.get(model_env_key, "")
    default_model = existing_model or info.get("default_model", "")
    if not default_model:
        default_model = ask_with_esc(questionary.text(
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

    choice = ask_with_esc(questionary.select(
        f"Select model for {provider_label or raw_provider}:",
        choices=choices,
        default=current_model,
        use_shortcuts=False,
    ))

    if not choice:
        return {"changed": False, "model": current_model, "provider": raw_provider}

    if choice == "__custom__":
        custom_model = ask_with_esc(questionary.text(
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
    raw_provider = _raw_provider_for_lookup(provider)
    provider = _normalize_provider(raw_provider or provider or get_effective_provider())
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
        keys = _ordered_compat_env_keys(
            raw_provider,
            _COMPAT_API_KEY_CANDIDATES,
            ["TERMPILOT_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
        )
        candidates = _env_candidates(keys, env)

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
    raw_provider = _raw_provider_for_lookup(provider)
    provider = _normalize_provider(raw_provider or provider or get_effective_provider())
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
    keys = _ordered_compat_env_keys(
        raw_provider,
        _COMPAT_BASE_URL_CANDIDATES,
        ["TERMPILOT_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"],
    )
    return _first_nonempty(*[value for _, value in _env_candidates(keys, env)], settings.get("base_url"))


def get_effective_model(default: str = "gpt-4o", provider: str | None = None) -> str:
    """获取默认模型。

    会按 provider 读取最自然的模型变量，同时保留旧变量名兼容。
    """
    raw_provider = _raw_provider_for_lookup(provider)
    provider = _normalize_provider(raw_provider or provider or get_effective_provider())
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
        keys = _ordered_compat_env_keys(
            raw_provider,
            _COMPAT_MODEL_CANDIDATES,
            ["OPENAI_MODEL", "TERMPILOT_MODEL", "ANTHROPIC_MODEL"],
        )
        model = _first_nonempty(*[value for _, value in _env_candidates(keys, env)], settings.get("model"), default)
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
