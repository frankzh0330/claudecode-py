"""Hooks 系统 — 用户可配置的 shell 命令钩子。

对应 TS:
- services/hooks/index.ts (Hooks 系统入口)
- services/hooks/HookRunner.ts (Hook 执行器)
- services/hooks/HookTypes.ts (Hook 类型定义)
- services/hooks/types.ts (Hook 事件枚举)

TS 版支持 33 个事件 + 6 种 hook 类型（command/prompt/http/agent/function/callback），
Python 简化版保留核心：
- 5 个事件: PreToolUse, PostToolUse, UserPromptSubmit, Stop, SessionStart
- 1 种类型: command（shell 命令，覆盖绝大多数使用场景）

配置格式与 TS 版一致，同一份 settings.json 可同时用于 TS 和 Python 版。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from cc_python.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类型
# 对应 TS: services/hooks/types.ts + services/hooks/HookTypes.ts
# ---------------------------------------------------------------------------

class HookEvent(str, Enum):
    """Hook 事件类型。对应 TS HookEventName。"""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    SESSION_START = "SessionStart"


@dataclass
class HookConfig:
    """单个 hook 命令配置。"""

    type: str            # "command"
    command: str         # Shell 命令
    timeout: int = 30    # 超时秒数
    is_async: bool = False  # 是否异步执行（不阻塞主流程）


@dataclass
class HookMatcher:
    """Hook 匹配器：可选的工具名模式 + hook 列表。"""

    matcher: str | None  # 工具名匹配模式，如 "Bash"、"write_file"
    hooks: list[HookConfig]


@dataclass
class HookResult:
    """Hook 执行结果。

    Exit code 语义（与 TS 一致）:
    - 0: 成功，stdout 可能有 JSON 响应
    - 2: 阻塞错误，拒绝/阻止操作
    - 其他: 非阻塞错误，记录并继续
    """

    exit_code: int
    stdout: str
    stderr: str
    # 从 stdout JSON 解析
    decision: str | None = None       # "allow" / "deny"
    reason: str | None = None
    updated_input: dict | None = None


# ---------------------------------------------------------------------------
# 配置加载
# 对应 TS: services/hooks/getHooks.ts + utils/hooks/resolveHookConfig.ts
# ---------------------------------------------------------------------------

def _parse_hook_config(raw: dict[str, Any]) -> HookConfig | None:
    """从 JSON 对象解析 HookConfig。"""
    hook_type = raw.get("type", "command")
    command = raw.get("command", "")
    if not command:
        return None
    return HookConfig(
        type=hook_type,
        command=command,
        timeout=int(raw.get("timeout", 30)),
        is_async=bool(raw.get("async", False)),
    )


def _parse_hook_matcher(raw: dict[str, Any]) -> HookMatcher | None:
    """从 JSON 对象解析 HookMatcher。"""
    matcher = raw.get("matcher") or None
    raw_hooks = raw.get("hooks", [])
    hooks: list[HookConfig] = []
    for rh in raw_hooks:
        if not isinstance(rh, dict):
            continue
        hc = _parse_hook_config(rh)
        if hc is not None:
            hooks.append(hc)
    if not hooks:
        return None
    return HookMatcher(matcher=matcher, hooks=hooks)


def load_hooks_config() -> dict[HookEvent, list[HookMatcher]]:
    """从 settings.json 读取 hooks 配置。

    格式:
    {
      "hooks": {
        "PreToolUse": [
          {"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}
        ],
        "UserPromptSubmit": [
          {"hooks": [{"type": "command", "command": "..."}]}
        ]
      }
    }
    """
    settings = get_settings()
    raw_hooks = settings.get("hooks", {})
    if not isinstance(raw_hooks, dict):
        return {}

    result: dict[HookEvent, list[HookMatcher]] = {}
    for event_name, matchers in raw_hooks.items():
        try:
            event = HookEvent(event_name)
        except ValueError:
            # 忽略不支持的事件
            continue
        if not isinstance(matchers, list):
            continue
        parsed: list[HookMatcher] = []
        for m in matchers:
            if not isinstance(m, dict):
                continue
            hm = _parse_hook_matcher(m)
            if hm is not None:
                parsed.append(hm)
        if parsed:
            result[event] = parsed
    return result


# ---------------------------------------------------------------------------
# 匹配器
# 对应 TS: services/hooks/matcher.ts
# ---------------------------------------------------------------------------

def _get_matching_hooks(
    event: HookEvent,
    tool_name: str | None = None,
) -> list[HookConfig]:
    """获取匹配的 hook 列表。

    matcher 匹配逻辑:
    - None / "" / "*" → 匹配所有
    - "Bash" / "bash" → 匹配 tool_name（不区分大小写）
    """
    config = load_hooks_config()
    matchers = config.get(event, [])
    result: list[HookConfig] = []

    for hm in matchers:
        if hm.matcher is None or hm.matcher in ("", "*"):
            # 匹配所有
            result.extend(hm.hooks)
        elif tool_name and hm.matcher.lower() == tool_name.lower():
            # 工具名匹配（不区分大小写）
            result.extend(hm.hooks)

    return result


# ---------------------------------------------------------------------------
# 子进程执行
# 对应 TS: services/hooks/runCommandHook.ts
# ---------------------------------------------------------------------------

def _build_hook_input(
    event: HookEvent,
    session_id: str = "",
    cwd: str = "",
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_use_id: str | None = None,
    tool_response: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """构建传给 hook 的 stdin JSON。

    对应 TS hookInputSchema，格式:
    {
      "session_id": "...",
      "cwd": "/current/working/directory",
      "hook_event_name": "PreToolUse",
      "tool_name": "bash",
      "tool_input": {"command": "ls"},
      "tool_use_id": "..."
    }
    """
    data: dict[str, Any] = {
        "session_id": session_id,
        "cwd": cwd,
        "hook_event_name": event.value,
    }
    if tool_name is not None:
        data["tool_name"] = tool_name
    if tool_input is not None:
        data["tool_input"] = tool_input
    if tool_use_id is not None:
        data["tool_use_id"] = tool_use_id
    if tool_response is not None:
        data["tool_response"] = tool_response
    if prompt is not None:
        data["prompt"] = prompt
    return data


def _parse_hook_stdout(stdout: str) -> dict[str, Any]:
    """从 hook stdout 解析 JSON 响应。

    Hook 可能输出多行，查找第一行包含 { ... } 的 JSON 对象。
    """
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _build_result(exit_code: int, stdout: str, stderr: str) -> HookResult:
    """从子进程输出构建 HookResult。"""
    parsed = _parse_hook_stdout(stdout)
    return HookResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        decision=parsed.get("decision"),
        reason=parsed.get("reason"),
        updated_input=parsed.get("updatedInput"),
    )


async def _execute_command_hook(
    command: str,
    input_json: dict[str, Any],
    timeout: int = 30,
) -> HookResult:
    """执行单个 command hook。

    1. 启动子进程
    2. 通过 stdin 写入 JSON
    3. 等待完成（带超时）
    4. 解析 stdout JSON
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        input_bytes = json.dumps(input_json).encode("utf-8")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_bytes),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return HookResult(
                exit_code=-1,
                stdout="",
                stderr=f"Hook timed out after {timeout}s: {command}",
            )

        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode if proc.returncode is not None else -1

        return _build_result(exit_code, stdout_str, stderr_str)

    except Exception as e:
        return HookResult(
            exit_code=-1,
            stdout="",
            stderr=f"Hook execution failed: {e}",
        )


