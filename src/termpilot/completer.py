"""Slash command auto-completer."""

from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion


class SlashCompleter(Completer):
    """Completer that suggests slash commands when input starts with /."""

    def __init__(self) -> None:
        self._commands: dict[str, str] = {}

    def refresh(self) -> None:
        from termpilot.commands import get_all_commands
        from termpilot.skills import get_all_skills

        self._commands.clear()
        for cmd in get_all_commands():
            if not cmd.is_hidden:
                self._commands[f"/{cmd.name}"] = cmd.description
        for skill in get_all_skills():
            if skill.user_invocable:
                self._commands[f"/{skill.name}"] = skill.description

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for name, desc in self._commands.items():
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display_meta=desc,
                )
