"""API 调用封装 + 工具调用循环。

对应 TS:
- services/api/client.ts (getAnthropicClient) — 客户端创建
- services/api/claude.ts (queryModel) — API 调用
- query.ts (主循环) — 工具调用循环
- services/tools/toolOrchestration.ts (runTools) — 并发工具执行

工具调用核心流程（对应 TS query.ts:554-863）：
1. 发送消息 + tools 给 API
2. API 流式响应，可能包含多个 tool_use content block
3. 如果有 tool_use：
   a. 解析所有工具名和参数
   b. 按并发安全性分组：安全的并行执行，不安全的串行执行
      （对应 TS toolOrchestration.ts partitionToolCalls + runToolsConcurrently）
   c. 将所有 tool_result 追加到消息
   d. 再次调用 API（带上 tool_result）
   e. 重复直到模型返回纯文本（stop_reason != 'tool_use'）
4. 返回最终文本响应
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

from termpilot.compact import auto_compact_if_needed
from termpilot.tool_result_storage import process_tool_result
from termpilot.config import (
    apply_settings_env,
    get_context_window,
    get_effective_api_key,
    get_effective_base_url,
    get_effective_provider,
    _get_raw_provider,
    get_settings_path,
    is_placeholder_key,
)
from termpilot.hooks import HookEvent, dispatch_hooks
from termpilot.permissions import (
    PermissionBehavior,
    PermissionContext,
    check_permission,
)
from termpilot.tools.base import Tool

# 对应 TS toolOrchestration.ts:8-12 getMaxToolUseConcurrency()
MAX_CONCURRENT_TOOLS = int(
    __import__("os").environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "10") or "10"
)

MAX_API_RETRIES = 3
MAX_TOOL_RETRIES = 2


def _tool_result_success(tool_name: str, result_text: str) -> bool:
    """Infer whether a string-returning tool succeeded."""
    if tool_name == "agent":
        return not result_text.lstrip().startswith((
            "Agent API error:",
            "Agent error:",
            "Error:",
        ))
    return True


def _apply_permission_rule_update(context: PermissionContext | None, update: dict[str, Any]) -> None:
    """Apply a persisted permission rule update to the in-memory context."""
    if context is None:
        return

    from termpilot.permissions import PermissionRule

    try:
        behavior = PermissionBehavior(update["behavior"])
    except (KeyError, ValueError):
        return

    rule = PermissionRule(
        tool_name=str(update.get("tool_name", "")),
        pattern=str(update.get("pattern", "*")),
        behavior=behavior,
        source="user_settings",
    )
    if not rule.tool_name:
        return

    target_lists = {
        PermissionBehavior.ALLOW: context.allow_rules,
        PermissionBehavior.DENY: context.deny_rules,
        PermissionBehavior.ASK: context.ask_rules,
    }
    for rules in target_lists.values():
        rules[:] = [
            existing for existing in rules
            if not (existing.tool_name == rule.tool_name and existing.pattern == rule.pattern)
        ]
    target_lists[behavior].insert(0, rule)


def _is_retryable_error(exc: Exception) -> bool:
    """判断 API 错误是否可重试（429/5xx/超时/网络）。"""
    s = str(exc).lower()
    if any(code in s for code in ("429", "500", "502", "503", "504")):
        return True
    return any(w in s for w in ("timeout", "timed out", "connection", "network"))


def _is_retryable_tool_error(exc: Exception) -> bool:
    """判断 tool 执行错误是否可重试（超时/限流）。"""
    s = str(exc).lower()
    return any(w in s for w in ("timeout", "timed out", "429", "rate limit"))


def create_client() -> tuple[Any, str]:
    """创建 API 客户端。

    返回 (client, client_format):
    - client: AsyncOpenAI 或 AsyncAnthropic 实例
    - client_format: "openai" 或 "anthropic"

    Anthropic provider 使用 Anthropic SDK（原生消息格式）。
    其他所有 provider 使用 OpenAI SDK（OpenAI 兼容格式）。
    """
    apply_settings_env()

    raw_provider = _get_raw_provider()
    provider = get_effective_provider()
    api_key = get_effective_api_key(raw_provider)
    settings_path = get_settings_path()

    if not api_key or is_placeholder_key(api_key):
        if sys.stdin.isatty():
            from termpilot.config import run_setup_wizard
            console.print("[dim]API key not configured. Launching setup wizard…[/]\n")
            run_setup_wizard()
            apply_settings_env()
            raw_provider = _get_raw_provider()
            provider = get_effective_provider()
            api_key = get_effective_api_key(raw_provider)
        if not api_key or is_placeholder_key(api_key):
            sys.exit(
                f"API key not configured.\n"
                f"Run: termpilot setup\n"
                f"Or edit {settings_path}"
            )

    if provider == "anthropic":
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            sys.exit(
                "Anthropic SDK not installed.\n"
                "Run: pip install anthropic"
            )
        base_url = get_effective_base_url(raw_provider)
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncAnthropic(**kwargs)
        return client, "anthropic"

    # OpenAI-compatible
    base_url = get_effective_base_url(raw_provider)
    try:
        from openai import AsyncOpenAI
    except ImportError:
        sys.exit("OpenAI SDK not installed. Run: pip install termpilot")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return client, "openai"


async def _call_openai_streaming(
        client: Any,
        model: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
) -> AsyncGenerator[dict[str, Any], None]:
    """OpenAI 格式的流式 API 调用。"""
    api_messages = [{"role": "system", "content": system_prompt}] + messages

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        # 转换为 OpenAI function calling 格式
        oai_tools = []
        for t in tools:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                }
            })
        kwargs["tools"] = oai_tools

    try:
        stream = await client.chat.completions.create(
            **kwargs,
            stream_options={"include_usage": True},
        )
    except Exception as e:
        # Some OpenAI-compatible providers do not accept stream_options. Fall
        # back to the plain request instead of failing the user turn.
        if "stream_options" not in str(e):
            raise
        logger.debug("provider does not support stream_options, retrying without usage: %s", e)
        stream = await client.chat.completions.create(**kwargs)

    # 收集 tool_calls
    tool_call_buffers: dict[int, dict[str, Any]] = {}  # index -> {id, name, arguments}

    async for chunk in stream:
        if hasattr(chunk, "usage") and chunk.usage:
            yield {
                "type": "usage",
                "usage": {
                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 文本内容
        if delta.content:
            yield {"type": "text", "content": delta.content}

        # tool calls 增量
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_call_buffers:
                    tool_call_buffers[idx] = {
                        "id": tc.id or "",
                        "name": "",
                        "arguments": "",
                    }
                if tc.id:
                    tool_call_buffers[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_call_buffers[idx]["name"] = tc.function.name
                    if tc.function.arguments:
                        tool_call_buffers[idx]["arguments"] += tc.function.arguments

    # 产出所有 tool_calls
    for idx in sorted(tool_call_buffers.keys()):
        tc = tool_call_buffers[idx]
        try:
            input_data = json.loads(tc["arguments"]) if tc["arguments"] else {}
        except json.JSONDecodeError:
            input_data = {}
        yield {
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": input_data,
        }

    # 产出 usage 事件（OpenAI 格式）
    try:
        if hasattr(stream, "usage") and stream.usage:
            yield {
                "type": "usage",
                "usage": {
                    "input_tokens": getattr(stream.usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(stream.usage, "completion_tokens", 0) or 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
    except Exception:
        logger.debug("could not extract usage from OpenAI stream")


async def _call_anthropic_streaming(
        client: Any,
        model: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
) -> AsyncGenerator[dict[str, Any], None]:
    """Anthropic 原生格式的流式 API 调用。"""
    # 转换消息格式：OpenAI → Anthropic
    anthropic_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            continue  # system 通过 system_prompt 参数传递
        if role == "user":
            anthropic_messages.append({"role": "user", "content": msg.get("content", "")})
        elif role == "assistant":
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    func = tc.get("function", {})
                    try:
                        inp = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        inp = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": func.get("name", ""),
                        "input": inp,
                    })
                anthropic_messages.append({"role": "assistant", "content": blocks})
            else:
                anthropic_messages.append({"role": "assistant", "content": content})
        elif role == "tool":
            # OpenAI tool result → Anthropic tool_result
            anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })

    kwargs: dict[str, Any] = {
        "model": model,
        "system": system_prompt,
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in tools
        ]

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    yield {"type": "text", "content": event.delta.text}
                elif hasattr(event.delta, "partial_json"):
                    pass  # tool input 部分在 message 停止后统一处理
            elif event.type == "message_stop":
                # 获取完整消息以提取 tool_use blocks
                message = await stream.get_final_message()
                for block in message.content:
                    if block.type == "tool_use":
                        yield {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                if hasattr(message, "usage") and message.usage:
                    yield {
                        "type": "usage",
                        "usage": {
                            "input_tokens": getattr(message.usage, "input_tokens", 0) or 0,
                            "output_tokens": getattr(message.usage, "output_tokens", 0) or 0,
                            "cache_creation_input_tokens": getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
                            "cache_read_input_tokens": getattr(message.usage, "cache_read_input_tokens", 0) or 0,
                        },
                    }


async def _execute_tools_concurrent(
        tool_use_blocks: list[dict[str, Any]],
        tools: list[Tool],
        on_tool_call: Any = None,
        on_event: Any = None,
        permission_context: PermissionContext | None = None,
        on_permission_ask: Any = None,
        session_id: str = "",
) -> list[dict[str, Any]]:
    """并发执行工具调用。

    对应 TS toolOrchestration.ts:
    - partitionToolCalls() — 按并发安全性分组
    - runToolsConcurrently() — 安全工具并行执行
    - runToolsSerially() — 不安全工具串行执行

    新增权限检查：
    执行前调用 check_permission，ASK 时通过 on_permission_ask 回调询问用户。

    Args:
        permission_context: 权限上下文，None 则跳过权限检查
        on_permission_ask: 异步回调 async (tool_name, input, message) -> PermissionResult
    """
    from termpilot.tools import find_tool_by_name

    tool_results: list[dict[str, Any]] = []
    logger.debug("_execute_tools_concurrent: %d tool calls to process", len(tool_use_blocks))

    # 分组：并发安全的 vs 不安全的
    safe_tasks: list[tuple[dict[str, Any], Tool]] = []
    unsafe_tasks: list[tuple[dict[str, Any], Tool]] = []

    for tb in tool_use_blocks:
        tool = find_tool_by_name(tools, tb["name"])
        if tool is None:
            # 未知工具直接生成错误结果
            logger.debug("unknown tool: %s", tb["name"])
            result_text = f"错误：未知工具 '{tb['name']}'"
            if on_event:
                on_event({
                    "type": "tool_failed",
                    "name": tb["name"],
                    "input": tb["input"],
                    "result": result_text,
                })
            if on_tool_call:
                on_tool_call(tb["name"], tb["input"], result_text)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tb["id"],
                "content": result_text,
            })
            continue

        # --- PreToolUse Hook ---
        hook_results = await dispatch_hooks(
            event=HookEvent.PRE_TOOL_USE,
            session_id=session_id,
            cwd=str(Path.cwd()),
            tool_name=tb["name"],
            tool_input=tb["input"],
            tool_use_id=tb["id"],
        )
        for hr in hook_results:
            if hr.exit_code == 2 or hr.decision == "deny":
                logger.debug("PreToolUse hook blocked: %s → %s", tb["name"], hr.stderr or hr.reason)
                result_text = f"Hook blocked: {hr.stderr or hr.reason or 'blocked by PreToolUse hook'}"
                if on_tool_call:
                    on_tool_call(tb["name"], tb["input"], result_text)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tb["id"],
                    "content": result_text,
                })
                break
            if hr.updated_input:
                tb["input"] = hr.updated_input
        else:
            # 没有 break（没有被 hook 阻断）→ 继续权限检查
            # --- 权限检查 ---
            if permission_context:
                perm_result = check_permission(tb["name"], tb["input"], permission_context)
                logger.debug("permission check: %s → %s (%s)", tb["name"], perm_result.behavior.value,
                             perm_result.message)

                if perm_result.behavior == PermissionBehavior.DENY:
                    result_text = f"权限拒绝: {perm_result.message}"
                    if on_event:
                        on_event({
                            "type": "tool_failed",
                            "name": tb["name"],
                            "input": tb["input"],
                            "result": result_text,
                        })
                    if on_tool_call:
                        on_tool_call(tb["name"], tb["input"], result_text)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tb["id"],
                        "content": result_text,
                    })
                    continue

                if perm_result.behavior == PermissionBehavior.ASK:
                    if on_permission_ask:
                        if on_event:
                            on_event({
                                "type": "permission_requested",
                                "name": tb["name"],
                                "input": tb["input"],
                                "message": perm_result.message,
                            })
                        user_result = await on_permission_ask(
                            tb["name"], tb["input"], perm_result.message,
                        )
                        # 持久化用户选择的规则
                        if user_result.rule_updates:
                            from termpilot.permissions import PermissionRule, save_permission_rule
                            for update in user_result.rule_updates:
                                save_permission_rule(PermissionRule(
                                    tool_name=update["tool_name"],
                                    pattern=update.get("pattern", "*"),
                                    behavior=PermissionBehavior(update["behavior"]),
                                    source="user_settings",
                                ))
                                _apply_permission_rule_update(permission_context, update)

                        if user_result.behavior == PermissionBehavior.DENY:
                            result_text = f"权限拒绝: {user_result.message or '用户拒绝'}"
                            if on_event:
                                on_event({
                                    "type": "tool_failed",
                                    "name": tb["name"],
                                    "input": tb["input"],
                                    "result": result_text,
                                })
                            if on_tool_call:
                                on_tool_call(tb["name"], tb["input"], result_text)
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": tb["id"],
                                "content": result_text,
                            })
                            continue
                    else:
                        # 没有回调时默认拒绝
                        result_text = f"权限拒绝: 需要用户确认但无交互界面"
                        if on_event:
                            on_event({
                                "type": "tool_failed",
                                "name": tb["name"],
                                "input": tb["input"],
                                "result": result_text,
                            })
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tb["id"],
                            "content": result_text,
                        })
                        continue
            # --- 权限检查结束 ---

        if tool.is_concurrency_safe:
            safe_tasks.append((tb, tool))
        else:
            unsafe_tasks.append((tb, tool))

    logger.debug("tool grouping: %d safe (parallel), %d unsafe (serial)",
                 len(safe_tasks), len(unsafe_tasks))

    # 1. 并发安全的一起跑（对应 TS runToolsConcurrently）
    if safe_tasks:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_TOOLS)

        async def _run_safe(tb: dict, tool: Tool) -> dict[str, Any]:
            async with semaphore:
                if on_event:
                    on_event({
                        "type": "tool_started",
                        "name": tb["name"],
                        "input": tb["input"],
                    })
                success = False
                result_text = ""
                for attempt in range(MAX_TOOL_RETRIES + 1):
                    try:
                        call_kwargs = dict(tb["input"])
                        if permission_context and tb["name"] in ("enter_plan_mode", "exit_plan_mode"):
                            call_kwargs["permission_context"] = permission_context
                        if tb["name"] == "agent" and on_event:
                            call_kwargs["_parent_on_event"] = on_event
                        result_text = await tool.call(**call_kwargs)
                        success = _tool_result_success(tb["name"], result_text)
                        break
                    except Exception as e:
                        if attempt == MAX_TOOL_RETRIES or not _is_retryable_tool_error(e):
                            result_text = f"工具执行错误: {e}"
                            break
                        wait = 2 ** attempt
                        logger.warning("tool %s error (attempt %d), retrying in %ds: %s",
                                       tb["name"], attempt + 1, wait, e)
                        await asyncio.sleep(wait)
            # 处理大型工具结果（持久化到磁盘）
            result_text = process_tool_result(result_text, tb["id"], tb["name"])
            # PostToolUse Hook
            hook_results = await dispatch_hooks(
                event=HookEvent.POST_TOOL_USE,
                session_id=session_id,
                cwd=str(Path.cwd()),
                tool_name=tb["name"],
                tool_input=tb["input"],
                tool_use_id=tb["id"],
                tool_response=result_text,
            )
            for hr in hook_results:
                if hr.exit_code == 2 and hr.stderr:
                    result_text += f"\n\n[Hook warning: {hr.stderr}]"
            if on_event:
                on_event({
                    "type": "tool_finished" if success else "tool_failed",
                    "name": tb["name"],
                    "input": tb["input"],
                    "result": result_text,
                })
            if on_tool_call:
                on_tool_call(tb["name"], tb["input"], result_text)
            return {
                "role": "tool",
                "tool_call_id": tb["id"],
                "content": result_text,
            }

        safe_results = await asyncio.gather(
            *[_run_safe(tb, tool) for tb, tool in safe_tasks],
            return_exceptions=False,
        )
        tool_results.extend(safe_results)

    # 2. 不安全的串行跑（对应 TS runToolsSerially）
    for tb, tool in unsafe_tasks:
        if on_event:
            on_event({
                "type": "tool_started",
                "name": tb["name"],
                "input": tb["input"],
            })
            # 交互式工具需要先清除 spinner，否则遮挡输入
            if tb["name"] in ("ask_user_question", "exit_plan_mode"):
                on_event({"type": "status_cleared"})
        success = False
        result_text = ""
        for attempt in range(MAX_TOOL_RETRIES + 1):
            try:
                call_kwargs = dict(tb["input"])
                if permission_context and tb["name"] in ("enter_plan_mode", "exit_plan_mode"):
                    call_kwargs["permission_context"] = permission_context
                if tb["name"] == "agent" and on_event:
                    call_kwargs["_parent_on_event"] = on_event
                # exit_plan_mode: show plan and ask user for approval
                if tb["name"] == "exit_plan_mode":
                    plan = tb["input"].get("plan", "")
                    if plan.strip() and on_permission_ask:
                        from rich.panel import Panel
                        from rich.text import Text
                        try:
                            from rich.console import Console
                            console_any = Console()
                            console_any.print()
                            console_any.print(Panel(
                                Text(plan[:3000], overflow="ellipsis"),
                                title="[bold]Plan Mode — Implementation Plan[/]",
                                border_style="yellow",
                            ))
                        except Exception:
                            pass
                        try:
                            loop = asyncio.get_event_loop()
                            import questionary
                            choice = await loop.run_in_executor(
                                None,
                                lambda: questionary.select(
                                    "Approve this plan?",
                                    choices=[
                                        questionary.Choice("Yes, start implementation", value="yes"),
                                        questionary.Choice("No, revise the plan", value="no"),
                                    ],
                                ).ask(),
                            )
                        except (KeyboardInterrupt, EOFError):
                            choice = "no"
                        if choice != "yes":
                            call_kwargs["plan_approved"] = False
                result_text = await tool.call(**call_kwargs)
                success = _tool_result_success(tb["name"], result_text)
                break
            except Exception as e:
                if attempt == MAX_TOOL_RETRIES or not _is_retryable_tool_error(e):
                    result_text = f"工具执行错误: {e}"
                    break
                wait = 2 ** attempt
                logger.warning("tool %s error (attempt %d), retrying in %ds: %s",
                               tb["name"], attempt + 1, wait, e)
                await asyncio.sleep(wait)
        # 处理大型工具结果（持久化到磁盘）
        result_text = process_tool_result(result_text, tb["id"], tb["name"])
        # PostToolUse Hook
        hook_results = await dispatch_hooks(
            event=HookEvent.POST_TOOL_USE,
            session_id=session_id,
            cwd=str(Path.cwd()),
            tool_name=tb["name"],
            tool_input=tb["input"],
            tool_use_id=tb["id"],
            tool_response=result_text,
        )
        for hr in hook_results:
            if hr.exit_code == 2 and hr.stderr:
                result_text += f"\n\n[Hook warning: {hr.stderr}]"
        if on_event:
            on_event({
                "type": "tool_finished" if success else "tool_failed",
                "name": tb["name"],
                "input": tb["input"],
                "result": result_text,
            })
        if on_tool_call:
            on_tool_call(tb["name"], tb["input"], result_text)
        tool_results.append({
            "role": "tool",
            "tool_call_id": tb["id"],
            "content": result_text,
        })

    return tool_results


async def query_with_tools(
        client: Any,
        model: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[Tool],
        max_tokens: int = 4096,
        on_text: Any = None,
        on_tool_call: Any = None,
        on_event: Any = None,
        permission_context: PermissionContext | None = None,
        on_permission_ask: Any = None,
        session_id: str = "",
        cost_tracker: Any | None = None,
        client_format: str = "openai",
        on_assistant_message: Any = None,
) -> str:
    """带工具调用的完整查询循环。

    对应 TS query.ts 的主循环（约第 554-863 行）+
    toolOrchestration.ts 的并发执行。

    统一使用 OpenAI-compatible 消息格式：
    - Assistant message: {"role": "assistant", "content": text, "tool_calls": [...]}
    - Tool result: {"role": "tool", "tool_call_id": ..., "content": ...}
    """
    from termpilot.tools import tool_to_api_schema

    tools_schema = [tool_to_api_schema(t) for t in tools]
    logger.debug("query_with_tools: model=%s, tools=%d, messages=%d",
                 model, len(tools), len(messages))

    current_messages = list(messages)

    # 上下文压缩：在发送 API 前检查 token 数
    context_window = get_context_window()
    current_messages = await auto_compact_if_needed(
        current_messages, system_prompt,
        client, model,
        context_window=context_window,
        client_format=client_format,
    )
    final_text = ""

    # 对应 TS query.ts:654 while (attemptWithFallback) 循环
    max_iterations = 20  # 防止无限循环
    for iteration in range(max_iterations):
        logger.debug("--- API loop iteration %d ---", iteration)
        text_chunks: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []
        iteration_usage: dict[str, int] | None = None
        assistant_started = False

        # 流式调用（带重试）
        for api_attempt in range(MAX_API_RETRIES + 1):
            try:
                if client_format == "anthropic":
                    stream = _call_anthropic_streaming(
                        client, model, system_prompt, current_messages, tools_schema, max_tokens,
                    )
                else:
                    stream = _call_openai_streaming(
                        client, model, system_prompt, current_messages, tools_schema, max_tokens,
                    )
                break
            except Exception as e:
                if api_attempt == MAX_API_RETRIES or not _is_retryable_error(e):
                    raise
                wait = 2 ** api_attempt
                logger.warning("API error (attempt %d/%d), retrying in %ds: %s",
                               api_attempt + 1, MAX_API_RETRIES + 1, wait, e)
                await asyncio.sleep(wait)

        async for event in stream:
            if event["type"] == "text":
                if not assistant_started and on_event:
                    on_event({"type": "assistant_text_started"})
                    assistant_started = True
                text_chunks.append(event["content"])
                if on_text:
                    on_text(event["content"])
            elif event["type"] == "tool_use":
                tool_use_blocks.append(event)
            elif event["type"] == "usage":
                iteration_usage = event["usage"]

        assistant_text = "".join(text_chunks)

        # 记录 token 用量
        if iteration_usage and cost_tracker:
            from termpilot.token_tracker import TokenUsage
            usage = TokenUsage(**iteration_usage)
            cost_tracker.add_usage(model, usage)
            logger.debug("usage: in=%d, out=%d, cache_write=%d, cache_read=%d",
                         usage.input_tokens, usage.output_tokens,
                         usage.cache_creation_input_tokens, usage.cache_read_input_tokens)

        # 没有工具调用 → 直接返回文本
        if not tool_use_blocks:
            final_text = assistant_text
            logger.debug("no tool_use blocks, returning text (%d chars)", len(final_text))
            break

        logger.debug("received %d tool_use blocks: %s",
                     len(tool_use_blocks),
                     ", ".join(tb["name"] for tb in tool_use_blocks))

        # --- 有工具调用，并发执行 ---

        # 1. 构造 assistant message（OpenAI tool_calls 格式）
        tool_calls = []
        for tb in tool_use_blocks:
            tool_calls.append({
                "id": tb["id"],
                "type": "function",
                "function": {
                    "name": tb["name"],
                    "arguments": json.dumps(tb["input"], ensure_ascii=False),
                },
            })

        current_messages.append({
            "role": "assistant",
            "content": assistant_text or None,
            "tool_calls": tool_calls,
        })

        # 写入 session（确保每轮中间状态持久化）
        if on_assistant_message:
            on_assistant_message(assistant_text, tool_calls)

        # 2. 并发执行工具调用
        tool_results = await _execute_tools_concurrent(
            tool_use_blocks, tools, on_tool_call, on_event,
            permission_context, on_permission_ask,
            session_id=session_id,
        )

        # 3. 将 tool results 作为独立的 role=tool 消息追加
        current_messages.extend(tool_results)
        if on_event:
            on_event({"type": "status_updated", "text": "Summarizing findings…"})

        final_text = assistant_text  # 保留最后一轮的文本
        logger.debug("tool results appended, messages in context: %d", len(current_messages))

    return final_text
