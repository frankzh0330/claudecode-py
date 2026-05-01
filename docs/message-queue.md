# Message Queue and Interactive Drain Loop

[English](message-queue.md) | [简体中文](message-queue.zh-CN.md)

This document describes TermPilot's interactive message queue, drain loop, interrupt behavior, and terminal prompt handling.

## Relevant Modules

```text
cli.py                 → REPL input collection, drain loop, slash commands, UI coordination
queue.py               → prioritized command queue and background-agent tracking
api.py                 → tool loop, permission updates, OpenAI-compatible usage extraction
ui.py                  → quiet status lines and compact tool cards
tools/agent.py         → sub-agent progress events and background notifications
tools/task.py          → task persistence and interrupted-task cleanup
tools/ask_user.py      → stable numeric user questions
prompt_utils.py        → shared prompt-toolkit/questionary cancellation helpers
routing.py             → lightweight delegation routing reminders
```

## Why This Exists

An interactive coding agent has two things happening at once:

- the user can keep typing while a model/tool turn is still running
- the current turn may be reading files, asking permission, spawning sub-agents, or waiting for clarification

If slash commands mutate `messages` immediately, commands such as `/clear`, `/compact`, or `/rewind` can race with an in-flight model turn. That can leave stale assistant/tool results in the session, clear context too early, or replay old task work after the user interrupted it.

TermPilot now treats input collection and command execution as separate responsibilities:

- input collection only enqueues commands
- the drain loop executes commands serially at safe points

## Queue Model

The main queue stores `QueuedCommand` objects with:

- `mode`: `prompt`, `slash_command`, `task_notification`, or `system`
- `priority`: `NOW`, `NEXT`, or `LATER`
- `origin`: `user`, `agent`, `system`, or `task-watcher`
- `agent_id`: empty for the main loop, non-empty for scoped agent work

The main REPL only drains commands whose `agent_id` targets the main loop. This prevents future sub-agent queues from stealing main-thread notifications or each other's messages.

The queue also supports:

- filtered dequeue without removing unrelated commands
- stable `peek()` without changing FIFO order
- `discard()` for removing stale queued work after `/clear` or interrupt
- background-agent registration and cancellation

## Drain Loop Flow

In interactive mode:

1. `_input_collector()` waits for terminal input.
2. It parses slash commands but does not execute them directly.
3. It enqueues either a `prompt` or `slash_command`.
4. `_drain_loop()` dequeues one main-thread command at a time.
5. It runs `_handle_prompt()` or `_handle_slash_command()`.
6. Only after the active command finishes does the loop consider task-watcher follow-up work.

This makes state mutation serial:

- `messages`
- session storage
- permission context
- task watcher state
- `/clear`, `/compact`, and `/rewind`

## Slash Command Safety

Slash commands are queued instead of executing immediately.

State-changing commands currently include:

- `/clear`
- `/compact`
- `/rewind`

If one of these commands is typed while a model turn is active, TermPilot marks it as queued during an active turn. If the assistant's final text appears to be waiting for user confirmation, the command is deferred until the next safe point.

Example:

```text
> 修改 hello.py
assistant: 文件当前内容是 ... 你想改成什么？
> /clear
> 算了不改了
assistant: 好的，保持原样。还有其他需要吗？
> 没了
assistant: 好的，有事随时找我。
/clear runs here
```

The current "waiting for user" check is intentionally lightweight. It looks for confirmation/choice-style wording such as questions, "confirm", "choose", "which", "确认", "选择", or "是否". It is not a full semantic task-completion detector.

## Interrupt Behavior

Pressing `ESC` during an active turn now invalidates the current turn generation and performs cleanup:

- cancels the active processing task
- clears the visible status line
- discards queued `agent` and `task-watcher` notifications
- cancels registered background agent tasks
- removes pending or in-progress task-list entries
- ignores late UI events from the cancelled turn

This prevents an interrupted delegation from resurfacing later as stale "Running delegated agents..." output or task-watcher follow-up work.

## Interactive Prompts

TermPilot avoids nested prompt-toolkit menus in the main REPL for high-frequency interactions.

The following prompts use stable numeric input:

- permission confirmation
- `ask_user_question`
- `/rewind`

This avoids terminal CPR warnings and raw escape sequences in terminals that do not support cursor-position requests reliably.

For the remaining questionary-based configuration prompts, `prompt_utils.ask_with_esc()` provides shared ESC cancellation and safer prompt-toolkit shutdown handling.

## Prompt Rendering

The interactive prompt uses `prompt_toolkit.patch_stdout()` so Rich output and prompt-toolkit input can coexist. This allows the prompt to redraw after model output, tool cards, and status lines.

TermPilot no longer prints a per-turn `console.rule()` separator. The UI stays closer to a quiet terminal assistant:

- compact status lines such as `: Coalescing...`
- compact tool cards
- no forced green divider after every turn
- prompt mode displayed in the prompt prefix, for example `plan >`

## Token Usage

For OpenAI-compatible streaming APIs, TermPilot requests:

```python
stream_options={"include_usage": True}
```

If the provider does not support this option, TermPilot falls back to the plain streaming request.

Token display is based on recorded token usage, not on known pricing. This matters for providers without built-in pricing tables: cost may be `$0.0000`, but token counts should still be shown when available.

## Current Limitations

- Waiting-for-user detection is heuristic and text based.
- `/clear` deferral does not prove that a user task is semantically complete; it only waits until the assistant no longer appears to be asking for a decision.
- True full-screen terminal UI is not implemented; the current approach keeps Rich and prompt-toolkit lightweight.
- Background sub-agent cancellation depends on registered tasks. Synchronous provider calls may still take a moment to stop at the SDK/network layer.

## Testing Focus

The current test coverage focuses on:

- slash command queue safety
- deferred `/clear`
- stable numeric permission and rewind choices
- `ask_user_question` numeric input
- queue filtering, discard, and FIFO behavior
- OpenAI-compatible streaming usage extraction
- interrupted task cleanup
