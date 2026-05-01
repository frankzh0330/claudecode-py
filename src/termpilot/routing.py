"""Intent routing and delegation planning.

This module keeps lightweight orchestration policy out of the CLI. The first
version only returns soft system reminders; later versions can execute plans
directly in the runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


RoutingKind = Literal["none", "delegate_batch"]

_FILE_REF_RE = re.compile(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|md|toml|json|ya?ml)")


@dataclass(frozen=True)
class DelegatedTaskPlan:
    """A proposed delegated subtask."""

    subagent_type: str
    description: str
    prompt: str
    target: str = ""


@dataclass(frozen=True)
class RoutingPlan:
    """A runtime routing suggestion for a user prompt."""

    kind: RoutingKind
    reason: str = ""
    tasks: list[DelegatedTaskPlan] = field(default_factory=list)

    @property
    def should_delegate(self) -> bool:
        return self.kind == "delegate_batch" and bool(self.tasks)

    def to_system_reminder(self) -> str | None:
        """Render this plan as a soft internal reminder for the model."""
        if not self.should_delegate:
            return None

        targets = ", ".join(task.target or task.description for task in self.tasks)
        task_lines = "\n".join(
            f"- {task.subagent_type}: {task.description} — {task.prompt}"
            for task in self.tasks
        )
        return (
            "<system-reminder>"
            "Runtime routing detected multiple independent inspection targets. "
            "Use the Agent tool with the tasks array before direct read_file calls. "
            f"Create one delegated task for each target: {targets}.\n"
            f"Suggested delegation plan:\n{task_lines}\n"
            f"Reason: {self.reason}\n"
            "The expected UI should show a Delegation tool card. Only skip delegation "
            "if there is a concrete reason these targets are not independent."
            "</system-reminder>"
        )


def build_routing_plan(user_input: str) -> RoutingPlan:
    """Build a lightweight routing plan for a user prompt."""
    return _plan_multi_file_inspection(user_input) or RoutingPlan(kind="none")


def build_routing_reminder(user_input: str) -> str | None:
    """Return a soft system reminder when the runtime has a routing preference."""
    return build_routing_plan(user_input).to_system_reminder()


def _plan_multi_file_inspection(user_input: str) -> RoutingPlan | None:
    file_refs = _unique_preserve_order(_FILE_REF_RE.findall(user_input))
    if len(file_refs) < 2:
        return None

    if not _has_file_list_shape(user_input, file_refs):
        return None
    if not _has_meaningful_non_file_intent(user_input):
        return None

    tasks = [
        DelegatedTaskPlan(
            subagent_type="Explore",
            description=f"Inspect {file_ref}",
            prompt=(
                f"Inspect `{file_ref}` and summarize its main responsibilities, "
                "important functions/classes, and how it fits into TermPilot."
            ),
            target=file_ref,
        )
        for file_ref in file_refs[:3]
    ]
    return RoutingPlan(
        kind="delegate_batch",
        reason="The prompt asks to inspect multiple files/modules separately.",
        tasks=tasks,
    )


def _unique_preserve_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _has_file_list_shape(user_input: str, file_refs: list[str]) -> bool:
    """Detect whether file refs appear as a list, without relying on a model."""
    if len(file_refs) >= 3:
        return True
    return bool(re.search(r"[,;，；、]|\band\b|\bor\b", user_input, re.IGNORECASE))


def _has_meaningful_non_file_intent(user_input: str) -> bool:
    """Ensure the prompt contains intent beyond just a list of filenames."""
    text = _FILE_REF_RE.sub(" ", user_input)
    text = re.sub(r"[,;，；、/|&()\[\]{}:：`'\"<>_-]", " ", text)
    text = re.sub(r"\b(?:and|or|the|a|an)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", "", text)
    return len(text) >= 2
