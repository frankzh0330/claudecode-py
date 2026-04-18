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
    output: str = ""  # 输出文本
    should_query: bool = False  # 是否将结果发送给模型
    new_messages: list | None = None  # 要注入的消息（可选）
    exit_repl: bool = False  # 是否退出 REPL


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


def _looks_like_command(name: str) -> bool:
    """判断字符串是否像合法命令名。

    对应 TS: processSlashCommand.tsx looksLikeCommand()。
    合法字符：字母、数字、冒号、连字符、下划线。
    包含其他字符（如 / . 等）则可能是文件路径。
    """
    import re
    return not bool(re.search(r"[^a-zA-Z0-9:\-_]", name))


async def dispatch_command(
        name: str,
        args: str,
        context: dict[str, Any] | None = None,
) -> CommandResult:
    """分派命令执行。

    对应 TS: processSlashCommand.tsx 中的命令执行逻辑。

    查找优先级（与 TS 对齐）：
    1. 内置命令（help, compact, clear 等）
    2. Skill 回退（/review → 查找 skill "review"）
    3. 未知命令
    """
    # 1. 查找内置命令
    cmd = find_command(name)
    if cmd:
        try:
            result = await cmd.handler(args, context or {})
            logger.debug("command /%s completed: output=%d chars, exit_repl=%s", name, len(result.output),
                         result.exit_repl)
            return result
        except Exception as e:
            logger.debug("command /%s error: %s", name, e)
            return CommandResult(output=f"Command error: {e}")

    # 2. Skill 回退（对应 TS: hasCommand → getCommand 查找 prompt 类型命令）
    from termpilot.skills import find_skill
    skill = find_skill(name)
    if skill:
        # 检查 userInvocable（对应 TS: command.userInvocable === false 的处理）
        if not skill.user_invocable:
            return CommandResult(
                output=f'This skill can only be invoked by Claude, not directly by users. '
                       f'Ask Claude to use the "{name}" skill for you.',
            )

        prompt = skill.get_prompt(args)
        logger.debug("skill /%s invoked: prompt=%d chars", name, len(prompt))
        return CommandResult(
            output=prompt,
            should_query=True,
        )

    # 3. 未知命令（对应 TS: looksLikeCommand 检查）
    logger.debug("unknown command: /%s", name)
    if _looks_like_command(name):
        return CommandResult(
            output=f"Unknown command: /{name}\nType /help for available commands.",
        )
    # 看起来像文件路径，提示用户
    return CommandResult(
        output=f"Unknown command: /{name}\n(Did you mean to type this without the / prefix?)",
    )


# ── 内置命令实现 ──────────────────────────────────────────


async def _cmd_help(args: str, ctx: dict) -> CommandResult:
    """显示帮助信息。"""
    lines = ["**Available commands:**", ""]

    commands = get_all_commands()
    for cmd in sorted(commands, key=lambda c: c.name):
        if cmd.is_hidden:
            continue
        aliases = f" ({', '.join(f'/{a}' for a in cmd.aliases)})" if cmd.aliases else ""
        hint = f" {cmd.argument_hint}" if cmd.argument_hint else ""
        lines.append(f"- `/{cmd.name}{hint}{aliases}` — {cmd.description}")

    # 列出 user-invocable skills（对应 TS: 命令和 skill 统一展示）
    from termpilot.skills import get_all_skills
    skills = [s for s in get_all_skills() if s.user_invocable]
    if skills:
        lines.append("")
        lines.append("**Available skills:**")
        for skill in sorted(skills, key=lambda s: s.name):
            lines.append(f"- `/{skill.name}` — {skill.description}")

    return CommandResult(output="\n".join(lines))


