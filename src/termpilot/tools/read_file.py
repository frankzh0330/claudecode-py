"""文件读取工具。

对应 TS: tools/FileReadTool/FileReadTool.ts
简化版：读取文本文件，支持 offset/limit 分段读取。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class ReadFileTool:
    """读取文件内容。

    对应 TS FileReadTool，简化了图片/PDF/notebook 支持。
    is_concurrency_safe=True：只读操作，可并行。
    """

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
            "Assume this tool is able to read all files on the machine.\n"
            "\n"
            "Usage:\n"
            "- The file_path parameter must be an absolute path, not a relative path\n"
            "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
            "- You can optionally specify a line offset and limit (especially handy for long files), "
            "but it's recommended to read the whole file by not providing these parameters\n"
            "- Results are returned using cat -n format, with line numbers starting at 1\n"
            "- This tool can only read files, not directories. To inspect a directory, use the list_dir tool.\n"
            "- If you read a file that exists but has empty contents you will receive a system reminder warning "
            "in place of file contents."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件绝对路径",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（从 1 开始），不指定则从第一行开始",
                },
                "limit": {
                    "type": "integer",
                    "description": "读取的最大行数，不指定则读取全部",
                },
            },
            "required": ["file_path"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        file_path = kwargs.get("file_path", "")
        offset = kwargs.get("offset") or 1
        limit = kwargs.get("limit")

        path = Path(file_path).expanduser()

        if not path.exists():
            return f"错误：文件不存在: {file_path}"
        if not path.is_file():
            return f"错误：不是文件: {file_path}"

        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        except PermissionError:
            return f"错误：无权限读取: {file_path}"

        lines = text.splitlines()
        total_lines = len(lines)
        start = max(1, offset) - 1  # 转为 0-based
        end = start + limit if limit else total_lines
        selected = lines[start:end]

        # 对应 TS FileReadTool formatFileLines() — cat -n 风格带行号
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>6}\t{line}")

        result = "\n".join(numbered)

        # 补充元信息
        meta = f"文件: {file_path} | 总行数: {total_lines} | 显示: {offset}-{min(offset + len(selected) - 1, total_lines)}"
        return f"{meta}\n{result}"
