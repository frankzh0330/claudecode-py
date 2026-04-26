"""Enter Plan Mode 工具。

对应 TS: tools/EnterPlanModeTool/EnterPlanModeTool.ts
进入规划模式：保存当前权限模式，切换到 plan 模式（只读）。
"""

from __future__ import annotations

from typing import Any

from termpilot.permissions import PermissionMode


class EnterPlanModeTool:
    """进入规划模式。"""

    @property
    def name(self) -> str:
        return "enter_plan_mode"

    @property
    def description(self) -> str:
        return (
            "Use this tool proactively when you're about to start a non-trivial "
            "implementation task. Getting user sign-off on your approach before "
            "writing code prevents wasted effort and ensures alignment. This tool "
            "transitions you into plan mode where you can explore the codebase and "
            "design an implementation approach for user approval."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        ctx = kwargs.get("permission_context")
        if ctx:
            ctx.pre_plan_mode = ctx.mode
            ctx.mode = PermissionMode.PLAN

        return (
            "Entered plan mode. You now have READ-ONLY access.\n\n"
            "In plan mode, you should:\n"
            "1. Thoroughly explore the codebase to understand existing patterns\n"
            "2. Identify similar features and architectural approaches\n"
            "3. Consider multiple approaches and their trade-offs\n"
            "4. Use AskUserQuestion if you need to clarify the approach\n"
            "5. Design a concrete implementation strategy\n"
            "6. When ready, call exit_plan_mode to present your plan for approval\n\n"
            "Remember: DO NOT write or edit any files. This is read-only exploration."
        )
