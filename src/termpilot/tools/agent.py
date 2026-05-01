"""Agent 工具（子代理系统）。

对应 TS: tools/AgentTool/（~800 行）
支持子代理：Explore（只读探索）、Plan（架构规划）、Verification（验证）、general-purpose（通用）。

子代理在独立的上下文中运行，有自己的 system prompt 和工具集。
主代理通过 Agent 工具委派任务给子代理，子代理返回结果。

子代理使用完整的 query_with_tools 循环，可以递归调用工具：
LLM 调工具 → 拿结果 → 再调工具 → 循环，直到任务完成。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from uuid import uuid4

from termpilot.config import get_config_home

logger = logging.getLogger(__name__)

MAX_BATCH_TASKS = 3
DEFAULT_SUBAGENT_TIMEOUT_SECONDS = 0

# 内置代理类型
BUILTIN_AGENTS = {
    "Explore": {
        "description": "Fast read-only agent specialized for pure codebase exploration, file discovery, code searches, and project structure analysis. Do not use for tasks where the user asks for a plan or implementation strategy.",
        "prompt": (
            "You are a file search specialist. You excel at thoroughly navigating and exploring codebases.\n\n"
            "CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS\n"
            "You are STRICTLY PROHIBITED from creating, modifying, or deleting any files.\n"
            "Your role is EXCLUSIVELY to search and analyze existing code.\n\n"
            "Guidelines:\n"
            "- Use list_dir for directory summaries before broad exploration\n"
            "- Use glob for broad file pattern matching\n"
            "- Use grep for searching file contents with regex\n"
            "- Use read_file when you know the specific file path\n"
            "- Use bash ONLY for read-only operations (ls, git status, git log, etc.)\n"
            "- Be thorough: search with multiple patterns if needed\n"
            "- Report your findings concisely\n"
            "- Describe the project from the TermPilot perspective. Do not mention reference "
            "implementations, upstream projects, comparison targets, Claude Code, or TypeScript "
            "versions unless the user explicitly asks about origins or comparisons.\n"
            "- If internal comments or docs mention a reference implementation, treat that as private "
            "implementation context and omit it from user-facing summaries."
        ),
        "tools": ["list_dir", "read_file", "glob", "grep", "bash"],
    },
    "Plan": {
        "description": "Software architect agent for designing implementation plans. Use this whenever the user asks to plan, design, or propose an implementation strategy, even if the plan requires exploring the codebase first. Returns step-by-step plans, identifies critical files, and considers architectural trade-offs.",
        "prompt": (
            "You are a software architect planning agent.\n\n"
            "Your job is to explore the codebase and create a detailed implementation plan.\n"
            "You have READ-ONLY access - do not modify any files.\n\n"
            "Approach:\n"
            "1. Understand the user's request and what needs to change\n"
            "2. Explore existing code to find relevant files and patterns\n"
            "3. Identify existing functions/utilities that should be reused\n"
            "4. Design the implementation approach step by step\n"
            "5. Consider architectural trade-offs\n\n"
            "Output a clear, step-by-step plan with file paths and specific changes.\n"
            "Describe the project from the TermPilot perspective. Do not mention reference "
            "implementations, upstream projects, comparison targets, Claude Code, or TypeScript "
            "versions unless the user explicitly asks about origins or comparisons.\n"
            "If internal comments or docs mention a reference implementation, treat that as private "
            "implementation context and omit it from user-facing summaries."
        ),
        "tools": ["list_dir", "read_file", "glob", "grep", "bash"],
    },
    "Verification": {
        "description": "Read-only verification agent for checking whether changes work as intended. Use after implementation work to inspect diffs, run tests, and identify regressions or missing coverage.",
        "prompt": (
            "You are a verification agent.\n\n"
            "Your job is to verify whether recent implementation work is correct.\n"
            "You may inspect files, review diffs, and run tests or read-only checks.\n"
            "Do not modify files.\n\n"
            "Approach:\n"
            "1. Understand what changed and what behavior should be verified\n"
            "2. Inspect relevant files and diffs\n"
            "3. Run targeted tests or checks when appropriate\n"
            "4. Report failures, risks, and missing coverage clearly\n\n"
            "Output concise findings first, followed by commands/checks performed.\n"
            "Describe the project from the TermPilot perspective. Do not mention reference "
            "implementations, upstream projects, comparison targets, Claude Code, or TypeScript "
            "versions unless the user explicitly asks about origins or comparisons.\n"
            "If internal comments or docs mention a reference implementation, treat that as private "
            "implementation context and omit it from user-facing summaries."
        ),
        "tools": ["list_dir", "read_file", "glob", "grep", "bash"],
    },
    "general-purpose": {
        "description": "General-purpose agent for complex, multi-step tasks that require autonomy.",
        "prompt": (
            "You are a general-purpose agent. Complete the task assigned to you.\n"
            "Use all available tools to accomplish your goal.\n"
            "Report your findings concisely when done.\n"
            "Describe the project from the TermPilot perspective. Do not mention reference "
            "implementations, upstream projects, comparison targets, Claude Code, or TypeScript "
            "versions unless the user explicitly asks about origins or comparisons.\n"
            "If internal comments or docs mention a reference implementation, treat that as private "
            "implementation context and omit it from user-facing summaries."
        ),
        "tools": None,  # None means all tools
    },
}


def _load_custom_agents() -> dict[str, dict[str, Any]]:
    """从 ~/.termpilot/agents/*.md 加载自定义 agent。"""
    agents_dir = get_config_home() / "agents"
    if not agents_dir.exists():
        return {}

    custom: dict[str, dict[str, Any]] = {}
    for md_file in sorted(agents_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            end = content.find("---", 3)
            if end == -1:
                continue
            frontmatter = content[3:end].strip()
            body = content[end + 3:].strip()
            meta: dict[str, str] = {}
            for line in frontmatter.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            name = meta.get("name", md_file.stem)
            description = meta.get("description", f"Custom agent: {name}")
            tools_str = meta.get("tools", "")
            tools = [t.strip() for t in tools_str.split(",") if t.strip()] or None
            custom[name] = {
                "description": description,
                "prompt": body or f"You are a {name} agent. Complete the assigned task.",
                "tools": tools,
            }
            logger.debug("loaded custom agent: %s from %s", name, md_file.name)
        except Exception as e:
            logger.debug("failed to load custom agent %s: %s", md_file.name, e)
    return custom


def _get_all_agents() -> dict[str, dict[str, Any]]:
    """返回所有 agent（内置 + 自定义）。"""
    agents = dict(BUILTIN_AGENTS)
    agents.update(_load_custom_agents())
    return agents


class AgentTool:
    """Agent 工具：委派任务给子代理。"""

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        agent_lines = []
        for agent_type, info in _get_all_agents().items():
            tools_desc = ", ".join(info["tools"]) if info.get("tools") else "All tools"
            agent_lines.append(f"- {agent_type}: {info['description']} (Tools: {tools_desc})")
        agent_list = "\n".join(agent_lines)

        return (
            "Delegate work to a specialized subagent. Think of this as delegate_task: "
            "spawn an isolated agent for exploration, planning, verification, or a bounded "
            "multi-step task, then return only that subagent's final summary.\n\n"
            "Subagents run in their own context with their own tool set. Their intermediate "
            "tool calls are not added to your main context, so delegation is useful when a "
            "side investigation would otherwise fill the main conversation.\n\n"
            f"Available agent types and the tools they have access to:\n{agent_list}\n\n"
            "For one task, specify subagent_type, description, and prompt. If subagent_type is "
            "omitted, general-purpose is used. For multiple independent directions, pass a "
            "tasks array with up to 3 delegated tasks; batch tasks run serially and each item "
            "returns its own success/result entry.\n\n"
            "By default, delegated agents run synchronously: wait for the subagent's final "
            "result and use that result before answering the user. Only set "
            "run_in_background=true when the user explicitly wants background work or when "
            "the task can safely complete later via notification.\n\n"
            "Agent routing rules:\n"
            "- Planning intent wins over exploration intent. If the user asks to plan, design, "
            "propose an approach, or add a feature with a plan, use subagent_type=Plan even "
            "when the agent must inspect the codebase first.\n"
            "- Use subagent_type=Explore for pure discovery or analysis requests such as "
            "understanding a repository, architecture, design patterns, command systems, "
            "or broad file relationships.\n"
            "- If the user asks to inspect, compare, or summarize multiple files/modules "
            "separately, use the tasks array with one Explore task per file/module.\n"
            "- Use subagent_type=Verification only for checking completed work, tests, diffs, "
            "or regressions.\n"
            "- Use general-purpose for complex multi-step execution that is not just planning, "
            "exploration, or verification.\n"
            "- Use custom agents from ~/.termpilot/agents/*.md when their descriptions match "
            "the task.\n\n"
            "When NOT to use this tool:\n"
            "- If you want to read one specific file path, use read_file instead\n"
            "- If the user names multiple files/modules and asks to inspect them separately, "
            "use agent.tasks instead of direct read_file calls\n"
            "- If you are searching for a specific class/function like 'class Foo', "
            "use grep instead\n"
            "- If you are searching within a specific file, use read_file instead\n"
            "- If the task can be completed with one or two direct tool calls, do it yourself\n\n"
            "Usage notes:\n"
            "- Always include a short description (3-5 words) summarizing what the agent will do\n"
            "- The agent runs in an isolated context and returns results when done. "
            "The result is not visible to the user — summarize it for them.\n"
            "- Clearly tell the agent whether to write code or just do research\n"
            "- If the agent description matches the user's task, use it without asking first\n\n"
            "Writing the prompt:\n"
            "Brief the agent like a smart colleague who just walked into the room — "
            "it hasn't seen this conversation. Explain what you're trying to accomplish and why. "
            "Give enough context for judgment calls rather than narrow instructions.\n\n"
            "Example:\n"
            "user: 'What design patterns does this project use?'\n"
            "-> Use subagent_type=Explore\n\n"
            "user: 'Help me plan adding a /undo command'\n"
            "-> Use subagent_type=Plan\n\n"
            "user: 'Verify the tests pass after my changes'\n"
            "-> Use subagent_type=Verification"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        all_agents = _get_all_agents()
        agent_types = list(all_agents.keys())
        task_item_schema = {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "description": f"Type of subagent to delegate to: {', '.join(agent_types)}",
                    "enum": agent_types,
                },
                "description": {
                    "type": "string",
                    "description": "Short description of this delegated task (3-7 words).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed standalone task brief for this subagent.",
                },
            },
            "required": ["prompt"],
        }
        return {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "description": f"Type of subagent for a single delegated task: {', '.join(agent_types)}",
                    "enum": agent_types,
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the agent will do (3-5 words).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task description for the agent.",
                },
                "tasks": {
                    "type": "array",
                    "description": (
                        "Optional batch of independent delegated tasks. Use this when there are "
                        "multiple separate exploration, planning, or verification directions. "
                        f"Maximum {MAX_BATCH_TASKS}; when present, top-level subagent_type/"
                        "description/prompt are ignored."
                    ),
                    "items": task_item_schema,
                    "maxItems": MAX_BATCH_TASKS,
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Set to true only when this delegation should run in the background. "
                        "Defaults to false, which blocks until the subagent result is available."
                    ),
                },
            },
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        """执行子代理任务。

        默认同步阻塞并返回完整结果；只有显式 run_in_background=true 时，
        才启动后台 agent 并返回 launched notification。
        """
        if kwargs.get("run_in_background") is True:
            return await self.call_async(**kwargs)

        tasks = kwargs.get("tasks")
        if isinstance(tasks, list) and tasks:
            return await self._run_batch(tasks)

        subagent_type = kwargs.get("subagent_type", "general-purpose")
        description = kwargs.get("description", "")
        prompt = kwargs.get("prompt", "")

        if not prompt:
            return "Error: Agent prompt is required."

        agent_config = _get_all_agents().get(subagent_type)
        if not agent_config:
            return f"Error: Unknown agent type '{subagent_type}'"

        try:
            parent_on_event = kwargs.get("_parent_on_event")
            if parent_on_event:
                result = await self._run_agent(
                    subagent_type,
                    agent_config,
                    prompt,
                    parent_on_event=parent_on_event,
                )
            else:
                result = await self._run_agent(subagent_type, agent_config, prompt)
            return result
        except Exception as e:
            return f"Agent error: {e}"

    async def _run_batch(self, tasks: list[Any]) -> str:
        """串行执行一批委派任务并汇总结果。"""
        if len(tasks) > MAX_BATCH_TASKS:
            return f"Error: agent.tasks supports at most {MAX_BATCH_TASKS} delegated tasks."

        all_agents = _get_all_agents()
        results: list[dict[str, Any]] = []

        for index, item in enumerate(tasks, start=1):
            if not isinstance(item, dict):
                results.append({
                    "index": index,
                    "success": False,
                    "error": "Task item must be an object.",
                })
                continue

            subagent_type = item.get("subagent_type") or "general-purpose"
            description = item.get("description", "")
            prompt = item.get("prompt", "")

            entry: dict[str, Any] = {
                "index": index,
                "subagent_type": subagent_type,
                "description": description,
            }

            if not prompt:
                entry.update({"success": False, "error": "Agent prompt is required."})
                results.append(entry)
                continue

            agent_config = all_agents.get(subagent_type)
            if not agent_config:
                entry.update({"success": False, "error": f"Unknown agent type '{subagent_type}'"})
                results.append(entry)
                continue

            try:
                result = await self._run_agent(subagent_type, agent_config, prompt)
                entry.update({
                    "success": not result.startswith("Agent API error:"),
                    "result": result,
                })
            except Exception as e:
                entry.update({"success": False, "error": str(e)})
            results.append(entry)

        failed = sum(1 for item in results if not item.get("success"))
        payload = {
            "delegated_tasks": results,
            "summary": {
                "total": len(results),
                "succeeded": len(results) - failed,
                "failed": failed,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ── 异步启动（REPL drain 模式用）─────────────────────

    async def call_async(self, **kwargs: Any) -> str:
        """异步启动子代理，立即返回，完成后通过队列通知。

        仅在显式 run_in_background=true 时使用。
        """
        tasks = kwargs.get("tasks")
        if isinstance(tasks, list) and tasks:
            return await self._run_batch_async(tasks)

        subagent_type = kwargs.get("subagent_type", "general-purpose")
        description = kwargs.get("description", "")
        prompt = kwargs.get("prompt", "")

        if not prompt:
            return "Error: Agent prompt is required."

        agent_config = _get_all_agents().get(subagent_type)
        if not agent_config:
            return f"Error: Unknown agent type '{subagent_type}'"

        agent_id = f"agent-{uuid4().hex[:8]}"
        self._launch_async_agent(agent_id, subagent_type, agent_config, prompt, description)
        return json.dumps({
            "status": "async_launched",
            "agent_id": agent_id,
            "subagent_type": subagent_type,
            "description": description,
        }, ensure_ascii=False)

    async def _run_batch_async(self, tasks: list[Any]) -> str:
        """异步启动多个子代理。"""
        if len(tasks) > MAX_BATCH_TASKS:
            return f"Error: agent.tasks supports at most {MAX_BATCH_TASKS} delegated tasks."

        all_agents = _get_all_agents()
        launched: list[dict[str, Any]] = []

        for i, item in enumerate(tasks):
            if not isinstance(item, dict):
                launched.append({
                    "agent_id": f"batch-{uuid4().hex[:8]}-{i}",
                    "description": "",
                    "status": "failed",
                    "error": "Task item must be an object.",
                })
                continue

            subagent_type = item.get("subagent_type") or "general-purpose"
            description = item.get("description", f"Task {i + 1}")
            prompt = item.get("prompt", "")
            agent_id = f"batch-{uuid4().hex[:8]}-{i}"

            if not prompt:
                launched.append({
                    "agent_id": agent_id,
                    "description": description,
                    "status": "failed",
                    "error": "Agent prompt is required.",
                })
                continue

            agent_config = all_agents.get(subagent_type)
            if not agent_config:
                launched.append({
                    "agent_id": agent_id,
                    "description": description,
                    "status": "failed",
                    "error": f"Unknown agent type '{subagent_type}'",
                })
                continue

            self._launch_async_agent(agent_id, subagent_type, agent_config, prompt, description)
            launched.append({
                "agent_id": agent_id,
                "description": description,
                "status": "launched",
            })

        return json.dumps({
            "status": "async_launched",
            "agents": launched,
            "total": len(launched),
        }, ensure_ascii=False, indent=2)

    async def _run_agent_and_notify(
            self,
            agent_id: str,
            agent_type: str,
            config: dict[str, Any],
            prompt: str,
            description: str = "",
    ) -> None:
        """后台运行子代理，完成后向主队列发送 task_notification。"""
        from termpilot.queue import QueuedCommand, Priority, get_main_queue
        from termpilot.tool_result_storage import persist_agent_result

        try:
            result = await self._run_agent(agent_type, config, prompt)
            persisted = persist_agent_result(result, agent_id, agent_type, description)
            get_main_queue().enqueue(QueuedCommand(
                mode="task_notification",
                value={
                    "agent_id": agent_id,
                    "subagent_type": agent_type,
                    "description": description,
                    "status": "completed",
                    "summary": persisted["summary"],
                    "result_path": persisted["filepath"],
                    "original_size": persisted["original_size"],
                    "has_more": persisted["has_more"],
                },
                priority=Priority.LATER,
                origin="agent",
            ))
        except Exception as e:
            get_main_queue().enqueue(QueuedCommand(
                mode="task_notification",
                value={
                    "agent_id": agent_id,
                    "subagent_type": agent_type,
                    "status": "failed",
                    "error": str(e),
                },
                priority=Priority.LATER,
                origin="agent",
            ))

    def _launch_async_agent(
            self,
            agent_id: str,
            agent_type: str,
            config: dict[str, Any],
            prompt: str,
            description: str = "",
    ) -> asyncio.Task:
        """创建并注册异步 agent task，返回 task 引用。"""
        from termpilot.queue import register_running_agent

        task = asyncio.create_task(
            self._run_agent_and_notify(agent_id, agent_type, config, prompt, description)
        )
        register_running_agent(task)
        return task

    async def _run_agent(
            self,
            agent_type: str,
            config: dict[str, Any],
            prompt: str,
            parent_on_event: Any = None,
    ) -> str:
        """运行子代理。

        使用完整的 query_with_tools 循环：
        1. 构建子代理的 system prompt
        2. 创建受限的工具集（排除 agent 自身防止无限嵌套）
        3. 调用 query_with_tools 实现递归工具调用
        """
        from termpilot.api import create_client, query_with_tools
        from termpilot.config import get_effective_model
        from termpilot.tools import get_all_tools

        # 构建工具集
        all_tools = get_all_tools()
        allowed_tool_names = config.get("tools")

        if allowed_tool_names is not None:
            agent_tools = [t for t in all_tools if t.name in allowed_tool_names]
        else:
            # general-purpose: 所有工具（但不再包含 agent 避免无限嵌套）
            agent_tools = [t for t in all_tools if t.name != "agent"]

        if not agent_tools:
            agent_tools = all_tools[:6]  # fallback

        # 构建 system prompt
        system_prompt = config["prompt"]

        # 添加环境信息
        from termpilot.context import get_system_context, get_git_status
        sys_ctx = get_system_context()
        system_prompt += f"\n\nEnvironment: {sys_ctx['os']}, cwd={sys_ctx['cwd']}"

        git_status = get_git_status()
        if git_status:
            system_prompt += f"\n\n{git_status}"

        # 添加任务描述
        messages = [{"role": "user", "content": prompt}]

        # 调用 API
        client, client_format = create_client()
        model = get_effective_model()

        logger.debug("agent _run_agent: type=%s, tools=%d, prompt=%d chars",
                     agent_type, len(agent_tools), len(prompt))

        reported_tools: set[str] = set()
        last_phase = ""

        def _subagent_event(event: dict[str, Any]) -> None:
            nonlocal last_phase
            if not parent_on_event:
                return
            event_type = event.get("type")
            if event_type == "tool_started":
                tool_name = event.get("name", "tool")
                if tool_name in reported_tools:
                    return
                reported_tools.add(tool_name)
                parent_on_event({
                    "type": "status_updated",
                    "text": f"{agent_type} agent: using {tool_name}…",
                })
            elif event_type in {"tool_finished", "tool_failed"}:
                if event_type != "tool_failed":
                    return
                parent_on_event({
                    "type": "status_updated",
                    "text": f"{agent_type} agent: tool failed, adjusting…",
                })
            elif event_type == "assistant_text_started":
                if last_phase == "summarizing":
                    return
                last_phase = "summarizing"
                parent_on_event({
                    "type": "status_updated",
                    "text": f"{agent_type} agent: summarizing…",
                })

        timeout_seconds = int(
            os.environ.get("TERMPILOT_SUBAGENT_TIMEOUT_SECONDS", "")
            or DEFAULT_SUBAGENT_TIMEOUT_SECONDS
        )

        try:
            agent_run = query_with_tools(
                client=client,
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=agent_tools,
                max_tokens=8192,
                # 子代理不直接渲染工具卡片，但会把阶段状态回传给父 UI。
                on_text=None,
                on_tool_call=None,
                on_event=_subagent_event if parent_on_event else None,
                permission_context=None,
                on_permission_ask=None,
                client_format=client_format,
            )
            if timeout_seconds > 0:
                result = await asyncio.wait_for(agent_run, timeout=timeout_seconds)
            else:
                result = await agent_run
            return result or "(agent returned no text)"
        except asyncio.TimeoutError:
            return (
                f"Agent API error: {agent_type} agent timed out after "
                f"{timeout_seconds} seconds. Try narrowing the task or set "
                "TERMPILOT_SUBAGENT_TIMEOUT_SECONDS to a larger value."
            )

        except Exception as e:
            logger.debug("agent error: %s", e)
            return f"Agent API error: {e}"
