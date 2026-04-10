"""CLI 入口。

对应 TS: main.tsx (CLI 参数解析、启动逻辑) + entrypoints/cli.tsx
"""

from __future__ import annotations

import asyncio
from typing import Any

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cc_python.api import create_client, query_with_tools
from cc_python.config import get_effective_model
from cc_python.context import build_system_prompt
from cc_python.hooks import HookEvent, dispatch_hooks
from cc_python.messages import create_assistant_message, create_user_message
from cc_python.permissions import (
    PermissionBehavior,
    PermissionContext,
    PermissionResult,
    build_permission_context,
)
from cc_python.session import SessionStorage, list_sessions, load_session
from cc_python.tools import get_all_tools

DEFAULT_MODEL = "claude-sonnet-4-20250514"

console = Console()


async def _permission_prompt(
    tool_name: str,
    tool_input: dict,
    message: str,
) -> PermissionResult:
    """权限确认提示。对应 TS useCanUseTool.tsx 的用户交互部分。"""
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
    console.print("[bold]选择:[/]")
    console.print("  [1] Allow once    (本次允许)")
    console.print("  [2] Always allow  (始终允许同类操作)")
    console.print("  [3] Deny          (拒绝)")
    console.print("  [4] Always deny   (始终拒绝同类操作)")

    try:
        choice = console.input("[bold green]选择 [1-4]: [/]").strip()
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
        client_format: str,
        model: str,
        system_prompt: str,
        messages: list[dict],
        tools: list,
        storage: SessionStorage | None = None,
        permission_context: PermissionContext | None = None,
        session_id: str = "",
) -> str:
    """带工具调用的流式响应。

    对应 TS query.ts 主循环 + REPL.tsx 的渲染逻辑。
    使用回调实现实时渲染：
    - on_text: 文本流实时渲染 Markdown
    - on_tool_call: 显示工具调用过程和结果
    """
    full_response = ""

    def on_text(chunk: str) -> None:
        nonlocal full_response
        full_response += chunk

    def on_tool_call(name: str, input_data: dict, result: str) -> None:
        # 对应 TS 中工具调用的 UI 渲染
        console.print()
        console.rule(f"[bold blue]工具调用: {name}[/]")
        # 显示参数（截断过长的）
        args_str = str(input_data)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        console.print(f"[dim]参数: {args_str}[/]")
        console.print()
        # 显示结果（截断过长的）
        if len(result) > 1000:
            console.print(Markdown(result[:1000] + "\n..."))
        else:
            console.print(Markdown(result))
        console.rule()

        # 记录工具调用到 session
        if storage:
            storage.record_tool_call(name, input_data, result)

    full_response = await query_with_tools(
        client=client,
        client_format=client_format,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        tools=tools,
        on_text=on_text,
        on_tool_call=on_tool_call,
        permission_context=permission_context,
        on_permission_ask=_permission_prompt,
        session_id=session_id,
    )

    # 最终渲染完整响应
    if full_response.strip():
        console.print()
        console.print(Markdown(full_response))

    return full_response


async def _async_single_prompt(prompt: str, model: str) -> None:
    """单次 prompt 模式。"""
    storage = SessionStorage()
    storage.start_session()

    # SessionStart Hook
    await dispatch_hooks(
        event=HookEvent.SESSION_START,
        session_id=storage.session_id or "",
    )

    client, client_format = create_client()
    tools = get_all_tools()
    enabled_tools = {t.name for t in tools}
    permission_context = build_permission_context()

    system_prompt = build_system_prompt(
        model=model,
        enabled_tools=enabled_tools,
    )

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
        effective_prompt += "\n\n<user-prompt-submit-hook>\n" + "\n".join(hook_feedback) + "\n</user-prompt-submit-hook>"

    messages = [create_user_message(effective_prompt)]

    storage.record_user_message(prompt)

    response = await _stream_response_with_tools(
        client, client_format, model, system_prompt, messages, tools, storage,
        permission_context=permission_context,
        session_id=storage.session_id or "",
    )

    storage.record_assistant_message(response)

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
    table.add_column("首条消息", width=40)
    table.add_column("消息数", justify="right", width=6)

    for i, s in enumerate(sessions[:20], 1):
        table.add_row(
            str(i),
            s.get("session_id", "")[:8] + "...",
            s.get("first_prompt", "")[:40],
            str(s.get("message_count", 0)),
        )
    console.print(table)

    try:
        choice = console.input("[bold green]选择会话编号（直接回车取消）: [/]")
        if not choice.strip():
            return None
        idx = int(choice.strip()) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx].get("session_id")
    except (ValueError, KeyboardInterrupt):
        pass
    return None


async def _async_interactive(model: str, resume_session_id: str | None = None) -> None:
    """交互循环模式。"""
    storage = SessionStorage()

    if resume_session_id:
        # 恢复模式 — 加载历史消息
        history_messages = load_session(resume_session_id)
        storage.start_session(resume_session_id)
        console.print(f"[dim]已恢复会话: {resume_session_id[:8]}... ({len(history_messages)} 条历史消息)[/]")
    else:
        history_messages = []
        storage.start_session()

    # SessionStart Hook
    await dispatch_hooks(
        event=HookEvent.SESSION_START,
        session_id=storage.session_id or "",
    )

    client, client_format = create_client()
    tools = get_all_tools()
    enabled_tools = {t.name for t in tools}
    permission_context = build_permission_context()

    system_prompt = build_system_prompt(
        model=model,
        enabled_tools=enabled_tools,
    )
    messages = list(history_messages)

    console.print(
        Panel(
            Text.from_markup(
                f"[bold]Claude Code (Python)[/] — model: {model}\n"
                f"工具: {', '.join(t.name for t in tools)}\n"
                f"权限模式: {permission_context.mode.value}\n"
                f"会话: {storage.session_id[:8] if storage.session_id else 'N/A'}...\n"
                f"输入消息开始对话，Ctrl+C 退出"
            ),
            border_style="blue",
        )
    )

    while True:
        try:
            console.print()
            user_input = console.input("[bold green]> [/]")

            # 清理终端输入中可能出现的 Unicode 代理字符
            user_input = user_input.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")

            if not user_input.strip():
                continue

            if user_input.strip().lower() in ("/exit", "/quit", "exit", "quit"):
                console.print("[dim]再见！[/]")
                break

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
                effective_input += "\n\n<user-prompt-submit-hook>\n" + "\n".join(hook_feedback) + "\n</user-prompt-submit-hook>"

            messages.append(create_user_message(effective_input))
            storage.record_user_message(user_input)

            full_response = await _stream_response_with_tools(
                client, client_format, model, system_prompt, messages, tools, storage,
                permission_context=permission_context,
                session_id=storage.session_id or "",
            )

            messages.append(create_assistant_message(full_response))
            storage.record_assistant_message(full_response)

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


@click.command()
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
def main(prompt: str | None, model: str | None, resume: bool, session_id: str | None) -> None:
    """Claude Code Python 版 — AI 编程助手。"""
    resolved_model = model or get_effective_model(DEFAULT_MODEL)

    # 确定 resume 的 session_id
    effective_session_id = session_id
    if resume and not effective_session_id:
        sessions = list_sessions()
        effective_session_id = _pick_session(sessions)

    if prompt:
        asyncio.run(_async_single_prompt(prompt, resolved_model))
    else:
        asyncio.run(_async_interactive(resolved_model, effective_session_id))


if __name__ == "__main__":
    main()
