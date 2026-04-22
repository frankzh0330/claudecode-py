"""Exit Plan Mode 工具。

对应 TS: tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts
退出规划模式：恢复之前的权限模式，将计划提交用户审批。
"""

from __future__ import annotations

from typing import Any

from termpilot.permissions import PermissionMode


class ExitPlanModeTool:
    """退出规划模式。"""

    @property
    def name(self) -> str:
        return "exit_plan_mode"

    @property
    def description(self) -> str:
        return (
            "Exit plan mode after completing exploration and design. "
            "Your plan will be presented to the user for approval before "
            "implementation begins."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "The implementation plan to present for user approval.",
                },
            },
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        plan = kwargs.get("plan", "")
        ctx = kwargs.get("permission_context")
        approved = kwargs.get("plan_approved")

        if approved is False:
            return (
                "The user rejected your plan. Stay in plan mode and revise.\n"
                "Consider the user's feedback and propose an alternative approach."
            )

        restore_mode = ctx.pre_plan_mode if ctx else PermissionMode.DEFAULT
        if ctx:
            ctx.mode = restore_mode
            ctx.pre_plan_mode = None

        if not plan.strip():
            return "Plan mode exited. You can now proceed."

        return (
            "User has approved your plan. You can now start coding.\n\n"
            f"## Approved Plan:\n{plan}"
        )
