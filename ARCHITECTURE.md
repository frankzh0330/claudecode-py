# Architecture Overview

[English](ARCHITECTURE.md) | [简体中文](ARCHITECTURE.zh-CN.md)

This document summarizes the current architecture of `termpilot`, the responsibility of each major module, and the intended dependency direction between layers.

## Layered View

```text
┌──────────────────────────────────────────────────────┐
│                    CLI Layer                         │
│                    cli.py                            │
│  REPL / one-shot mode · quiet rendering              │
│  permission menu · tool cards · slash command        │
│  dispatch · session startup                          │
├──────────────────────────────────────────────────────┤
│                    API Layer                         │
│                    api.py                            │
│  model client creation · streaming · tool loop       │
│  hook dispatch · permission gating · orchestration   │
├──────────────────────────────────────────────────────┤
│                 Service Layer                        │
│  permissions.py · hooks.py · compact.py · undo.py   │
│  session.py · tool_result_storage.py                 │
├──────────────────────────────────────────────────────┤
│                 Context Layer                        │
│  context.py · config.py · messages.py                │
│  attachments.py · termpilotmd.py · skills.py        │
├──────────────────────────────────────────────────────┤
│                  Tool Layer                          │
│  core tools · advanced tools · web tools             │
│  MCP adapters · skill tool                           │
└──────────────────────────────────────────────────────┘
```

## Dependency Direction

Core rule: dependencies flow downward.

```text
cli.py
  └─ api.py
      ├─ permissions.py / hooks.py / compact.py / undo.py
      ├─ context.py / config.py / messages.py / session.py
      ├─ attachments.py / tool_result_storage.py / termpilotmd.py / skills.py
      └─ tools/*.py / mcp/*
```

Guidelines:

- `cli.py` owns UI and user interaction, not tool policy.
- `api.py` owns the model/tool execution loop.
- Service modules should stay reusable and not depend on the CLI layer.
- Tools should focus on execution, not permission policy.

## Module Responsibilities

### `cli.py`

- Parses CLI arguments with `click`
- Runs REPL and one-shot execution
- Initializes sessions, undo state, MCP, and skills
- Dispatches `SessionStart`, `UserPromptSubmit`, and `Stop` hooks
- Renders markdown, staged statuses, and compact tool cards with `rich`
- Stores recent tool results for `/details`
- Uses keyboard-friendly permission menus
- Handles slash commands from `commands.py`

### `api.py`

- Creates Anthropic/OpenAI-compatible clients
- Streams text and tool-use events
- Emits structured UI events for status, permission, and tool lifecycle changes
- Executes tool calls with safe/unsafe concurrency partitioning
- Runs `PreToolUse` and `PostToolUse` hooks
- Applies permission checks before tool execution
- Stores or truncates large tool results before re-injecting them
- Triggers auto compaction when context grows too large

### `permissions.py`

- Defines five permission modes: `DEFAULT`, `ACCEPT_EDITS`, `BYPASS`, `DONT_ASK`, `PLAN`
- Evaluates allow/deny/ask rules from settings
- Validates sensitive file paths
- Classifies dangerous bash commands
- Produces `PermissionResult` objects consumed by `api.py` and `cli.py`

### `hooks.py`

- Loads hook configuration from `~/.termpilot/settings.json`
- Defines hook events and matcher structures
- Executes shell-command hooks asynchronously
- Parses hook stdout JSON for allow/deny/input-update behavior

### `compact.py`

- Estimates token usage with a local heuristic
- Performs count-based and time-based micro-compaction
- Falls back to model-generated full compaction when needed

### `session.py`

- Persists transcript entries as JSONL under `~/.termpilot/projects/...`
- Restores session history by replaying the parent UUID chain
- Stores metadata such as generated conversation titles

### `context.py`

- Builds the full system prompt
- Injects static prompt sections and dynamic sections
- Loads memory guidance and project instructions
- Includes MCP instructions when connected servers provide them

### `messages.py`, `attachments.py`, `tool_result_storage.py`, `token_tracker.py`

- `messages.py`: message construction and normalization helpers
- `attachments.py`: local attachment expansion for prompts
- `tool_result_storage.py`: persistence/truncation of oversized tool outputs
- `token_tracker.py`: exact token counting from API usage and per-model cost tracking

### `skills.py`, `commands.py`, `termpilotmd.py`

- `skills.py`: loads bundled and disk-based skills
- `commands.py`: builtin slash commands plus skill fallback
- `termpilotmd.py`: discovers layered `TERMPILOT.md` / rules files for prompt injection

### `mcp/*` and `tools/*`

- `mcp/*`: transport, client, config, and connection management
- `tools/*`: concrete tools exposed to the model

Current tool families:

- Directory summary tool: `list_dir`
- Core file/shell/search tools
- Advanced workflow tools: ask-user, agent, task, plan, notebook
- Web tools: `web_fetch`, `web_search`
- MCP dynamic tools and resource readers
- Skill expansion tool

## Runtime Flow

```text
User input
  │
  ▼
cli.py
  ├─ process attachments / slash commands
  ├─ dispatch UserPromptSubmit hook
  └─ call api.query_with_tools()
        │
        ├─ stream model output
        ├─ collect tool_use blocks
        ├─ run PreToolUse hooks
        ├─ check permissions
        ├─ execute tools
        ├─ run PostToolUse hooks
        ├─ store/truncate tool results if needed
        └─ call model again until no tool_use remains
  │
  ▼
cli.py renders final response
  │
  ▼
Stop hook
```

## Configuration Flow

```text
~/.termpilot/settings.json
  ├─ config.py           → model / API key / base URL / env
  ├─ permissions.py      → permission rules and mode
  ├─ hooks.py            → hook matchers
  └─ mcp/config.py       → MCP server definitions
```
