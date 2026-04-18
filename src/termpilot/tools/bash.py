"""Shell 命令执行工具。

对应 TS: tools/BashTool/BashTool.tsx
"""

from __future__ import annotations

import asyncio
from typing import Any


class BashTool:
    """执行 shell 命令并返回输出。

    is_concurrency_safe=False：bash 命令可能有副作用。
    TS 版中通过 isReadOnly(input) 判断，Python 简化版默认 False。
    """

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Executes a given bash command and returns its output.\n"
            "\n"
            "The working directory persists between commands, but shell state does not. "
            "The shell environment is initialized from the user's profile (bash or zsh).\n"
            "\n"
            "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, "
            "`awk`, or `echo` commands, unless explicitly instructed or after you have verified that "
            "a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool:\n"
            " - Directory summary: Use list_dir (NOT ls, find, or tree)\n"
            " - File search: Use Glob (NOT find or ls)\n"
            " - Content search: Use Grep (NOT grep or rg)\n"
            " - Read files: Use Read (NOT cat/head/tail)\n"
            " - Edit files: Use Edit (NOT sed/awk)\n"
            " - Write files: Use Write (NOT echo/cat <<EOF)\n"
            " - Communication: Output text directly (NOT echo/printf)\n"
            "\n"
            "Instructions:\n"
            "- Always quote file paths that contain spaces with double quotes\n"
            "- You may specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). "
            "By default, your command will timeout after 120000ms (2 minutes).\n"
            "- When issuing multiple commands: if independent, make separate calls in parallel; "
            "if dependent, use && to chain them.\n"
            "- For git commands: prefer creating a new commit over amending. "
            "Before destructive operations, consider safer alternatives."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（毫秒），默认 120000（2 分钟）",
                },
            },
            "required": ["command"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        command = kwargs.get("command", "")
        timeout_ms = kwargs.get("timeout") or 120000
        timeout_sec = timeout_ms / 1000

        if not command.strip():
            return "错误：命令为空"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"错误：命令超时（{timeout_sec}秒）: {command}"
        except Exception as e:
            return f"错误：执行失败: {e}"

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        parts = []
        if stdout_text.strip():
            parts.append(stdout_text)
        if stderr_text.strip():
            parts.append(f"[stderr]\n{stderr_text}")
        if proc.returncode != 0:
            parts.append(f"[exit code: {proc.returncode}]")

        return "\n".join(parts) if parts else f"[exit code: {proc.returncode}]"
