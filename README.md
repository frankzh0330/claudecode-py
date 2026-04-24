# TermPilot

[English](README.md) | [简体中文](README.zh-CN.md)

## Overview

`TermPilot` is a terminal-native AI coding assistant implemented in Python. It can read and edit files, execute commands, search code, call external tools, and manage long-running coding sessions from the command line.

It is already usable for day-to-day coding tasks and continues to evolve toward a more complete, production-ready terminal agent experience.

## Highlights

- Multi-provider API support: Anthropic, OpenAI, OpenAI-compatible endpoints, and compatible providers such as Zhipu GLM
- Streaming responses with Markdown-friendly terminal rendering
- Quiet terminal UI with staged status updates, compact tool cards, and on-demand detail expansion
- Tool-use loop: the model can call tools repeatedly until the task is complete
- Concurrent tool execution for safe tools, serialized execution for unsafe tools
- Permission system with five modes, persistent rules, path validation, and dangerous-command detection
- Hook system for shell-command hooks around prompts and tool calls
- Automatic `TERMPILOT.md` loading for project-level persistent instructions
- Context compaction for long conversations
- Session persistence with resumable JSONL history, session rewind, and crash recovery
- Automatic retry with exponential backoff for API rate limits and transient failures
- MCP integration for dynamic tools and resources
- Skills and slash commands
- Five built-in sub-agent types: Explore, Plan, Verification, general-purpose, and user-defined custom agents (loaded from `~/.termpilot/agents/*.md`)
- Plan Mode: press Shift+Tab to cycle between Default, Accept Edits, and Plan modes; models in Plan mode are read-only and present plans for user approval
- Persistent memory, undo snapshots, token/cost tracking, large tool-result storage, and attachments

## Available Tools

| Tool | Name | Description | Concurrency-safe | Needs confirmation |
|------|------|-------------|------------------|--------------------|
| Directory summary | `list_dir` | Summarize a directory layout without dumping a full `ls`/`find` listing | ✅ | ❌ |
| Read file | `read_file` | Read file contents with line numbers, `offset`, and `limit` | ✅ | ❌ |
| Write file | `write_file` | Create or overwrite files and auto-create parent directories | ❌ | ✅ |
| Edit file | `edit_file` | Exact string replacement with optional `replace_all` | ❌ | ✅ |
| Run command | `bash` | Execute shell commands with timeout support | ❌ | ✅ |
| File search | `glob` | Search files using glob patterns | ✅ | ❌ |
| Content search | `grep` | Search file contents with regular expressions | ✅ | ❌ |
| Sub-agent | `agent` | Launch a recursive sub-agent: Explore, Plan, Verification, general-purpose, or custom | ✅ | ❌ |
| Ask user | `ask_user_question` | Ask the user a focused follow-up question | ✅ | ❌ |
| Tasks | `task_create`, `task_update`, `task_list`, `task_get` | Create and manage task items for the current session | ✅ | ❌ |
| Plan mode | `enter_plan_mode`, `exit_plan_mode` | Switch into or out of planning mode | ✅ | ❌ |
| Notebook edit | `notebook_edit` | Edit Jupyter notebook cells | ❌ | ✅ |
| Web search | `web_search` | Search the web with optional domain filters | ✅ | ❌ |
| Web fetch | `web_fetch` | Fetch a URL with SSRF protection and convert HTML to Markdown | ✅ | ❌ |
| MCP tools | `mcp__*__*` | Dynamic tools exposed by MCP servers | ✅ | ❌ |
| MCP resources | `list_mcp_resources`, `read_mcp_resource` | List and read MCP resources | ✅ | ❌ |
| Skill tool | `skill` | Invoke reusable skill prompts | ✅ | ❌ |

## Quick Start

### Requirements

- Python 3.10+
- `pip`

### Install

```bash
pip install termpilot
```

To upgrade later:

```bash
pip install -U termpilot
```

### Configure

On first launch, TermPilot will guide you through an interactive setup:

```bash
termpilot
```

You'll see a provider selector:

```
? Select your LLM provider:
  > OpenAI
    Anthropic (Claude)
    Zhipu GLM
    DeepSeek
    Qwen / DashScope
    Moonshot / Kimi
    SiliconFlow
    OpenRouter
    Groq
    Together
    Fireworks
    Ollama (local)
    vLLM (local)
    OpenAI-compatible (custom)
```

Select your provider, enter your API key, and you're ready to go.

To reconfigure later:

```bash
termpilot model
```

Or manually edit `~/.termpilot/settings.json`:

```json
{
  "provider": "zhipu",
  "env": {
    "ZHIPU_API_KEY": "your-api-key",
    "ZHIPU_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
    "ZHIPU_MODEL": "glm-5.1"
  }
}
```

All providers use the OpenAI-compatible API format. Supported providers: OpenAI, Anthropic, Zhipu GLM, DeepSeek, Qwen/DashScope, Moonshot/Kimi, SiliconFlow, OpenRouter, Groq, Together, Fireworks, Ollama, vLLM, and any custom OpenAI-compatible endpoint.

Environment variables override `settings.json`.

### Run

```bash
termpilot
termpilot -p "Read main.py"
termpilot -m gpt-4o
termpilot --resume
termpilot -s <session-id>
```

### Common Slash Commands