# ---------------------------------------------------------------------------
# 事件分发
# 对应 TS: services/hooks/index.ts dispatchHooks()
# ---------------------------------------------------------------------------

async def dispatch_hooks(
    event: HookEvent,
    session_id: str = "",
    cwd: str = "",
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_use_id: str | None = None,
    tool_response: str | None = None,
    prompt: str | None = None,
) -> list[HookResult]:
    """Hooks 分发主入口。

    1. 加载配置，获取匹配的 hooks
    2. 构建 stdin JSON
    3. 执行同步 hooks（遇到阻塞结果短路返回）
    4. 异步 hooks 不 await，直接 spawn
    5. 返回所有同步 hook 的结果
    """
    matching = _get_matching_hooks(event, tool_name)
    if not matching:
        return []

    input_json = _build_hook_input(
        event=event,
        session_id=session_id,
        cwd=cwd or str(Path.cwd()),
        tool_name=tool_name,
        tool_input=tool_input,
        tool_use_id=tool_use_id,
        tool_response=tool_response,
        prompt=prompt,
    )

    results: list[HookResult] = []

    for hook in matching:
        if hook.type != "command":
            # 仅支持 command 类型，其他类型跳过
            continue

        if hook.is_async:
            # 异步 hook：spawn 但不等待
            asyncio.create_task(_execute_command_hook(
                hook.command, input_json, hook.timeout,
            ))
            continue

        # 同步 hook：等待执行
        result = await _execute_command_hook(
            hook.command, input_json, hook.timeout,
        )

        # 非阻塞错误：记录日志
        if result.exit_code != 0 and result.exit_code != 2:
            if result.stderr:
                logger.warning(
                    "Hook %s returned exit_code=%d: %s",
                    hook.command[:50], result.exit_code, result.stderr[:200],
                )

        results.append(result)

        # 阻塞结果：短路返回
        if result.exit_code == 2 or result.decision == "deny":
            break

    return results
