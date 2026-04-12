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
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from cc_python.compact import auto_compact_if_needed
from cc_python.tool_result_storage import process_tool_result
from cc_python.config import (
    apply_settings_env,
    get_context_window,
    get_effective_api_key,
    get_effective_base_url,
)
from cc_python.hooks import HookEvent, dispatch_hooks
from cc_python.permissions import (
    PermissionBehavior,
    PermissionContext,
    PermissionResult,
    check_permission,
)
from cc_python.tools.base import Tool


# 对应 TS toolOrchestration.ts:8-12 getMaxToolUseConcurrency()
MAX_CONCURRENT_TOOLS = int(
    __import__("os").environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "10") or "10"
)


def _is_anthropic_format(base_url: str | None) -> bool:
    """判断是否使用 Anthropic API 格式。"""
    if not base_url:
        return True
    result = "/anthropic" in base_url
    logger.debug("API format detection: base_url=%s → %s", base_url, "anthropic" if result else "openai")
    return result


def create_client() -> tuple[Any, str]:
    """创建 API 客户端。对应 TS getAnthropicClient()。"""
    apply_settings_env()

    api_key = get_effective_api_key()
    if not api_key:
        raise ValueError(
            "未找到 API Key。请通过以下任一方式设置：\n"
            "  1. 环境变量: export ANTHROPIC_API_KEY=xxx\n"
            "  2. settings.json: {\"env\": {\"ANTHROPIC_API_KEY\": \"xxx\"}}"
        )

    base_url = get_effective_base_url()

    if _is_anthropic_format(base_url):
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
        )
        return client, "anthropic"
    else:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        return client, "openai"


