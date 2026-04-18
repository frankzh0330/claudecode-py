"""Agent 工具（子代理系统）。

对应 TS: tools/AgentTool/（~800 行）
支持子代理：Explore（只读探索）、Plan（架构规划）、general-purpose（通用）。

子代理在独立的上下文中运行，有自己的 system prompt 和工具集。
主代理通过 Agent 工具委派任务给子代理，子代理返回结果。

子代理使用完整的 query_with_tools 循环，可以递归调用工具：
LLM 调工具 → 拿结果 → 再调工具 → 循环，直到任务完成。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 内置代理类型
BUILTIN_AGENTS = {
    "Explore": {
        "description": "Fast agent specialized for exploring codebases. Use for finding files, searching code, and understanding project structure.",
        "prompt": (
            "You are a file search specialist. You excel at thoroughly navigating and exploring codebases.\n\n"
            "CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS\n"
            "You are STRICTLY PROHIBITED from creating, modifying, or deleting any files.\n"
            "Your role is EXCLUSIVELY to search and analyze existing code.\n\n"
            "Guidelines:\n"
            "- Use glob for broad file pattern matching\n"
            "- Use grep for searching file contents with regex\n"
            "- Use read_file when you know the specific file path\n"
            "- Use bash ONLY for read-only operations (ls, git status, git log, etc.)\n"
            "- Be thorough: search with multiple patterns if needed\n"
            "- Report your findings concisely"
        ),
        "tools": ["read_file", "glob", "grep", "bash"],
    },
    "Plan": {
        "description": "Software architect agent for designing implementation plans. Use for planning approach before coding.",
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
            "Output a clear, step-by-step plan with file paths and specific changes."
        ),
        "tools": ["read_file", "glob", "grep", "bash"],
    },
    "general-purpose": {
        "description": "General-purpose agent for complex, multi-step tasks that require autonomy.",
        "prompt": (
            "You are a general-purpose agent. Complete the task assigned to you.\n"
            "Use all available tools to accomplish your goal.\n"
            "Report your findings concisely when done."
        ),
        "tools": None,  # None means all tools
    },
}


class AgentTool:
    """Agent 工具：委派任务给子代理。"""

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        agent_lines = []
        for agent_type, info in BUILTIN_AGENTS.items():
            agent_lines.append(f"- {agent_type}: {info['description']}")
        agent_list = "\n".join(agent_lines)

        return (
            "Launch a specialized sub-agent to handle a task autonomously.\n\n"
            f"Available agent types:\n{agent_list}\n\n"
            "The agent runs in an isolated context with its own tool set and returns results when done."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "description": "Type of agent: 'Explore', 'Plan', or 'general-purpose'",
                    "enum": ["Explore", "Plan", "general-purpose"],
                },
                "description": {
                    "type": "string",
                    "description": "Short description of what the agent will do (3-5 words).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Detailed task description for the agent.",
                },
            },
            "required": ["subagent_type", "prompt"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        """执行子代理任务。"""
        subagent_type = kwargs.get("subagent_type", "general-purpose")
        prompt = kwargs.get("prompt", "")

        if not prompt:
            return "Error: Agent prompt is required."

        agent_config = BUILTIN_AGENTS.get(subagent_type)
        if not agent_config:
            return f"Error: Unknown agent type '{subagent_type}'"

        try:
            result = await self._run_agent(subagent_type, agent_config, prompt)
            return result
        except Exception as e:
            return f"Agent error: {e}"

    async def _run_agent(
            self,
            agent_type: str,
            config: dict[str, Any],
            prompt: str,
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
        client = create_client()
        model = get_effective_model()

        logger.debug("agent _run_agent: type=%s, tools=%d, prompt=%d chars",
                     agent_type, len(agent_tools), len(prompt))

        try:
            result = await query_with_tools(
                client=client,
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=agent_tools,
                max_tokens=8192,
                # 子代理不需要权限确认和 UI 回调
                on_text=None,
                on_tool_call=None,
                permission_context=None,
                on_permission_ask=None,
            )
            return result or "(agent returned no text)"

        except Exception as e:
            logger.debug("agent error: %s", e)
            return f"Agent API error: {e}"
