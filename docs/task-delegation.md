# Task Delegation and Sub-Agent Routing

[English](task-delegation.md) | [简体中文](task-delegation.zh-CN.md)

This document explains the motivation, current runtime flow, known limitations, and implementation plan behind TermPilot's task delegation and sub-agent routing upgrade.

The goal of this change is to make TermPilot better at handling complex coding tasks when the selected LLM is less naturally inclined to plan, decompose work, or delegate exploration. The first implementation phase focuses on improving tool semantics, system guidance, batch delegation, task tracking, and Quiet UI rendering. It does not introduce true parallel sub-agent execution yet.

## Background

TermPilot already has the major building blocks of a terminal coding agent:

- A main agent loop that can call tools, read files, edit code, run commands, and continue reasoning from tool results.
- Persistent sessions and context compaction.
- A permission system for file and shell operations.
- A task system backed by `~/.termpilot/projects/<cwd>/tasks.json`.
- Built-in sub-agents such as `Explore`, `Plan`, `Verification`, and `general-purpose`.
- Custom agents loaded from `~/.termpilot/agents/*.md`.

The previous version directly migrated the Claude Code design and logic in this area. After connecting more LLMs, the limitation became clear: task decomposition and sub-agent spawning that work well with Claude models do not automatically work with every model. Some models still prefer to keep searching and editing in the main loop, while others can reliably use direct file/search tools but do not consistently infer when to split work, plan first, or delegate verification. This exposed a product problem rather than just an implementation gap: delegation must be easier for the model to choose, not merely available.

However, simply exposing a generic `agent` tool is not enough. Strong planning models may infer when to delegate, but models such as GLM-5.1 often prefer to keep working in the main loop with `glob`, `grep`, `read_file`, and `bash` until the task is done. That behavior is understandable: direct tools have obvious semantics, while a generic "agent" tool requires the model to first realize that delegation is beneficial.

Task decomposition and delegation should be first-class runtime capabilities, not behavior that depends entirely on the model's moment-by-moment initiative.

## Previous Flow

Before this change, the flow looked roughly like this:

1. The user sends a request.
2. The main agent receives the system prompt, user message, available tools, and session context.
3. The model decides whether to answer directly or call tools.
4. For codebase exploration, the model usually calls `glob`, `grep`, `read_file`, or `bash`.
5. If the model chooses `agent`, TermPilot starts one sub-agent with `subagent_type`, `description`, and `prompt`.
6. The sub-agent runs in an isolated context and returns a final result to the main agent.
7. The main agent summarizes or continues.

This worked technically, but it relied too heavily on model initiative. The tool existed, but the model did not always understand when it should use it.

## Problems

### 1. Delegation Was Too Abstract

The old `agent` description was framed as "launch an agent". That phrasing is accurate, but not very operational. Models had to infer:

- Whether the task is complex enough.
- Which sub-agent type should be used.
- Whether exploration should stay in the main loop or move into a sub-agent.
- Whether planning should be delegated before implementation.

For models with weaker self-evaluation and planning behavior, this often resulted in the main agent doing everything itself.

### 2. Planning Intent Was Not Strong Enough

Requests such as "plan a `/redo` command" should naturally trigger the `Plan` agent. In practice, the model sometimes treated the request as a code search task and started with `grep` or `glob`.

The issue was not that `Plan` was unavailable. The issue was that the runtime guidance did not make the routing rule forceful enough.

### 3. Multi-Direction Exploration Had No Batch Interface

When a request contained several independent directions, the main agent had two weak options:

- Run many searches itself and accumulate noisy context.
- Call `agent` multiple times manually.

There was no low-friction way to say: "delegate these three independent investigations and give me a combined summary."

### 4. Task Tracking Was Under-Specified

The existing `task_create`, `task_update`, and `task_list` tools already persisted task state, but their descriptions were generic. They did not clearly tell the model:

- Use tasks for 3+ step work.
- Use tasks for multi-file changes.
- Keep exactly one task `in_progress`.
- Mark each stage `completed` before moving on.
- Use `task_list` to regain focus in long sessions.

As a result, the task system was available but not reliably triggered.

### 5. Quiet UI Did Not Distinguish Batch Delegation

The Quiet UI could display a single agent card, but batch-style delegation needed a clearer display:

- "Running 3 delegated agents..."
- One compact line per delegated task.
- Full results available through `/details`.

Without this, batch delegation would either feel noisy or opaque.

## Design Goals

The upgrade follows several constraints:

- Improve trigger rate and delegation quality without requiring a stronger model.
- Keep the public tool name `agent` for compatibility.
- Reframe the tool semantics around `delegate_task`.
- Preserve conservative permission boundaries.
- Avoid true parallelism in phase one.
- Keep sub-agents isolated from the main context.
- Avoid recursive agent spawning.
- Make task tracking feel like a todo system.
- Keep the default UI quiet and compact.

