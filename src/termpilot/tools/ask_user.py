"""AskUserQuestion 工具。

对应 TS: tools/AskUserQuestionTool/
让模型在执行过程中向用户提问，获取反馈或确认。

模型通过此工具向用户展示选择题，用户选择后结果返回给模型。
"""

from __future__ import annotations

import builtins
from typing import Any


class AskUserQuestionTool:
    """向用户提问的工具。"""

    @property
    def name(self) -> str:
        return "ask_user_question"

    @property
    def description(self) -> str:
        return (
            "Asks the user multiple choice questions to gather information, "
            "clarify ambiguity, understand preferences, make decisions or offer them choices. "
            "Use this tool when you need to ask the user questions during execution."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The complete question to ask the user.",
                            },
                            "header": {
                                "type": "string",
                                "description": "Very short label (max 12 chars) for the question.",
                            },
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "Display text for this option (1-5 words).",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "Explanation of what this option means.",
                                        },
                                    },
                                    "required": ["label", "description"],
                                },
                                "description": "Available choices (2-4 options).",
                                "minItems": 2,
                                "maxItems": 4,
                            },
                            "multiSelect": {
                                "type": "boolean",
                                "description": "Allow multiple selections. Default false.",
                                "default": False,
                            },
                        },
                        "required": ["question", "header", "options", "multiSelect"],
                    },
                    "minItems": 1,
                    "maxItems": 4,
                    "description": "Questions to ask the user (1-4 questions).",
                },
            },
            "required": ["questions"],
        }

    @property
    def is_concurrency_safe(self) -> bool:
        return False  # 需要用户交互，不能并行

    async def call(self, **kwargs: Any) -> str:
        """执行提问。

        由于 Python 版在 CLI 中没有复杂的 UI 组件，
        此工具通过 Rich 在终端渲染问题，等待用户输入。
        """
        questions = kwargs.get("questions", [])
        if not questions:
            return "Error: No questions provided."

        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console()
        answers = {}

        for q in questions:
            question_text = q.get("question", "")
            header = q.get("header", "?")
            options = q.get("options", [])
            multi_select = q.get("multiSelect", False)

            console.print()
            panel_content = Text(question_text)
            console.print(Panel(panel_content, title=f"[bold]{header}[/]", border_style="yellow"))

            # 显示选项
            console.print()
            for i, opt in enumerate(options):
                label = opt.get("label", "")
                desc = opt.get("description", "")
                console.print(f"  [{i + 1}] {label}")
                if desc:
                    console.print(f"      [dim]{desc}[/]")

            console.print()
            try:
                if multi_select:
                    console.print("[dim](多选，用逗号分隔，如 1,3)[/]")
                    console.file.flush()
                    choice = builtins.input("选择: ").strip()
                    selected = []
                    for c in choice.split(","):
                        c = c.strip()
                        if c.isdigit():
                            idx = int(c) - 1
                            if 0 <= idx < len(options):
                                selected.append(options[idx]["label"])
                    answers[header] = selected
                else:
                    console.file.flush()
                    choice = builtins.input(f"选择 [1-{len(options)}] (或输入自定义): ").strip()
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            answers[header] = options[idx]["label"]
                        else:
                            answers[header] = choice
                    else:
                        answers[header] = choice or "N/A"
            except (KeyboardInterrupt, EOFError):
                answers[header] = "cancelled"

        # 格式化回答
        result_lines = ["User answers:"]
        for header, answer in answers.items():
            if isinstance(answer, list):
                result_lines.append(f"- {header}: {', '.join(answer)}")
            else:
                result_lines.append(f"- {header}: {answer}")

        return "\n".join(result_lines)