async def _cmd_compact(args: str, ctx: dict) -> CommandResult:
    """手动触发上下文压缩。"""
    messages = ctx.get("messages", [])
    if not messages:
        return CommandResult(output="No messages to compact.")

    # 调用压缩
    from termpilot.compact import auto_compact_if_needed, estimate_tokens
    from termpilot.config import get_context_window

    context_window = get_context_window()
    system_prompt = ctx.get("system_prompt", "")

    tokens_before = estimate_tokens(messages, system_prompt)

    client = ctx.get("client")
    model = ctx.get("model", "")

    if not client:
        return CommandResult(output="Cannot compact: API client not available.")

    compacted = await auto_compact_if_needed(
        messages, system_prompt,
        client, model,
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
    from termpilot.config import (
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


async def _cmd_details(args: str, ctx: dict) -> CommandResult:
    """Show the full output of a recent tool call."""
    ui = ctx.get("ui")
    if ui is None:
        return CommandResult(output="Tool details are not available in this session.")

    token = args.strip() or "last"
    return CommandResult(output=ui.format_tool_details(token))


async def _cmd_skills(args: str, ctx: dict) -> CommandResult:
    """列出可用 skills。"""
    from termpilot.skills import get_all_skills

    skills = get_all_skills()
    if not skills:
        return CommandResult(output="No skills available. Create .claude/skills/*.md or ~/.termpilot/skills/*.md to add custom skills.")

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

    from termpilot.mcp.config import get_mcp_configs
    from termpilot.config import get_settings_path
    configs = get_mcp_configs()

    if not configs:
        return CommandResult(output=f"No MCP servers configured.\n\nAdd to {get_settings_path()}:\n" + json.dumps({
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


# ── /undo ──────────────────────────────────────────────


async def _cmd_undo(args: str, ctx: dict) -> CommandResult:
    """回退最近一次文件修改。

    对应 TS 的 undo 功能：弹出快照栈顶，恢复文件到修改前的状态。
    """
    from termpilot.undo import pop_snapshot, has_snapshots

    if not has_snapshots():
        return CommandResult(output="Nothing to undo. No file snapshots available.")

    snapshot = pop_snapshot()
    path = snapshot["path"]
    content = snapshot["content"]

    if content is None:
        # 文件修改前不存在 → 删除它（恢复为"不存在"状态）
        import asyncio
        from pathlib import Path
        p = Path(path)
        if p.exists():
            try:
                await asyncio.to_thread(p.unlink)
                return CommandResult(output=f"Undone: deleted {path} (file was created by the last operation)")
            except OSError as e:
                return CommandResult(output=f"Undo failed: cannot delete {path}: {e}")
        return CommandResult(output=f"Undone: {path} (file doesn't exist, nothing to restore)")
    else:
        # 文件修改前存在 → 恢复内容
        import asyncio
        from pathlib import Path
        p = Path(path)
        try:
            await asyncio.to_thread(p.write_text, content, "utf-8")
            return CommandResult(output=f"Undone: restored {path} to previous state ({len(content)} chars)")
        except OSError as e:
            return CommandResult(output=f"Undo failed: cannot write {path}: {e}")


# ── /commit ────────────────────────────────────────────

_COMMIT_PROMPT = """\
Analyze the following git changes and create a commit.

## Current git status
```
{status}
```

## Staged changes
```
{staged_diff}
```

## Unstaged changes
```
{unstaged_diff}
```

## Recent commit messages
```
{recent_logs}
```

## Instructions
Follow these steps carefully:

1. Run `git status` and `git diff` to see both staged and unstaged changes that will be committed.
2. Analyze all changes and draft a clear, concise commit message that:
   - Uses the imperative mood (e.g., "Add feature" not "Added feature")
   - Focuses on the "why" rather than the "what"
   - Follows the existing commit message style shown above
3. If there are no staged changes, stage the relevant files with `git add`.
4. Create the commit using a HEREDOC format like this:
```bash
git commit -m "$(cat <<'EOF'
   <commit message here>
   EOF
   )
```
5. Run `git status` after the commit to verify success.

IMPORTANT:
- NEVER use --no-verify or --no-gpg-sign flags
- NEVER run git push unless the user explicitly asks
- Only commit files that are relevant to the changes
- If the changes are trivial, use a simple one-line commit message"""


async def _cmd_commit(args: str, ctx: dict) -> CommandResult:
    """读取 git 变更信息，构造 prompt 让 AI 生成并执行 commit。

    对应 TS: commands/commit.ts — prompt-based 命令。
    """
    import asyncio

    async def _run_git(cmd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *cmd.split(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode("utf-8", errors="replace").strip()

    # 并行读取 git 信息
    status, staged_diff, unstaged_diff, recent_logs = await asyncio.gather(
        _run_git("status --short"),
        _run_git("diff --staged"),
        _run_git("diff"),
        _run_git("log --oneline -5"),
    )

    # 检查是否有任何变更
    if not status and not staged_diff and not unstaged_diff:
        return CommandResult(output="No changes to commit. Working tree is clean.")

    prompt = _COMMIT_PROMPT.format(
        status=status or "(empty)",
        staged_diff=staged_diff or "(no staged changes)",
        unstaged_diff=unstaged_diff or "(no unstaged changes)",
        recent_logs=recent_logs or "(no recent commits)",
    )

    return CommandResult(output=prompt, should_query=True)


# ── /init ──────────────────────────────────────────────

_INIT_PROMPT = """\
Analyze this project and create a CLAUDE.md file.

## Project root: {project_root}

## Directory structure
```
{dir_listing}
```

## Existing CLAUDE.md
```
{existing_claudemd}
```

## Project configuration files
```
{config_files}
```

## Instructions
1. Analyze the project structure, dependencies, and configuration to understand:
   - What the project does
   - Tech stack and frameworks used
   - How to build, test, and run it
   - Key directories and their purposes
2. Create a CLAUDE.md file at the project root that includes:
   - Project name and brief description
   - Tech stack
   - How to run, build, and test
   - Project structure overview
   - Any notable conventions or patterns
3. Use the Write tool to create/update the CLAUDE.md file.
4. Keep the CLAUDE.md concise and practical — focus on information that helps an AI assistant work effectively in this codebase.
5. Write in the same language as the existing documentation (or English if unclear)."""


async def _cmd_init(args: str, ctx: dict) -> CommandResult:
    """分析项目并生成 CLAUDE.md。

    对应 TS: commands/init.ts — prompt-based 命令。
    """
    import asyncio
    from pathlib import Path

    project_root = str(Path.cwd())

    # 读取目录结构
    async def _run(cmd: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd.split(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode("utf-8", errors="replace").strip()

    dir_listing = await _run("ls -la")

    # 读取现有 CLAUDE.md
    claudemd_path = Path("CLAUDE.md")
    existing = ""
    if claudemd_path.exists():
        existing = claudemd_path.read_text(encoding="utf-8")

    # 读取项目配置文件
    config_parts = []
    for config_name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile"):
        config_path = Path(config_name)
        if config_path.exists():
            content = config_path.read_text(encoding="utf-8")
            # 截断过长的配置
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated)"
            config_parts.append(f"--- {config_name} ---\n{content}")

    prompt = _INIT_PROMPT.format(
        project_root=project_root,
        dir_listing=dir_listing or "(empty)",
        existing_claudemd=existing or "(does not exist)",
        config_files="\n".join(config_parts) if config_parts else "(no config files found)",
    )

    return CommandResult(output=prompt, should_query=True)


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
        name="details",
        description="Show full output for a recent tool result",
        handler=_cmd_details,
        argument_hint="[last|n]",
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
    register_command(Command(
        name="undo",
        description="Undo the last file modification",
        handler=_cmd_undo,
    ))
    register_command(Command(
        name="commit",
        description="Create a git commit with AI-generated message",
        handler=_cmd_commit,
    ))
    register_command(Command(
        name="init",
        description="Generate CLAUDE.md for the current project",
        handler=_cmd_init,
    ))


# 模块加载时自动注册
register_builtin_commands()