## New Flow

After this change, the intended flow is:

1. The user sends a request.
2. Session guidance tells the model when to use `Plan`, `Explore`, `Verification`, task tools, or batch delegation.
3. If the request is complex implementation work, the model creates a task list first.
4. If the request is planning/design, the model delegates to `Plan`.
5. If the request is broad codebase understanding, architecture analysis, or design-pattern discovery, the model delegates to `Explore`.
6. If the request is review/testing/checking, the model delegates to `Verification`.
7. If there are multiple independent investigation directions, the model calls `agent` with a `tasks` array.
8. Sub-agents run serially in phase one and return structured summaries.
9. Quiet UI renders one compact delegation card.
10. The main agent uses the results to continue or produce the final answer.

## Tool Semantics

### Single Delegation

The existing single-agent path remains compatible:

```json
{
  "subagent_type": "Plan",
  "description": "Plan redo command",
  "prompt": "Design an implementation plan for adding a /redo command to TermPilot."
}
```

The model can still call `agent` exactly as before. The difference is that the tool description now frames the operation as task delegation instead of generic agent launching.

### Batch Delegation

The new `tasks` field supports up to three independent delegated tasks:

```json
{
  "tasks": [
    {
      "subagent_type": "Explore",
      "description": "Inspect commands",
      "prompt": "Find how slash commands are registered and executed."
    },
    {
      "subagent_type": "Explore",
      "description": "Inspect session rewind",
      "prompt": "Find how session rewind and parentUuid traversal work."
    },
    {
      "subagent_type": "Verification",
      "description": "Review tests",
      "prompt": "Check which tests cover command execution and session persistence."
    }
  ]
}
```

Phase-one behavior is intentionally serial:

- If `tasks` is present, top-level `subagent_type`, `description`, and `prompt` are ignored.
- Each task gets its own result entry.
- One failed task does not stop the remaining tasks.
- The tool returns a JSON payload with `delegated_tasks` and `summary`.
- More than three tasks are rejected to prevent runaway delegation.

## Built-In Agent Roles

### Explore

Use for broad, read-only codebase discovery:

- Project architecture.
- Design patterns.
- Command system structure.
- File relationships.
- Large searches where the main agent would otherwise run many `glob` and `grep` calls.

Allowed tools:

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### Plan

Use when the user asks for planning, design, implementation strategy, or "how should we build this?".

Planning intent takes priority over exploration intent. A plan may require codebase inspection, but it should still use `Plan`.

Allowed tools:

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### Verification

Use after implementation, or when the user asks to check correctness:

- Review diffs.
- Run targeted tests.
- Find regressions.
- Identify missing coverage.

Allowed tools:

- `list_dir`
- `read_file`
- `glob`
- `grep`
- `bash`

### general-purpose

Use for complex autonomous work that is not just planning, exploration, or verification.

Allowed tools:

- All normal tools except `agent`, to prevent recursive spawning.

### Custom Agents

Custom agents can be defined in:

```text
~/.termpilot/agents/*.md
```

Their frontmatter can specify names, descriptions, and allowed tools. These agents are added to the `agent` schema dynamically.

## Task Tool Changes

The task system now presents itself more like a todo list for complex coding work.

### When to Create Tasks

The model is guided to create tasks when the work involves:

- Three or more steps.
- Multi-file changes.
- Multiple user goals.
- A long-running investigation.
- Implementation that must be tested or verified.

### Task Status Discipline

The model is guided to:

- Keep only one task `in_progress` at a time.
- Mark a stage `completed` immediately after finishing it.
- Use `task_list` to recover focus during long sessions.

This makes the task system more useful for both model planning and human observability.

## System Prompt Guidance

The session guidance now includes stronger routing rules:

- Planning/design requests should use `Agent` with `subagent_type=Plan`.
- Whole-project reading, architecture analysis, or design-pattern discovery should use `Explore`.
- Correctness checks, tests, reviews, and regression searches should use `Verification`.
- Broad searches that would take more than three queries should be delegated to `Explore`.
- Multiple independent directions should use `agent.tasks`.
- Complex implementation work should create a task list first.
- `task_update` should keep exactly one active task.

This is deliberately more direct than the previous guidance. The goal is to help models that are less naturally inclined to decompose work.

## Quiet UI Changes

Single-agent calls continue to render as agent cards:

```text
Running Explore agent: Inspect command system...
```

Batch delegation renders as a compact grouped card:

```text
Running 3 delegated agents...

1. Explore - Inspect commands (completed)
2. Explore - Inspect session rewind (completed)
3. Verification - Review tests (failed)
Summary: 2/3 succeeded
```