| Command | Description |
|---------|-------------|
| `/details last`, `/details <n>` | Show the full output for the most recent or a specific tool result |
| `/help` | Show available commands |
| `/compact` | Trigger manual context compaction |
| `/clear` | Clear conversation history |
| `/config` | Show effective configuration |
| `/model` | Switch the active model for the current provider |
| `/skills` | List available skills |
| `/mcp` | Show MCP server status |
| `/undo` | Restore the previous file snapshot |
| `/rewind` | Rewind conversation to a previous turn and continue from there |
| `/commit` | Draft a commit flow with AI-generated commit message guidance |
| `/init` | Generate a project instruction seed for the current project |
| `/exit`, `/quit` | Exit the program |

## Sub-Agents

The `agent` tool launches sub-agents that run in an isolated context with their own system prompt and tool set. Sub-agents can recursively call tools until the task is complete, and their results are returned to the main agent.

| Type | Description |
|------|-------------|
| `Explore` | Fast read-only agent for codebase exploration, file discovery, and code searches |
| `Plan` | Architect agent for designing implementation strategies and exploring trade-offs |
| `Verification` | Read-only agent for checking diffs, running tests, and identifying regressions |
| `general-purpose` | General agent for complex multi-step tasks with full tool access |
| Custom | User-defined agents loaded from `~/.termpilot/agents/*.md` with frontmatter metadata |

### Custom Agents

Create a Markdown file in `~/.termpilot/agents/` with YAML frontmatter:

```markdown
---
name: code-reviewer
description: Reviews code for quality, security, and best practices
tools: read_file, glob, grep
---

You are a code review specialist. Analyze the code and report findings.
```

## Plan Mode

Press **Shift+Tab** to cycle between permission modes:

| Mode | Behavior |
|------|----------|
| Default | Normal operation with permission prompts |
| Accept Edits | Auto-approve file edits within the working directory |
| Plan | Read-only mode — only exploration and planning tools are allowed |

In Plan mode, the model explores the codebase and designs an implementation plan, then calls `exit_plan_mode` to present the plan for your approval via an interactive dialog. After approval, the mode restores to the previous setting.

A bottom toolbar shows the current mode (yellow for Plan, green for Accept Edits, gray for Default).

## Project Layout

```text
src/termpilot/
├── cli.py            # CLI entrypoint, quiet UI, permission menus, slash commands
├── api.py            # Tool loop, streaming, UI events, hooks, orchestration
├── context.py        # System prompt builder
├── config.py         # Settings and environment resolution
├── hooks.py          # Hook system
├── permissions.py    # Permission engine
├── messages.py       # Message normalization and helpers
├── session.py        # Session persistence
├── compact.py        # Context compaction
├── token_tracker.py  # Token counting and cost tracking
├── skills.py         # Skill loading and registry
├── commands.py       # Slash commands
├── termpilotmd.py    # TERMPILOT.md loading
├── mcp/              # MCP client, transport, and config
└── tools/            # Core tools, web tools, advanced tools, MCP adapters
```

## Architecture Summary

Main runtime flow:

1. `cli.py` collects user input and renders streamed output.
2. `context.py` builds the system prompt from environment, config, memory, and project instructions.
3. `api.py` calls the model, gathers `tool_use` blocks, and orchestrates tool execution.
4. `hooks.py` runs pre/post hooks and prompt/session hooks.
5. `permissions.py` decides whether each tool call is allowed, denied, or requires confirmation.
6. Tool results are returned to the model until it stops requesting tools.

For a deeper module breakdown, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Docs Map

- [ARCHITECTURE.md](ARCHITECTURE.md): layering, data flow, and module ownership
- [docs/golden-rules.md](docs/golden-rules.md): mechanical coding rules
- [docs/conventions.md](docs/conventions.md): naming and organization conventions
- [docs/hooks.md](docs/hooks.md): hook design and behavior
- [docs/termpilotmd.md](docs/termpilotmd.md): project instruction loading
- [docs/compact.md](docs/compact.md): compaction strategy
- [docs/mcp.md](docs/mcp.md): MCP, skills, and commands
- [docs/system_prompt_sections.md](docs/system_prompt_sections.md): system prompt sections

## Status

| Phase | Scope | Status |
|------|-------|--------|
| 1 | Tool framework and core tools | ✅ |
| 2 | System prompt sections | ✅ |
| 3 | Permission system | ✅ |
| 4 | Hooks system | ✅ |
| 5 | `TERMPILOT.md` loading | ✅ |
| 6 | Context compaction | ✅ |
| 7 | Messages and attachments | ✅ |
| 8 | Advanced tools: agent, task, ask-user, plan | ✅ |
| 9 | MCP, skills, and slash commands | ✅ |
| 10 | Remaining TypeScript alignment work | 🚧 In progress |

## Reference Implementation

The project is still being refined with reference to an upstream TypeScript implementation, but `TermPilot` keeps its own product identity, packaging, and documentation.

## UI Notes

- The default CLI experience is intentionally quiet: long raw tool output is collapsed into compact cards instead of being printed in full.
- When the model is still gathering context, the CLI shows short-lived staged statuses such as `Coalescing…`, project inspection, and summarization.
- Permission prompts use a keyboard-friendly menu (`↑` / `↓` + Enter) instead of numeric input.

## Development

If you want to work on TermPilot locally:

```bash
git clone https://github.com/frankzh0330/termpilot.git
cd termpilot

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
termpilot
```

For local quality checks:

```bash
python3 scripts/check.py
```

## License

MIT
