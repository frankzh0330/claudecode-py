# MCP、Skills 与 Commands

[English](mcp.md) | [简体中文](mcp.zh-CN.md)

本文档概述 `termpilot` 当前的 MCP、skills 和 slash commands 集成方式。

## MCP 概览

MCP（Model Context Protocol）让 `termpilot` 可以连接外部工具服务器，并将它们暴露出的工具和资源动态提供给模型使用。

## MCP 模块

```text
mcp/__init__.py   → MCPManager
mcp/client.py     → JSON-RPC 客户端
mcp/transport.py  → stdio / SSE 传输
mcp/config.py     → 从 settings 读取 mcpServers

tools/mcp_tool.py            → 将 MCP 工具包装成模型可调用工具
tools/list_mcp_resources.py  → 列出资源
tools/read_mcp_resource.py   → 读取资源
```

## 支持的传输层

- stdio：本地子进程 MCP server
- SSE：远程流式 MCP server

## 启动流程

session 启动时：

1. `MCPManager` 读取已配置的 servers。
2. 为每个 server 创建 transport 和 `MCPClient`。
3. 客户端与 server 完成初始化握手。
4. 缓存工具和资源元数据。
5. 如果 server 提供 instructions，可注入 system prompt。

## MCP 工具暴露方式

已连接的 MCP 工具会通过 `tools/mcp_tool.py` 转换成模型可见工具。

这些工具使用 `mcp__...` 命名约定，以便和内置工具区分开。

## 资源支持

资源能力分成两部分：

- 列出可用资源
- 读取指定资源内容

## Skills

Skills 是轻量级可复用 prompt 模板，来自带 frontmatter 的 Markdown 文件。

当前来源：

- `~/.termpilot/skills/*.md`
- `<cwd>/.termpilot/skills/*.md`

同名情况下，项目级 skill 会覆盖用户全局 skill。

## Slash Commands

内置 slash commands 实现在 `commands.py`。

当前内置集合包括：

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

如果某个 slash command 不是内置命令，则系统会在存在同名且允许用户调用的 skill 时回退到该 skill。
