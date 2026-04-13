"""文件编辑工具（精确字符串替换）。

对应 TS: tools/FileEditTool/FileEditTool.ts
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cc_python.tools.base import Tool


class EditFileTool:
    """精确字符串替换编辑文件。

    is_concurrency_safe=False：有副作用（修改文件）。
    """

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Performs exact string replacements in files.\n"
            "\n"
            "Usage:\n"
            "- You must use your Read tool at least once in the conversation before editing. "
            "This tool will error if you attempt an edit without reading the file.\n"
            "- When editing text from Read tool output, ensure you preserve the exact indentation "
            "(tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: "
            "spaces + line number + arrow. Everything after that is the actual file content to match. "
            "Never include any part of the line number prefix in the old_string or new_string.\n"
            "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n"
            "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string "
            "with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n"
            "- Use `replace_all` for replacing and renaming strings across the file."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件绝对路径",
                },
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原文本（必须在文件中唯一匹配）",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新文本",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有匹配项（默认 false）",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False

    async def call(self, **kwargs: Any) -> str:
        file_path = kwargs.get("file_path", "")
        old_string = kwargs.get("old_string", "")
        new_string = kwargs.get("new_string", "")
        replace_all = kwargs.get("replace_all", False)

        path = Path(file_path).expanduser()

        if not path.exists():
            return f"错误：文件不存在: {file_path}"

        # 修改前保存快照（用于 /undo 回退）
        from cc_python.undo import save_snapshot
        save_snapshot(file_path)

        def _edit() -> str:
            content = path.read_text(encoding="utf-8")
            count = content.count(old_string)
            if count == 0:
                return "错误：未找到要替换的文本。请检查 old_string 是否和文件内容完全一致。"
            if not replace_all and count > 1:
                return f"错误：找到 {count} 处匹配，old_string 必须唯一。请扩大上下文使其唯一，或设置 replace_all=true。"

            if replace_all:
                new_content = content.replace(old_string, new_string)
                path.write_text(new_content, encoding="utf-8")
                return f"已编辑: {file_path} (替换了 {count} 处)"
            else:
                new_content = content.replace(old_string, new_string, 1)
                path.write_text(new_content, encoding="utf-8")
                return f"已编辑: {file_path} (替换了 1 处)"

        try:
            return await asyncio.to_thread(_edit)
        except PermissionError:
            return f"错误：无权限写入: {file_path}"