The full output remains available through:

```text
/details <n>
```

This keeps the main terminal view quiet while preserving access to complete sub-agent results.

## Implementation Steps

### 1. Update Agent Tool Descriptions

The `agent` tool description was rewritten to emphasize:

- Delegation.
- Isolated sub-agent context.
- Final summary return behavior.
- When to use each built-in agent.
- When not to delegate.
- Custom agent support.

This change targets model behavior directly.

### 2. Add `tasks` to Agent Schema

The schema now supports:

- Existing single-task fields: `subagent_type`, `description`, `prompt`.
- New batch field: `tasks`.

Top-level required fields were removed from the JSON schema so batch calls are valid. Validation now happens inside `AgentTool.call()`.

### 3. Implement Serial Batch Execution

`AgentTool.call()` now checks for `tasks`.

If present:

1. Validate the batch size.
2. Iterate over each task.
3. Validate `prompt` and `subagent_type`.
4. Run each sub-agent with `_run_agent()`.
5. Capture success or failure per item.
6. Return a JSON summary.

This keeps runtime behavior deterministic and avoids the extra complexity of concurrent permissions, interleaved UI updates, and result ordering.

### 4. Add `list_dir` to Read-Only Agents

`Explore`, `Plan`, and `Verification` now include `list_dir` in their allowed tools.

This nudges project-understanding tasks away from raw `bash ls` or `find` output and toward structured directory summaries.

### 5. Strengthen Task Tool Descriptions

The descriptions for `task_create`, `task_update`, and `task_list` now explicitly describe:

- Todo-style use.
- Complex-work triggers.
- Single active task discipline.
- Focus recovery.

No task data model changes were required.

### 6. Extend Session Guidance

`get_session_guidance_section()` now gives the model concrete routing rules for:

- Planning.
- Exploration.
- Verification.
- Broad search delegation.
- Batch delegation.
- Task list usage.

### 7. Update Quiet UI Rendering

The UI now detects `agent` calls with a `tasks` array and renders them as `Delegation` cards. It parses the JSON result to show one compact line per delegated task.

## Testing

The implementation added targeted tests for:

- `AgentTool.input_schema` includes `tasks`.
- Single-agent calls remain compatible.
- Batch delegation runs multiple tasks.
- Unknown sub-agent types fail per item without stopping the batch.
- Batch size limits are enforced.
- Task tool descriptions contain todo-style guidance.
- Session guidance includes routing rules.
- Quiet UI summarizes batch delegation.

Targeted test command:

```bash
PYTHONPATH=src uv run pytest \
  tests/tools/test_agent_tool.py \
  tests/tools/test_task.py \
  tests/test_context.py::TestSessionGuidance \
  tests/test_ui_delegation.py \
  -q
```

Current targeted result:

```text
23 passed, 1 skipped
```

The skipped test depends on the optional `rich` UI dependency being available in the test environment.

## Current Limitations

### No True Parallelism Yet

Batch delegation is serial in phase one. This is intentional. True parallel execution would require careful handling of:

- UI event interleaving.
- Permission prompts.
- Tool result ordering.
- Shared workspace writes.
- Cancellation and timeout behavior.

### Sub-Agents Are Conservative

Read-only agents remain read-only. `general-purpose` can use broader tools, but recursive `agent` calls are blocked.

This avoids uncontrolled spawn trees and keeps the first version easier to reason about.

### Delegation Still Depends on Model Compliance

The runtime now gives clearer instructions and better schemas, but the LLM still decides whether to call tools. The change improves the probability of correct delegation; it does not force a deterministic router in front of the model.

### No Worker-Agent Write Isolation

This phase does not introduce dedicated write-capable worker agents with separate permission scopes. That should be designed separately if TermPilot later supports parallel implementation workers.

## Future Work

Possible next steps:

- Add true parallel batch execution after UI and permission boundaries are ready.
- Add a deterministic pre-router for obvious intents such as planning, broad project analysis, and verification.
- Add telemetry or eval prompts to measure delegation trigger rate across models.
- Add worker-style sub-agents with explicit write scopes.
- Add cancellation support for long-running sub-agents.
- Preserve active task state in compaction summaries.
- Add richer `/details` views for nested delegated results.

## Summary

This upgrade changes TermPilot's delegation system from "a generic agent tool that the model may or may not use" into a clearer task-distribution interface.

The core idea is simple:

- Use task tools to track multi-step work.
- Use `Plan` for implementation strategy.
- Use `Explore` for broad codebase understanding.
- Use `Verification` for correctness checks.
- Use `agent.tasks` when several independent directions can be delegated together.

This makes TermPilot less dependent on the model's spontaneous planning ability and gives weaker planning models a much clearer path toward structured execution.
