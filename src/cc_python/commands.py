"""Slash Commands 系统。

对应 TS:
- utils/slashCommandParsing.ts (parseSlashCommand)
- utils/processUserInput/processSlashCommand.tsx (processSlashCommand)
- commands/ (各命令实现)

Slash 命令是用户以 / 开头的特殊输入，在发送给模型之前被拦截和处理。
支持内置命令（help, compact, clear, config, skills, mcp, exit）和 skill 命令。

流程：
  用户输入 "/command args"
    │
    ▼
  parse_slash_command() → {name, args}
    │
    ▼
  dispatch_command() → 查找命令 → 执行 handler
    │
    ├─ 内置命令 → 直接执行，返回结果
    └─ skill 命令 → 返回 skill prompt，由模型处理
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# 命令 handler 类型：接收 (args, context) → CommandResult
CommandHandler = Callable[..., Awaitable["CommandResult"]]


@dataclass
class CommandResult:
    """命令执行结果。"""
    output: str = ""           # 输出文本
    should_query: bool = False  # 是否将结果发送给模型
    new_messages: list | None = None  # 要注入的消息（可选）
    exit_repl: bool = False    # 是否退出 REPL


@dataclass
class Command:
    """命令定义。"""
    name: str
    description: str
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    argument_hint: str = ""
    is_hidden: bool = False


# 全局命令注册表
_commands: dict[str, Command] = {}


def parse_slash_command(input_text: str) -> tuple[str, str] | None:
    """解析 slash 命令。

    对应 TS: utils/slashCommandParsing.ts parseSlashCommand()

    返回 (command_name, args) 或 None（如果不是 slash 命令）。

    示例：
    - "/help" → ("help", "")
    - "/compact full" → ("compact", "full")
    - "/mcp" → ("mcp", "")
    """
    text = input_text.strip()
    if not text.startswith("/"):
        return None

    without_slash = text[1:]
    if not without_slash:
        return None

    parts = without_slash.split(None, 1)
    command_name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    return command_name, args


def register_command(cmd: Command) -> None:
    """注册命令。"""
    _commands[cmd.name] = cmd
    for alias in cmd.aliases:
        _commands[alias] = cmd


def find_command(name: str) -> Command | None:
    """按名称或别名查找命令。"""
    return _commands.get(name)


def get_all_commands() -> list[Command]:
    """获取所有已注册命令（去重）。"""
    seen = set()
    result = []
    for cmd in _commands.values():
        if cmd.name not in seen:
            seen.add(cmd.name)
            result.append(cmd)
    return result


async def dispatch_command(
    name: str,
    args: str,
    context: dict[str, Any] | None = None,
) -> CommandResult:
    """分派命令执行。

    对应 TS: processSlashCommand.tsx 中的命令执行逻辑。
    """
    cmd = find_command(name)
    if not cmd:
        logger.debug("unknown command: /%s", name)
        return CommandResult(
            output=f"Unknown command: /{name}\nType /help for available commands.",
        )

    try:
        result = await cmd.handler(args, context or {})
        logger.debug("command /%s completed: output=%d chars, exit_repl=%s", name, len(result.output), result.exit_repl)
        return result
    except Exception as e:
        logger.debug("command /%s error: %s", name, e)
        return CommandResult(output=f"Command error: {e}")


# ── 内置命令实现 ──────────────────────────────────────────


async def _cmd_help(args: str, ctx: dict) -> CommandResult:
    """显示帮助信息。"""
    lines = ["Available commands:", ""]

    commands = get_all_commands()
    for cmd in sorted(commands, key=lambda c: c.name):
        if cmd.is_hidden:
            continue
        aliases = f" ({', '.join(f'/{a}' for a in cmd.aliases)})" if cmd.aliases else ""
        hint = f" {cmd.argument_hint}" if cmd.argument_hint else ""
        lines.append(f"  /{cmd.name}{hint}{aliases} — {cmd.description}")

    lines.append("")
    lines.append("Tip: You can also invoke skills via /skill-name")

    return CommandResult(output="\n".join(lines))


async def _cmd_compact(args: str, ctx: dict) -> CommandResult:
    """手动触发上下文压缩。"""
    messages = ctx.get("messages", [])
    if not messages:
        return CommandResult(output="No messages to compact.")

    # 调用压缩
    from cc_python.compact import auto_compact_if_needed, estimate_tokens
    from cc_python.config import get_context_window

    context_window = get_context_window()
    system_prompt = ctx.get("system_prompt", "")

    tokens_before = estimate_tokens(messages, system_prompt)

    client = ctx.get("client")
    client_format = ctx.get("client_format", "anthropic")
    model = ctx.get("model", "")

    if not client:
        return CommandResult(output="Cannot compact: API client not available.")

    compacted = await auto_compact_if_needed(
        messages, system_prompt,
        client, client_format, model,
        context_window=context_window,
        force=True,  # 强制压缩
    )

    tokens_after = estimate_tokens(compacted, system_prompt)
    saved = tokens_before - tokens_after

    return CommandResult(
        output=f"Context compacted: {tokens_before:,} → {tokens_after:,} tokens (saved {saved:,})",
        should_query=True,
        new_messages=compacted,
    )


async def _cmd_clear(args: str, ctx: dict) -> CommandResult:
    """清除对话历史。"""
    return CommandResult(
        output="Conversation cleared.",
        should_query=False,
        new_messages=[],  # 空列表表示清除
    )


async def _cmd_config(args: str, ctx: dict) -> CommandResult:
    """显示当前配置。"""
    from cc_python.config import (
        get_effective_api_key,
        get_effective_base_url,
        get_effective_model,
        get_context_window,
        get_settings,
    )

    settings = get_settings()
    api_key = get_effective_api_key()
    base_url = get_effective_base_url()
    model = get_effective_model()
    context_window = get_context_window()

    # 脱敏 API key
    masked_key = "not set"
    if api_key:
        if len(api_key) > 8:
            masked_key = api_key[:4] + "..." + api_key[-4:]
        else:
            masked_key = "***"

    lines = [
        "Current configuration:",
        f"  Model: {model}",
        f"  API Key: {masked_key}",
        f"  Base URL: {base_url or 'default (Anthropic)'}",
        f"  Context Window: {context_window:,} tokens",
        f"  MCP Servers: {len(settings.get('mcpServers', {}))} configured",
    ]

    mcp_servers = settings.get("mcpServers", {})
    for name, config in mcp_servers.items():
        server_type = config.get("type", "stdio")
        if server_type == "stdio":
            lines.append(f"    - {name} (stdio): {config.get('command', '?')}")
        elif server_type == "sse":
            lines.append(f"    - {name} (sse): {config.get('url', '?')}")

    return CommandResult(output="\n".join(lines))


async def _cmd_skills(args: str, ctx: dict) -> CommandResult:
    """列出可用 skills。"""
    from cc_python.skills import get_all_skills

    skills = get_all_skills()
    if not skills:
        return CommandResult(output="No skills available. Create .claude/skills/*.md to add custom skills.")

    lines = ["Available skills:", ""]
    for skill in sorted(skills, key=lambda s: s.name):
        source = f" [{skill.source}]" if skill.source != "disk" else ""
        lines.append(f"  /{skill.name} — {skill.description}{source}")

    return CommandResult(output="\n".join(lines))


async def _cmd_mcp(args: str, ctx: dict) -> CommandResult:
    """显示 MCP 服务器状态。"""
    mcp_manager = ctx.get("mcp_manager")
    if not mcp_manager:
        return CommandResult(output="MCP not initialized.")

    from cc_python.mcp.config import get_mcp_configs
    configs = get_mcp_configs()

    if not configs:
        return CommandResult(output="No MCP servers configured.\n\nAdd to ~/.claude/settings.json:\n" + json.dumps({
            "mcpServers": {
                "example": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                }
            }
        }, indent=2))

    lines = ["MCP Servers:", ""]

    for name, config in configs.items():
        server_type = config.get("type", "stdio")
        client = mcp_manager._clients.get(name)

        if client and client.is_connected:
            status = "connected"
            tool_count = len(client.tools)
            resource_count = len(client.resources)
            info = f"{tool_count} tools, {resource_count} resources"
            server_info = client.server_info
            if server_info:
                info += f" ({server_info.get('name', '?')} v{server_info.get('version', '?')})"
        elif client:
            status = "failed"
            info = "connection failed"
        else:
            status = "not loaded"
            info = ""

        lines.append(f"  {name} ({server_type}): {status}")
        if info:
            lines.append(f"    {info}")

    return CommandResult(output="\n".join(lines))


async def _cmd_exit(args: str, ctx: dict) -> CommandResult:
    """退出程序。"""
    return CommandResult(exit_repl=True)


# ── 注册内置命令 ──────────────────────────────────────────

def register_builtin_commands() -> None:
    """注册所有内置命令。"""
    register_command(Command(
        name="help",
        description="Show available commands",
        handler=_cmd_help,
        aliases=["?"],
    ))
    register_command(Command(
        name="compact",
        description="Manually trigger context compression",
        handler=_cmd_compact,
        argument_hint="[force]",
    ))
    register_command(Command(
        name="clear",
        description="Clear conversation history",
        handler=_cmd_clear,
    ))
    register_command(Command(
        name="config",
        description="Show current configuration",
        handler=_cmd_config,
    ))
    register_command(Command(
        name="skills",
        description="List available skills",
        handler=_cmd_skills,
    ))
    register_command(Command(
        name="mcp",
        description="Show MCP server status",
        handler=_cmd_mcp,
    ))
    register_command(Command(
        name="exit",
        description="Exit the program",
        handler=_cmd_exit,
        aliases=["quit", "q"],
        is_hidden=True,  # 已通过 Ctrl+C 支持
    ))


# 模块加载时自动注册
register_builtin_commands()
