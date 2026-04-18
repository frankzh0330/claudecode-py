"""Directory summary tool for quiet project exploration."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any


class ListDirTool:
    """Summarize a directory without dumping a full ls/tree listing."""

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "Summarizes a directory on the local filesystem.\n"
            "Use this instead of bash `ls`, `find`, or `tree` when you need to understand a project layout.\n"
            "It returns a concise overview: counts, key entries, and common file types."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to inspect. Defaults to the current working directory.",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum number of top-level entries to include. Defaults to 20.",
                },
            },
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def call(self, **kwargs: Any) -> str:
        raw_path = kwargs.get("path") or "."
        max_entries = int(kwargs.get("max_entries") or 20)
        path = Path(raw_path).expanduser()

        if not path.exists():
            return f"错误：目录不存在: {raw_path}"
        if not path.is_dir():
            return f"错误：不是目录: {raw_path}"

        try:
            return await asyncio.to_thread(self._summarize, path, max_entries)
        except PermissionError:
            return f"错误：无权限读取目录: {raw_path}"

    def _summarize(self, path: Path, max_entries: int) -> str:
        entries = sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        directories = [entry for entry in entries if entry.is_dir()]
        files = [entry for entry in entries if entry.is_file()]

        lines = [
            f"Directory: {path}",
            f"Top-level entries: {len(entries)} ({len(directories)} dirs, {len(files)} files)",
        ]

        if entries:
            lines.append("Key entries:")
            for entry in entries[:max_entries]:
                label = "[D]" if entry.is_dir() else "[F]"
                lines.append(f"  {label} {entry.name}")
            if len(entries) > max_entries:
                lines.append(f"  ... and {len(entries) - max_entries} more")

        suffix_counts = Counter(
            entry.suffix.lower() or "<no-ext>"
            for entry in files
        )
        if suffix_counts:
            lines.append("Common file types:")
            for suffix, count in suffix_counts.most_common(6):
                lines.append(f"  {suffix}: {count}")

        important = [
            name for name in (
                "src", "app", "backend", "frontend", "tests", "scripts",
                "pyproject.toml", "requirements.txt", "README.md", "AGENTS.md",
            )
            if (path / name).exists()
        ]
        if important:
            lines.append("Notable paths:")
            for item in important:
                lines.append(f"  - {item}")

        return "\n".join(lines)
