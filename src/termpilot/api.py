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
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from termpilot.compact import auto_compact_if_needed
from termpilot.tool_result_storage import process_tool_result
from termpilot.config import (
    apply_settings_env,
    get_context_window,
    get_effective_api_key,
    get_effective_base_url,
    get_effective_provider,
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


def create_client() -> Any:
    """创建 OpenAI-compatible API 客户端。对应 TS getAnthropicClient()。

    所有 provider 统一使用 OpenAI SDK，因为绝大多数 LLM 平台
    都兼容 OpenAI API 格式（包括 Anthropic 的 Messages API 兼容端点）。
    """
    apply_settings_env()

    provider = get_effective_provider()
    api_key = get_effective_api_key(provider)
    settings_path = get_settings_path()

    if not api_key or is_placeholder_key(api_key):
        sys.exit(
            f"API key not configured.\n"
            f"Edit {settings_path} and replace the placeholder with your real key.\n"
            f"Then run termpilot again."
        )

    base_url = get_effective_base_url(provider)

    try:
        from openai import AsyncOpenAI
    except ImportError:
        sys.exit("OpenAI SDK not installed. Run: pip install termpilot")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    return client


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

    stream = await client.chat.completions.create(**kwargs)

    # 收集 tool_calls
    tool_call_buffers: dict[int, dict[str, Any]] = {}  # index -> {id, name, arguments}

    async for chunk in stream:
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
                try:
                    result_text = await tool.call(**tb["input"])
                    success = True
                except Exception as e:
                    result_text = f"工具执行错误: {e}"
                    success = False
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
        try:
            result_text = await tool.call(**tb["input"])
            success = True
        except Exception as e:
            result_text = f"工具执行错误: {e}"
            success = False
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

        stream = _call_openai_streaming(
            client, model, system_prompt, current_messages, tools_schema, max_tokens,
        )

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
