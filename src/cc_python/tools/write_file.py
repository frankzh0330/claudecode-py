"""文件写入工具。

对应 TS: tools/FileWriteTool/FileWriteTool.ts
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cc_python.tools.base import Tool


class WriteFileTool:
    """写入文件。如果文件已存在则覆盖，不存在则创建（含父目录）。

    is_concurrency_safe=False：有副作用（写入文件）。
    """

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Writes a file to the local filesystem.\n"
            "\n"
            "Usage:\n"
            "- This tool will overwrite the existing file if there is one at the provided path.\n"
            "- If this is an existing file, you MUST use the Read tool first to read the file's contents. "
            "This tool will fail if you did not read the file first.\n"
            "- Prefer the Edit tool for modifying existing files — it only sends the diff. "
            "Only use this tool to create new files or for complete rewrites.\n"
            "- NEVER create documentation files (*.md) or README files unless explicitly requested by the User."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件绝对路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整文件内容",
                },
            },
            "required": ["file_path", "content"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")

        path = Path(file_path).expanduser()

        # 修改前保存快照（用于 /undo 回退）
        from cc_python.undo import save_snapshot
        save_snapshot(file_path)

        def _write() -> str:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return f"已写入: {file_path} ({line_count} 行, {len(content)} 字符)"

        try:
            return await asyncio.to_thread(_write)
        except PermissionError:
            return f"错误：无权限写入: {file_path}"
        except OSError as e:
            return f"错误：写入失败: {e}"
