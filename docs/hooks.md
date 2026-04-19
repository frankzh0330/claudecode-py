# Hooks

[English](hooks.md) | [简体中文](hooks.zh-CN.md)

This document describes the hook system implemented in `hooks.py`.

## Overview

Hooks are user-configured shell commands that run around key lifecycle events. They allow policy checks, logging, prompt augmentation, and tool-call interception without changing the main codebase.

## Relevant Modules

```text
hooks.py    → hook config loading, matching, execution, parsing
api.py      → PreToolUse / PostToolUse dispatch
cli.py      → SessionStart / UserPromptSubmit / Stop dispatch
```

## Supported Events

Current hook events:

- `PreToolUse`
- `PostToolUse`
- `UserPromptSubmit`
- `Stop`
- `SessionStart`

## Config Shape

Hooks are loaded from `~/.termpilot/settings.json`.

Conceptually:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "/path/to/hook.sh", "timeout": 5}
        ]
      }
    ]
  }
}
```

## Matching Model

Each event contains a list of matchers.

- `matcher` may target a tool family or a specific tool name
- missing matcher or `*` behaves like a wildcard
- matching only matters for tool-related events; prompt/session events apply directly

## Input to Hook Commands

Hook commands receive JSON on stdin with event-specific fields such as:

- session id
- current working directory
- event name
- tool name / tool input for tool events
- prompt text for prompt submission events

## Hook Results

Each executed hook returns a `HookResult` containing:

- process exit code
- stdout
- stderr
- parsed decision such as `allow` / `deny`
- optional updated tool input

## Blocking Behavior

- exit code `2` is treated as a blocking result
- parsed `deny` decisions can also block tool execution
- non-blocking failures are logged and surfaced as warnings rather than fatal errors

## Typical Lifecycle

```text
PreToolUse hook
  → permission check
  → tool execution
  → PostToolUse hook
```

Other lifecycle events:

- `SessionStart`: right after a session is initialized
- `UserPromptSubmit`: before a prompt is sent to the model
- `Stop`: after the model finishes a response
