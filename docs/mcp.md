# MCP, Skills, and Commands

[English](mcp.md) | [简体中文](mcp.zh-CN.md)

This document summarizes the current MCP, skills, and slash-command integration in `termpilot`.

## MCP Overview

MCP (Model Context Protocol) allows `termpilot` to connect to external tool servers and expose their tools/resources to the model dynamically.

## MCP Modules

```text
mcp/__init__.py   → MCPManager
mcp/client.py     → JSON-RPC client
mcp/transport.py  → stdio and SSE transports
mcp/config.py     → reads mcpServers from settings

tools/mcp_tool.py            → wraps MCP tools as model-callable tools
tools/list_mcp_resources.py  → lists resources
tools/read_mcp_resource.py   → reads resources
```

## Supported Transports

- stdio: local subprocess-backed MCP servers
- SSE: remote streaming MCP servers

## Startup Flow

At session startup:

1. `MCPManager` reads configured servers.
2. A transport and `MCPClient` are created per server.
3. The client initializes with the server.
4. Tool and resource metadata are cached.
5. Connected server instructions can be surfaced into the system prompt.

## MCP Tool Exposure

Connected MCP tools are converted into model-visible tools via `tools/mcp_tool.py`.

These tools are named with an `mcp__...` convention so they remain distinguishable from built-in tools.

## Resources

Resource support is split into:

- listing available resources
- reading the content of a specific resource

## Skills

Skills are lightweight reusable prompt templates loaded from markdown files with frontmatter.

Current sources:

- `~/.termpilot/skills/*.md`
- `<cwd>/.termpilot/skills/*.md`

Project-local skills override user-global skills of the same name.

## Slash Commands

Builtin slash commands are implemented in `commands.py`.

Current builtin set includes:

- `/help`
- `/compact`
- `/clear`
- `/config`
- `/details`
- `/skills`
- `/mcp`
- `/undo`
- `/commit`
- `/init`
- `/exit` / `/quit`

If a slash command is not builtin, the system falls back to a user-invocable skill with the same name when available.
