"""Prompt-toolkit/questionary helpers."""

from __future__ import annotations

from typing import Any


def ask_with_esc(question) -> Any:
    """Ask a questionary prompt with ESC cancellation and safe shutdown."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    _patch_safe_application_exit(question.application)

    bindings = KeyBindings()

    @bindings.add(Keys.Escape)
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    kb = question.application.key_bindings
    if hasattr(kb, "add"):
        kb.add(Keys.Escape)(_cancel)
    elif hasattr(kb, "registries"):
        kb.registries.append(bindings)
    return question.ask()


def _patch_safe_application_exit(application: Any) -> None:
    """Make repeated prompt exits harmless.

    prompt_toolkit can raise "Application is not running" when Ctrl+C/Ctrl+D/ESC
    races with another prompt being suspended. For user-facing menus, repeated
    exit attempts should be treated as already-cancelled rather than fatal.
    """
    if getattr(application, "_termpilot_safe_exit_patched", False):
        return

    original_exit = application.exit

    def safe_exit(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_exit(*args, **kwargs)
        except Exception as exc:
            if "Application is not running" in str(exc):
                return None
            raise

    application.exit = safe_exit
    application._termpilot_safe_exit_patched = True
