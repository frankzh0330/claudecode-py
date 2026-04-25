"""CLI 入口。

对应 TS: main.tsx (CLI 参数解析、启动逻辑) + entrypoints/cli.tsx
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from termpilot.api import create_client, query_with_tools
from termpilot.attachments import process_attachments
from termpilot.commands import dispatch_command, parse_slash_command
from termpilot.config import ensure_settings_template, get_config_home, get_effective_model
from termpilot.context import build_system_prompt
from termpilot.hooks import HookEvent, dispatch_hooks
from termpilot.mcp import MCPManager
from termpilot.messages import create_assistant_message, create_user_message
from termpilot.permissions import (
    PermissionBehavior,
    PermissionContext,
    PermissionMode,
    PermissionResult,
    build_permission_context,
)
from termpilot.session import SessionStorage, list_sessions, load_session
from termpilot.skills import discover_and_load_skills
from termpilot.tools import get_all_tools
from termpilot.ui import QuietUI

DEFAULT_MODEL = "gpt-4o"

console = Console()
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


async def _permission_prompt(
        tool_name: str,
        tool_input: dict,
        message: str,
        ui: QuietUI | None = None,
) -> PermissionResult:
    """权限确认提示。对应 TS useCanUseTool.tsx 的用户交互部分。"""
    import questionary

    if ui:
        ui.clear_status()
    console.print()
    console.rule("[bold yellow]权限请求[/]")
    console.print(f"[bold]{tool_name}[/] — {message}")

    # 显示操作摘要
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        console.print(f"  [dim]命令:[/] {cmd[:200]}")
    elif tool_name in ("write_file", "edit_file"):
        console.print(f"  [dim]文件:[/] {tool_input.get('file_path', '')}")

    console.print()

    try:
        loop = asyncio.get_event_loop()
        choice = await loop.run_in_executor(
            None,
            lambda: _esc_ask(questionary.select(
                "选择操作",
                choices=[
                    questionary.Choice("Allow once    (本次允许)", value="1"),
                    questionary.Choice("Always allow  (始终允许同类操作)", value="2"),
                    questionary.Choice("Deny          (拒绝)", value="3"),
                    questionary.Choice("Always deny   (始终拒绝同类操作)", value="4"),
                ],
                use_shortcuts=False,
            )),
        )
    except (KeyboardInterrupt, EOFError):
        choice = "3"

    if choice == "1":
        return PermissionResult(behavior=PermissionBehavior.ALLOW)
    elif choice == "2":
        return PermissionResult(
            behavior=PermissionBehavior.ALLOW,
            rule_updates=[{
                "tool_name": tool_name,
                "pattern": "*",
                "behavior": "allow",
            }],
        )
    elif choice == "3":
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message="用户拒绝",
        )
    else:
        return PermissionResult(
            behavior=PermissionBehavior.DENY,
            message="用户拒绝",
            rule_updates=[{
                "tool_name": tool_name,
                "pattern": "*",
                "behavior": "deny",
            }],
        )


async def _stream_response_with_tools(
        client: Any,
        model: str,
        system_prompt: str,
        messages: list[dict],
        tools: list,
        storage: SessionStorage | None = None,
        permission_context: PermissionContext | None = None,
        session_id: str = "",
        cost_tracker: Any | None = None,
        ui: QuietUI | None = None,
        client_format: str = "openai",
) -> str:
    """带工具调用的流式响应。

    对应 TS query.ts 主循环 + REPL.tsx 的渲染逻辑。
    使用回调实现实时渲染：
    - on_text: 文本流实时渲染 Markdown
    - on_tool_call: 显示工具调用过程和结果
    """
    logger.debug("_stream_response_with_tools: model=%s, messages=%d, tools=%d",
                 model, len(messages), len(tools))
    full_response = ""

    def on_text(chunk: str) -> None:
        nonlocal full_response
        full_response += chunk

    def on_tool_call(name: str, input_data: dict, result: str) -> None:
        # 记录工具调用到 session
        if storage:
            storage.record_tool_call(name, input_data, result)

    def on_assistant_message(text: str, tool_calls: list) -> None:
        # 记录 assistant 中间消息到 session（确保崩溃可恢复）
        if storage and text and text.strip():
            storage.record_assistant_message(text)

    def on_event(event: dict[str, Any]) -> None:
        if ui:
            ui.handle_event(event)

    if ui:
        ui.handle_event({"type": "status_started", "text": "Coalescing…"})
    try:
        full_response = await query_with_tools(
            client=client,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            on_text=on_text,
            on_tool_call=on_tool_call,
            on_event=on_event,
            permission_context=permission_context,
            on_permission_ask=lambda tn, ti, m: _permission_prompt(tn, ti, m, ui=ui),
            session_id=session_id,
            cost_tracker=cost_tracker,
            client_format=client_format,
            on_assistant_message=on_assistant_message,
        )
    except Exception:
        if ui:
            ui.handle_event({"type": "status_cleared"})
        raise

    # 最终渲染完整响应
    if full_response.strip():
        if ui:
            ui.handle_event({"type": "status_cleared"})
        console.print()
        console.print(Markdown(full_response))
    elif ui:
        ui.handle_event({"type": "status_cleared"})

    # 显示本轮费用
    if cost_tracker:
        from termpilot.token_tracker import CostTracker
        total = cost_tracker.total_usage
        if total.total_tokens > 0:
            console.print()
            console.print(f"[dim]{cost_tracker.format_per_response(model, total)}[/]")

    return full_response


async def _async_single_prompt(prompt: str, model: str) -> None:
    """单次 prompt 模式。"""
    logger.debug("=== single_prompt mode: model=%s, prompt=%r", model, prompt[:100])

    storage = SessionStorage()
    ui = QuietUI(console)
    storage.start_session()
    logger.debug("session started: %s", storage.session_id)

    # 初始化 Undo 系统
    from termpilot.undo import init_undo, cleanup_stale_snapshots
    init_undo(storage.session_id)
    cleanup_stale_snapshots()

    # SessionStart Hook
    await dispatch_hooks(
        event=HookEvent.SESSION_START,
        session_id=storage.session_id or "",
    )

    # 初始化 MCP
    mcp_manager = MCPManager()
    await mcp_manager.discover_and_connect()

    # 加载 skills
    discover_and_load_skills()

    client, client_format = create_client()
    logger.debug("client created")
    tools = get_all_tools(mcp_manager=mcp_manager)
    enabled_tools = {t.name for t in tools}
    logger.debug("tools: %d enabled (%s)", len(tools), ", ".join(sorted(enabled_tools)[:10]))
    permission_context = build_permission_context()
    logger.debug("permission mode: %s", permission_context.mode.value)

    system_prompt = build_system_prompt(
        model=model,
        enabled_tools=enabled_tools,
        mcp_manager=mcp_manager,
    )
    logger.debug("system prompt built: %d chars", len(system_prompt))

    # UserPromptSubmit Hook
    hook_results = await dispatch_hooks(
        event=HookEvent.USER_PROMPT_SUBMIT,
        session_id=storage.session_id or "",
        prompt=prompt,
    )
    # 检查是否被 hook 阻断
    for hr in hook_results:
        if hr.exit_code == 2:
            console.print(f"[yellow]Hook blocked prompt: {hr.stderr or 'blocked'}[/]")
            return
    # 注入 hook 反馈
    hook_feedback = [hr.stdout for hr in hook_results if hr.exit_code == 0 and hr.stdout.strip()]
    effective_prompt = prompt
    if hook_feedback:
        effective_prompt += "\n\n<user-prompt-submit-hook>\n" + "\n".join(
            hook_feedback) + "\n</user-prompt-submit-hook>"

    messages = [create_user_message(effective_prompt)]

    storage.record_user_message(prompt)
    logger.debug("sending single prompt to API (%d chars)", len(effective_prompt))

    from termpilot.token_tracker import CostTracker
    cost_tracker = CostTracker()

    try:
        response = await _stream_response_with_tools(
            client, model, system_prompt, messages, tools, storage,
            permission_context=permission_context,
            session_id=storage.session_id or "",
            cost_tracker=cost_tracker,
            ui=ui,
            client_format=client_format,
        )
    except Exception as api_exc:
        _print_connection_error(api_exc)
        return

    storage.record_assistant_message(response)
    logger.debug("single prompt response: %d chars", len(response))

    # 生成会话标题
    messages.append(create_assistant_message(response))
    from termpilot.session import generate_session_title
    title = await generate_session_title(messages, client, model, client_format)
    if title:
        storage.save_metadata("custom-title", title)
        logger.debug("session title: %s", title)

    # Stop Hook
    await dispatch_hooks(
        event=HookEvent.STOP,
        session_id=storage.session_id or "",
    )

    console.print()
    console.rule()


def _pick_session(sessions: list[dict]) -> str | None:
    """让用户从历史会话中选择一个。"""
    if not sessions:
        console.print("[yellow]没有找到历史会话。[/]")
        return None

    table = Table(title="历史会话")
    table.add_column("#", style="dim", width=4)
    table.add_column("会话ID", style="cyan", width=12)
    table.add_column("标题", width=30)
    table.add_column("首条消息", width=30)
    table.add_column("消息数", justify="right", width=6)

    for i, s in enumerate(sessions[:20], 1):
        title = s.get("title", "")[:30]
        table.add_row(
            str(i),
            s.get("session_id", "")[:8] + "...",
            title or s.get("first_prompt", "")[:30],
            s.get("first_prompt", "")[:30] if title else "",
            str(s.get("message_count", 0)),
        )
    console.print(table)

    try:
        import questionary as _q
        choice = _esc_ask(_q.text("选择会话编号（直接回车取消）:")) or ""
        if not choice.strip():
            return None
        idx = int(choice.strip()) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx].get("session_id")
    except (ValueError, KeyboardInterrupt):
        pass
    return None


def _print_connection_error(exc: Exception) -> None:
    """打印连接失败的友好提示。"""
    from termpilot.config import get_settings_path
    settings_path = get_settings_path()
    console.print(
        f"[red]Failed to connect to LLM API.[/]\n\n"
        f"Check [bold]{settings_path}[/]:\n"
        f"  - API key is valid and active\n"
        f"  - Base URL is correct\n"
        f"  - Network is accessible\n\n"
        f"Tip: run [bold]termpilot model[/] to reconfigure.\n\n"
        f"[dim]Error: {exc}[/]"
    )


async def _async_interactive(model: str, resume_session_id: str | None = None) -> None:
    """交互循环模式。"""
    logger.debug("=== interactive mode: model=%s, resume_session_id=%s", model, resume_session_id)
    storage = SessionStorage()
    ui = QuietUI(console)

    if resume_session_id:
        # 恢复模式 — 加载历史消息
        history_messages = load_session(resume_session_id)
        storage.start_session(resume_session_id)
        logger.debug("resumed session %s: %d history messages", resume_session_id[:8], len(history_messages))
        console.print(f"[dim]已恢复会话: {resume_session_id[:8]}... ({len(history_messages)} 条历史消息)[/]")
    else:
        history_messages = []
        storage.start_session()
        logger.debug("new session: %s", storage.session_id)

    # 初始化 Undo 系统
    from termpilot.undo import init_undo, cleanup_stale_snapshots
    init_undo(storage.session_id)
    cleanup_stale_snapshots()

    # SessionStart Hook
    await dispatch_hooks(
        event=HookEvent.SESSION_START,
        session_id=storage.session_id or "",
    )

    # 初始化 MCP
    mcp_manager = MCPManager()
    await mcp_manager.discover_and_connect()

    # 加载 skills
    discover_and_load_skills()

    client, client_format = create_client()
    logger.debug("client created")
    tools = get_all_tools(mcp_manager=mcp_manager)
    enabled_tools = {t.name for t in tools}
    logger.debug("tools: %d enabled (%s)", len(tools), ", ".join(sorted(enabled_tools)[:10]))
    permission_context = build_permission_context()
    logger.debug("permission mode: %s, working_dir=%s", permission_context.mode.value,
                 permission_context.working_directory)

    system_prompt = build_system_prompt(
        model=model,
        enabled_tools=enabled_tools,
        mcp_manager=mcp_manager,
    )
    logger.debug("system prompt built: %d chars", len(system_prompt))
    messages = list(history_messages)

    # 构建 MCP 状态信息
    mcp_info = ""
    if mcp_manager.is_connected:
        mcp_tools = mcp_manager.get_tools()
        if mcp_tools:
            mcp_info = f"\nMCP: {len(mcp_tools)} 工具 ({', '.join(t['full_name'] for t in mcp_tools[:3])}{'...' if len(mcp_tools) > 3 else ''})"

    from termpilot.token_tracker import CostTracker
    cost_tracker = CostTracker()
    title_generated = False  # 首轮对话后生成标题

    def refresh_runtime() -> str:
        nonlocal client, client_format, model, system_prompt
        client, client_format = create_client()
        model = get_effective_model(DEFAULT_MODEL)
        system_prompt = build_system_prompt(
            model=model,
            enabled_tools=enabled_tools,
            mcp_manager=mcp_manager,
        )
        logger.debug("runtime refreshed after /model: model=%s, format=%s", model, client_format)
        return model

    console.print(
        Panel(
            Text.from_markup(
                f"[bold]TermPilot[/] — model: {model}\n"
                f"工具: {', '.join(t.name for t in tools[:6])}{'...' if len(tools) > 6 else ''}\n"
                f"权限模式: {permission_context.mode.value}\n"
                f"会话: {storage.session_id[:8] if storage.session_id else 'N/A'}...{mcp_info}\n"
                f"输入消息开始对话，/help 查看命令，Ctrl+C 退出\n"
                f"Shift+Tab 切换 Plan Mode（只读规划）"
            ),
            border_style="blue",
        )
    )

    from termpilot.completer import SlashCompleter
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style as PtStyle

    slash_completer = SlashCompleter()
    slash_completer.refresh()
    pt_style = PtStyle.from_dict({
        "prompt": "bold green",
        "plan-prompt": "bold #ff8800",
    })

    # Plan mode 状态（通过闭包在键绑定和循环之间共享）
    plan_mode_active = False

    plan_bindings = KeyBindings()

    @plan_bindings.add(Keys.BackTab, eager=True)
    def _toggle_plan_mode(event):
        nonlocal plan_mode_active
        plan_mode_active = not plan_mode_active
        if plan_mode_active:
            print("[Plan Mode ON — read-only]")
        else:
            print("[Plan Mode OFF — full access]")
        event.app.invalidate()

    def _get_prompt_message():
        if plan_mode_active:
            return [("class:plan-prompt", "PLAN> ")]
        return [("class:prompt", "> ")]

    history_file = get_config_home() / "prompt_history"
    pt_session = PromptSession(
        message=_get_prompt_message,
        completer=slash_completer,
        complete_while_typing=True,
        style=pt_style,
        history=FileHistory(str(history_file)),
        enable_history_search=True,
        key_bindings=plan_bindings,
    )

    while True:
        try:
            console.print()
            user_input = await pt_session.prompt_async()

            # 清理终端输入中可能出现的 Unicode 代理字符
            user_input = user_input.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")

            if not user_input.strip():
                continue

            # ── Slash 命令处理 ──
            parsed = parse_slash_command(user_input)
            if parsed:
                cmd_name, cmd_args = parsed
                logger.debug("slash command: /%s %s", cmd_name, cmd_args[:50])
                cmd_context = {
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "client": client,
                    "model": model,
                    "mcp_manager": mcp_manager,
                    "ui": ui,
                    "client_format": client_format,
                    "refresh_runtime": refresh_runtime,
                    "storage": storage,
                }
                result = await dispatch_command(cmd_name, cmd_args, cmd_context)
                logger.debug("command result: exit_repl=%s, should_query=%s, output=%d chars",
                             result.exit_repl, result.should_query, len(result.output))

                if result.exit_repl:
                    console.print("[dim]再见！[/]")
                    break

                if result.output:
                    console.print()
                    console.print(Markdown(result.output))

                if result.new_messages is not None:
                    if len(result.new_messages) == 0:
                        # /clear：清除消息
                        messages.clear()
                        console.print("[dim]对话已清除[/]")
                    else:
                        messages = result.new_messages

                if result.should_query:
                    # 命令要求发送给模型（skill 回退、/compact 后的摘要等）
                    # 将 skill prompt 作为用户消息发送，让 LLM 按指令执行
                    messages.append(create_user_message(result.output))
                    storage.record_user_message(result.output)
                    logger.debug("should_query: sending skill/command output to API, %d messages", len(messages))

                    try:
                        full_response = await _stream_response_with_tools(
                            client, model, system_prompt, messages, tools, storage,
                            permission_context=permission_context,
                            session_id=storage.session_id or "",
                            cost_tracker=cost_tracker,
                            ui=ui,
                            client_format=client_format,
                        )
                    except Exception as api_exc:
                        _print_connection_error(api_exc)
                        console.print()
                        console.rule()
                        continue
                    messages.append({**create_assistant_message(full_response), "_timestamp": time.time()})
                    storage.record_assistant_message(full_response)

                    # Stop Hook
                    await dispatch_hooks(
                        event=HookEvent.STOP,
                        session_id=storage.session_id or "",
                    )

                console.print()
                console.rule()
                continue

            # ── 正常对话处理 ──
            logger.debug("user input: %s", user_input[:100])

            # UserPromptSubmit Hook
            hook_results = await dispatch_hooks(
                event=HookEvent.USER_PROMPT_SUBMIT,
                session_id=storage.session_id or "",
                prompt=user_input,
            )
            # 检查是否被 hook 阻断
            blocked = False
            for hr in hook_results:
                if hr.exit_code == 2:
                    console.print(f"[yellow]Hook blocked prompt: {hr.stderr or 'blocked'}[/]")
                    blocked = True
                    break
            if blocked:
                continue
            # 注入 hook 反馈
            hook_feedback = [hr.stdout for hr in hook_results if hr.exit_code == 0 and hr.stdout.strip()]
            effective_input = user_input
            if hook_feedback:
                effective_input += "\n\n<user-prompt-submit-hook>\n" + "\n".join(
                    hook_feedback) + "\n</user-prompt-submit-hook>"

            # 处理文件附件（@file 引用）
            attachment_blocks = process_attachments(effective_input)
            logger.debug("attachments: %d blocks", len(attachment_blocks))
            if attachment_blocks:
                # 将附件和文本一起作为 content blocks
                content_blocks = [{"type": "text", "text": effective_input}] + attachment_blocks
                messages.append(create_user_message(content_blocks))
            else:
                messages.append(create_user_message(effective_input))
            storage.record_user_message(user_input)
            logger.debug("sending to API: %d messages in context", len(messages))

            # Plan mode: 切换为只读权限
            if plan_mode_active:
                effective_permission = PermissionContext(
                    mode=PermissionMode.PLAN,
                    allow_rules=[],
                    deny_rules=[],
                    ask_rules=[],
                    working_directory=permission_context.working_directory,
                )
            else:
                effective_permission = permission_context

            try:
                full_response = await _stream_response_with_tools(
                    client, model, system_prompt, messages, tools, storage,
                    permission_context=effective_permission,
                    session_id=storage.session_id or "",
                    cost_tracker=cost_tracker,
                    ui=ui,
                    client_format=client_format,
                )
            except Exception as api_exc:
                _print_connection_error(api_exc)
                continue

            messages.append({**create_assistant_message(full_response), "_timestamp": time.time()})
            storage.record_assistant_message(full_response)
            logger.debug("response received: %d chars, total messages: %d", len(full_response), len(messages))

            # 首轮对话后生成会话标题
            if not title_generated and len(messages) >= 2:
                from termpilot.session import generate_session_title
                title = await generate_session_title(messages, client, model, client_format)
                if title:
                    storage.save_metadata("custom-title", title)
                    logger.debug("session title generated: %s", title)
                title_generated = True

            # Stop Hook
            await dispatch_hooks(
                event=HookEvent.STOP,
                session_id=storage.session_id or "",
            )

            console.print()
            console.rule()

        except KeyboardInterrupt:
            console.print("\n[dim]再见！[/]")
            break

    # 显示费用汇总
    if cost_tracker.get_total_cost() > 0:
        console.print()
        console.print(f"[dim]{cost_tracker.format_report()}[/]")

    # 清理 MCP 连接
    await mcp_manager.shutdown()


def _setup_logging() -> None:
    """配置日志到文件，不影响终端交互。

    对齐 TS 版设计：
    - 日志写文件，不写 stderr（不干扰 Rich UI）
    - 按 session 分文件：~/.termpilot/debug/<sessionId>.txt
    - latest 软链接指向当前会话，方便 tail -f
    - 环境变量 CC_PYTHON_LOG_LEVEL 控制级别
    """
    import os
    import uuid

    log_level_str = os.environ.get("CC_PYTHON_LOG_LEVEL", "DEBUG").upper()
    log_level = getattr(logging, log_level_str, logging.DEBUG)

    log_dir = get_config_home() / "debug"
    log_dir.mkdir(parents=True, exist_ok=True)

    session_id = str(uuid.uuid4())[:8]
    log_file = log_dir / f"{session_id}.txt"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    ))

    root_logger = logging.getLogger("termpilot")
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # latest 软链接指向当前会话
    latest_link = log_dir / "latest"
    try:
        if latest_link.is_symlink() or latest_link.exists():
            latest_link.unlink()
        latest_link.symlink_to(log_file)
    except OSError:
        pass

    # 静默第三方库的日志
    for noisy in ("httpx", "httpcore", "openai", "anthropic", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger.info("=== termpilot 启动 (session: %s) ===", session_id)


def _check_update() -> None:
    """检查 PyPI 上是否有新版本，有则提示升级。

    使用缓存的检查结果（~/.termpilot/.update-check），每天最多查一次。
    """
    import json
    import urllib.request
    from packaging.version import Version
    from termpilot import __version__
    from termpilot.config import get_config_home

    cache_file = get_config_home() / ".update-check"

    def _is_outdated(latest: str) -> bool:
        try:
            return Version(latest) > Version(__version__)
        except Exception:
            return False

    now_day = time.strftime("%Y-%m-%d")
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("day") == now_day:
                latest = cached.get("latest", "")
                if _is_outdated(latest):
                    console.print(f"[dim]⚠️  termpilot {__version__} is outdated. Latest: {latest}[/]")
                    console.print(f"[dim]   Run: pip install -U termpilot[/]\n")
                return
        except (json.JSONDecodeError, KeyError):
            pass

    try:
        req = urllib.request.Request(
            "https://pypi.org/pypi/termpilot/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            latest = data.get("info", {}).get("version", "")

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"day": now_day, "latest": latest}), encoding="utf-8")

        if _is_outdated(latest):
            console.print(f"[dim]⚠️  termpilot {__version__} is outdated. Latest: {latest}[/]")
            console.print(f"[dim]   Run: pip install -U termpilot[/]\n")
    except Exception:
        pass


@click.group(invoke_without_command=True)
@click.option(
    "--prompt", "-p",
    default=None,
    help="直接传入一条 prompt，不进入交互模式",
)
@click.option(
    "--model", "-m",
    default=None,
    help="模型名称 (默认从 settings.json 或 claude-sonnet-4-20250514)",
)
@click.option(
    "--resume", "-r", "resume",
    is_flag=True,
    default=False,
    help="恢复上一次会话继续对话",
)
@click.option(
    "--session", "-s", "session_id",
    default=None,
    help="指定要恢复的会话 ID",
)
@click.pass_context
def main(ctx: click.Context, prompt: str | None, model: str | None, resume: bool, session_id: str | None) -> None:
    """TermPilot — AI 编程助手。"""
    if ctx.invoked_subcommand is not None:
        return
    _setup_logging()
    _check_update()
    ensure_settings_template()

    resolved_model = model or get_effective_model(DEFAULT_MODEL)
    logger.debug("=== main() called: prompt=%s, model=%s, resume=%s, session_id=%s ===",
                 "yes" if prompt else None, resolved_model, resume, session_id)

    # 确定 resume 的 session_id
    effective_session_id = session_id

    if resume and not effective_session_id:
        sessions = list_sessions()
        effective_session_id = _pick_session(sessions)

    if prompt:
        asyncio.run(_async_single_prompt(prompt, resolved_model))
    else:
        asyncio.run(_async_interactive(resolved_model, effective_session_id))


@main.command(name="model")
def model_cmd() -> None:
    """Configure LLM provider and model interactively."""
    from termpilot.config import run_setup_wizard
    run_setup_wizard()


@main.command(name="setup")
def setup_cmd() -> None:
    """Configure LLM provider and API key interactively."""
    from termpilot.config import run_setup_wizard
    run_setup_wizard()


if __name__ == "__main__":
    main()
