"""Quiet terminal UI helpers."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ToolResultEntry:
    """Stored tool result for compact cards and /details."""

    index: int
    name: str
    input_data: dict[str, Any]
    result: str
    success: bool
    summary: str
    preview: str
    hidden_lines: int


class QuietUI:
    """Render a quieter Claude Code / Codex-like CLI experience."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._status = None
        self._status_text = ""
        self._next_tool_index = 1
        self.tool_results: list[ToolResultEntry] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "status_started":
            self.set_status(str(event.get("text", "")))
        elif event_type == "status_updated":
            self.set_status(str(event.get("text", "")))
        elif event_type == "status_cleared":
            self.clear_status()
        elif event_type == "assistant_text_started":
            self.clear_status()
        elif event_type == "permission_requested":
            self.clear_status()
        elif event_type == "tool_started":
            self.set_status(_status_for_tool(str(event.get("name", "")), event.get("input", {})))
        elif event_type in {"tool_finished", "tool_failed"}:
            self.clear_status()
            self._render_tool_card(
                name=str(event.get("name", "")),
                input_data=event.get("input", {}) or {},
                result=str(event.get("result", "")),
                success=event_type == "tool_finished",
            )

    def set_status(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._status is None:
            self._status = self.console.status(text, spinner="dots")
            self._status.__enter__()
        else:
            self._status.update(text)
        self._status_text = text

    def clear_status(self) -> None:
        if self._status is not None:
            self._status.__exit__(None, None, None)
            self._status = None
            self._status_text = ""

    def get_tool_result(self, token: str) -> ToolResultEntry | None:
        if token == "last":
            return self.tool_results[-1] if self.tool_results else None
        try:
            index = int(token)
        except ValueError:
            return None
        for entry in self.tool_results:
            if entry.index == index:
                return entry
        return None

    def format_tool_details(self, token: str) -> str:
        entry = self.get_tool_result(token)
        if entry is None:
            return "No matching tool result. Try `/details last`."

        args_text = _compact_text(str(entry.input_data))
        if not args_text:
            args_text = "{}"

        return (
            f"Tool #{entry.index}: `{entry.name}`\n"
            f"Summary: {entry.summary}\n"
            f"Args: `{args_text}`\n\n"
            "```text\n"
            f"{entry.result}\n"
            "```"
        )

    def _render_tool_card(self, name: str, input_data: dict[str, Any], result: str, success: bool) -> None:
        summary = _tool_summary(name, input_data, result)
        preview_lines = _preview_lines(name, result, success)
        preview = "\n".join(preview_lines)
        hidden_lines = max(0, len(result.splitlines()) - len(preview_lines))

        entry = ToolResultEntry(
            index=self._next_tool_index,
            name=name,
            input_data=input_data,
            result=result,
            success=success,
            summary=summary,
            preview=preview,
            hidden_lines=hidden_lines,
        )
        self.tool_results.append(entry)
        self._next_tool_index += 1

        status_text = "completed" if success else "failed"
        header = Text()
        header.append(f"{entry.index}. {name}", style="bold cyan")
        header.append(f"  {status_text}", style="green" if success else "red")

        body = Text()
        body.append(f"{summary}\n", style="bold")
        if preview:
            body.append(preview)
            body.append("\n")
        if hidden_lines > 0:
            body.append(f"+{hidden_lines} lines hidden", style="dim")
            body.append("\n")
        body.append(f"Use /details {entry.index} to view full output", style="dim")

        self.console.print()
        self.console.print(Panel(body, title=header, border_style="blue" if success else "red"))


def _status_for_tool(name: str, input_data: dict[str, Any]) -> str:
    if name == "list_dir":
        return "Inspecting project structure…"
    if name in {"glob", "grep"}:
        return "Inspecting project files…"
    if name == "read_file":
        return "Reading key files…"
    if name == "bash":
        command = str(input_data.get("command", "")).strip()
        lowered = command.lower()
        if any(token in lowered for token in ("find ", "ls ", "tree", "rg ", "grep ")):
            return "Inspecting project structure…"
        return "Running command…"
    return "Working…"


def _tool_summary(name: str, input_data: dict[str, Any], result: str) -> str:
    if name == "list_dir":
        path = input_data.get("path") or "."
        return f"Scanned directory `{path}`"
    if name == "glob":
        return f"Matched files for `{input_data.get('pattern', '')}`"
    if name == "grep":
        return f"Searched text for `{input_data.get('pattern', '')}`"
    if name == "read_file":
        return f"Read `{input_data.get('file_path', '')}`"
    if name == "bash":
        command = _compact_text(str(input_data.get("command", "")), limit=72)
        return f"Ran `{command}`"
    if name == "write_file":
        return f"Wrote `{input_data.get('file_path', '')}`"
    if name == "edit_file":
        return f"Edited `{input_data.get('file_path', '')}`"
    if not result.strip():
        return f"Executed `{name}`"
    return f"Executed `{name}`"


def _preview_lines(name: str, result: str, success: bool) -> list[str]:
    lines = [line.rstrip() for line in result.splitlines() if line.strip()]
    if not lines:
        return ["(no output)"] if success else ["Tool returned no output."]

    if not success:
        return lines[:6]

    if name in {"list_dir", "glob", "grep", "read_file"}:
        return lines[:6]

    if name == "bash":
        if _looks_like_listing(lines):
            return [_summarize_listing(lines)]
        return lines[:5]

    return lines[:5]


def _summarize_listing(lines: list[str]) -> str:
    file_count = len(lines)
    sample = ", ".join(_compact_text(line, limit=28) for line in lines[:3])
    return f"Produced {file_count} lines of output ({sample})"


def _looks_like_listing(lines: list[str]) -> bool:
    if len(lines) < 3:
        return False
    path_like = 0
    for line in lines[:10]:
        if "/" in line or line.startswith((".", "-", "d", "l")):
            path_like += 1
    return path_like >= math.ceil(min(10, len(lines)) / 2)


def _compact_text(text: str, limit: int = 120) -> str:
    normalized = _WHITESPACE_RE.sub(" ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