async def _call_anthropic_streaming(
    client: Any,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
) -> AsyncGenerator[dict[str, Any], None]:
    """Anthropic 格式的流式 API 调用。

    对应 TS query.ts 中 deps.callModel 的 Anthropic 路径。
    yield 两种事件：
    - {"type": "text", "content": "..."} — 文本片段
    - {"type": "tool_use", "id": "...", "name": "...", "input": {...}} — 工具调用
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    # 收集 tool_use blocks（流式传输中 input 是分块的，需要累积）
    tool_use_blocks: dict[str, dict[str, Any]] = {}  # id -> {name, input_json_str}
    index_to_tool_id: dict[int, str] = {}  # content block index → tool_use id

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if event.type == "content_block_start":
                # tool_use 块开始 — 记录 id 和 index 的映射
                if hasattr(event.content_block, "type") and event.content_block.type == "tool_use":
                    tool_id = event.content_block.id
                    tool_use_blocks[tool_id] = {
                        "name": event.content_block.name,
                        "input_json": "",
                    }
                    index_to_tool_id[event.index] = tool_id

            elif event.type == "content_block_delta":
                if hasattr(event.delta, "text") and event.delta.text:
                    # 文本内容块 — 直接产出
                    yield {"type": "text", "content": event.delta.text}
                elif hasattr(event.delta, "partial_json") and event.delta.partial_json:
                    # tool_use input 分块 — 累积到对应的 block
                    tool_id = index_to_tool_id.get(event.index)
                    if tool_id and tool_id in tool_use_blocks:
                        tool_use_blocks[tool_id]["input_json"] += event.delta.partial_json

        # 流结束后，产出所有收集到的 tool_use blocks
        for block_id, block_info in tool_use_blocks.items():
            try:
                input_data = json.loads(block_info["input_json"]) if block_info["input_json"] else {}
            except json.JSONDecodeError:
                input_data = {}
            yield {
                "type": "tool_use",
                "id": block_id,
                "name": block_info["name"],
                "input": input_data,
            }


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


async def _execute_tools_concurrent(
    tool_use_blocks: list[dict[str, Any]],
    tools: list[Tool],
    on_tool_call: Any = None,
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
    from cc_python.tools import find_tool_by_name

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
            if on_tool_call:
                on_tool_call(tb["name"], tb["input"], result_text)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb["id"],
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
                    "type": "tool_result",
                    "tool_use_id": tb["id"],
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
                logger.debug("permission check: %s → %s (%s)", tb["name"], perm_result.behavior.value, perm_result.message)

                if perm_result.behavior == PermissionBehavior.DENY:
                    result_text = f"权限拒绝: {perm_result.message}"
                    if on_tool_call:
                        on_tool_call(tb["name"], tb["input"], result_text)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb["id"],
                        "content": result_text,
                    })
                    continue

                if perm_result.behavior == PermissionBehavior.ASK:
                    if on_permission_ask:
                        user_result = await on_permission_ask(
                            tb["name"], tb["input"], perm_result.message,
                        )
                        # 持久化用户选择的规则
                        if user_result.rule_updates:
                            from cc_python.permissions import PermissionRule, save_permission_rule
                            for update in user_result.rule_updates:
                                save_permission_rule(PermissionRule(
                                    tool_name=update["tool_name"],
                                    pattern=update.get("pattern", "*"),
                                    behavior=PermissionBehavior(update["behavior"]),
                                    source="user_settings",
                                ))

                        if user_result.behavior == PermissionBehavior.DENY:
                            result_text = f"权限拒绝: {user_result.message or '用户拒绝'}"
                            if on_tool_call:
                                on_tool_call(tb["name"], tb["input"], result_text)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tb["id"],
                                "content": result_text,
                            })
                            continue
                    else:
                        # 没有回调时默认拒绝
                        result_text = f"权限拒绝: 需要用户确认但无交互界面"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb["id"],
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
                try:
                    result_text = await tool.call(**tb["input"])
                except Exception as e:
                    result_text = f"工具执行错误: {e}"
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
            if on_tool_call:
                on_tool_call(tb["name"], tb["input"], result_text)
            return {
                "type": "tool_result",
                "tool_use_id": tb["id"],
                "content": result_text,
            }

        safe_results = await asyncio.gather(
            *[_run_safe(tb, tool) for tb, tool in safe_tasks],
            return_exceptions=False,
        )
        tool_results.extend(safe_results)

    # 2. 不安全的串行跑（对应 TS runToolsSerially）
    for tb, tool in unsafe_tasks:
        try:
            result_text = await tool.call(**tb["input"])
        except Exception as e:
            result_text = f"工具执行错误: {e}"
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
        if on_tool_call:
            on_tool_call(tb["name"], tb["input"], result_text)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tb["id"],
            "content": result_text,
        })

    return tool_results


async def query_with_tools(
    client: Any,
    client_format: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    max_tokens: int = 4096,
    on_text: Any = None,
    on_tool_call: Any = None,
    permission_context: PermissionContext | None = None,
    on_permission_ask: Any = None,
    session_id: str = "",
) -> str:
    """带工具调用的完整查询循环。

    对应 TS query.ts 的主循环（约第 554-863 行）+
    toolOrchestration.ts 的并发执行。

    核心流程：
    1. 调用 API，收集文本和 tool_use blocks
    2. 如果有 tool_use → 并发执行安全工具、串行执行不安全工具 → 追加消息 → 再次调用
    3. 循环直到没有 tool_use，返回最终文本

    Args:
        on_text: 流式文本回调（用于实时渲染）
        on_tool_call: 工具调用回调（用于显示工具执行过程）
        permission_context: 权限上下文
        on_permission_ask: 权限确认回调
    """
    from cc_python.tools import tool_to_api_schema

    tools_schema = [tool_to_api_schema(t) for t in tools]
    logger.debug("query_with_tools: model=%s, format=%s, tools=%d, messages=%d",
                 model, client_format, len(tools), len(messages))

    current_messages = list(messages)

    # 上下文压缩：在发送 API 前检查 token 数
    context_window = get_context_window()
    current_messages = await auto_compact_if_needed(
        current_messages, system_prompt,
        client, client_format, model,
        context_window=context_window,
    )
    final_text = ""

    # 对应 TS query.ts:654 while (attemptWithFallback) 循环
    max_iterations = 20  # 防止无限循环
    for iteration in range(max_iterations):
        logger.debug("--- API loop iteration %d ---", iteration)
        text_chunks: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []

        # 选择对应的流式调用
        if client_format == "anthropic":
            stream = _call_anthropic_streaming(
                client, model, system_prompt, current_messages, tools_schema, max_tokens,
            )
        else:
            stream = _call_openai_streaming(
                client, model, system_prompt, current_messages, tools_schema, max_tokens,
            )

        async for event in stream:
            if event["type"] == "text":
                text_chunks.append(event["content"])
                if on_text:
                    on_text(event["content"])
            elif event["type"] == "tool_use":
                tool_use_blocks.append(event)

        assistant_text = "".join(text_chunks)

        # 没有工具调用 → 直接返回文本
        if not tool_use_blocks:
            final_text = assistant_text
            logger.debug("no tool_use blocks, returning text (%d chars)", len(final_text))
            break

        logger.debug("received %d tool_use blocks: %s",
                     len(tool_use_blocks),
                     ", ".join(tb["name"] for tb in tool_use_blocks))

        # --- 有工具调用，并发执行 ---

        # 1. 构造 assistant message（包含文本 + tool_use content blocks）
        # 对应 TS query.ts:826-844
        assistant_content: list[dict[str, Any]] = []
        if assistant_text:
            assistant_content.append({"type": "text", "text": assistant_text})
        for tb in tool_use_blocks:
            assistant_content.append({
                "type": "tool_use",
                "id": tb["id"],
                "name": tb["name"],
                "input": tb["input"],
            })

        current_messages.append({"role": "assistant", "content": assistant_content})

        # 2. 并发执行工具调用
        # 对应 TS toolOrchestration.ts runTools()
        tool_results = await _execute_tools_concurrent(
            tool_use_blocks, tools, on_tool_call,
            permission_context, on_permission_ask,
            session_id=session_id,
        )

        # 3. 将 tool_result 作为 user message 追加
        # Anthropic API 要求 tool_result 放在 role=user 的 message 中
        current_messages.append({"role": "user", "content": tool_results})

        final_text = assistant_text  # 保留最后一轮的文本
        logger.debug("tool results appended, messages in context: %d", len(current_messages))

    return final_text
