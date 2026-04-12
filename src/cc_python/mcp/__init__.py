"""MCP（Model Context Protocol）集成。

对应 TS: services/mcp/（~15 文件，~5000 行）
Python 简化版保留核心：stdio/sse 传输 + 客户端连接 + 工具发现 + 工具调用。

架构：
  mcp/
  ├── __init__.py      ← MCPManager（连接管理 + 工具收集）
  ├── transport.py     ← 传输层（StdioTransport + SSETransport）
  ├── client.py        ← MCPClient（JSON-RPC 通信 + 工具调用）
  └── config.py        ← 配置读取（settings.json mcpServers）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cc_python.mcp.client import MCPClient
from cc_python.mcp.config import McpServerConfig, get_mcp_configs
from cc_python.mcp.transport import SSETransport, StdioTransport

logger = logging.getLogger(__name__)


class MCPManager:
    """MCP 连接管理器。

    对应 TS: services/mcp/MCPConnectionManager.tsx
    负责读取配置、创建客户端、连接、工具发现、生命周期管理。
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def discover_and_connect(self) -> None:
        """读取配置并为每个 MCP server 建立连接。

        流程：
        1. 从 settings.json 读取 mcpServers 配置
        2. 为每个 server 创建对应 transport + client
        3. 连接并发现工具
        """
        configs = get_mcp_configs()
        if not configs:
            logger.debug("No MCP servers configured")
            return

        logger.debug("connecting to %d MCP servers: %s", len(configs), ", ".join(configs.keys()))
        connect_tasks = []
        for name, config in configs.items():
            client = self._create_client(name, config)
            self._clients[name] = client
            connect_tasks.append(self._safe_connect(name, client))

        await asyncio.gather(*connect_tasks)
        self._connected = True

    async def _safe_connect(self, name: str, client: MCPClient) -> None:
        """安全连接，失败不阻塞其他 server。"""
        try:
            await client.connect()
            logger.info("MCP server '%s' connected", name)
        except Exception as e:
            logger.warning("MCP server '%s' failed to connect: %s", name, e)

    def _create_client(self, name: str, config: McpServerConfig) -> MCPClient:
        """根据配置类型创建对应 transport + client。"""
        if config["type"] == "stdio":
            transport = StdioTransport(
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env"),
            )
        elif config["type"] == "sse":
            transport = SSETransport(
                url=config["url"],
                headers=config.get("headers"),
            )
        else:
            raise ValueError(f"Unsupported MCP transport type: {config['type']}")

        return MCPClient(name=name, transport=transport)

    def get_tools(self) -> list[dict[str, Any]]:
        """收集所有已连接 server 的工具列表。

        返回格式：[{"name": "mcp__server__tool", "description": "...", "inputSchema": {...}}]
        """
        tools = []
        for name, client in self._clients.items():
            if client.is_connected:
                for tool in client.tools:
                    tools.append({
                        "server_name": name,
                        "tool_name": tool["name"],
                        "full_name": f"mcp__{name}__{tool['name']}",
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("inputSchema", {}),
                    })
        return tools

    def get_resources(self) -> list[dict[str, Any]]:
        """收集所有已连接 server 的资源列表。"""
        resources = []
        for name, client in self._clients.items():
            if client.is_connected:
                for res in client.resources:
                    resources.append({
                        "server_name": name,
                        **res,
                    })
        return resources

    def get_instructions(self) -> str:
        """收集所有已连接 server 的 instructions，用于 System Prompt 注入。"""
        parts = []
        for name, client in self._clients.items():
            if client.is_connected and client.instructions:
                parts.append(f"## MCP Server: {name}\n\n{client.instructions}")
        return "\n\n".join(parts)

    def find_client_for_tool(self, tool_full_name: str) -> MCPClient | None:
        """根据 mcp__server__tool 名称找到对应 client。"""
        if not tool_full_name.startswith("mcp__"):
            return None
        parts = tool_full_name.split("__", 2)
        if len(parts) < 3:
            return None
        server_name = parts[1]
        return self._clients.get(server_name)

    def find_original_tool_name(self, tool_full_name: str) -> str | None:
        """从 mcp__server__tool 提取原始工具名。"""
        parts = tool_full_name.split("__", 2)
        if len(parts) < 3:
            return None
        return parts[2]

    async def call_tool(self, tool_full_name: str, arguments: dict) -> str:
        """调用 MCP 工具。"""
        client = self.find_client_for_tool(tool_full_name)
        if not client:
            return f"Error: MCP server not found for tool '{tool_full_name}'"

        original_name = self.find_original_tool_name(tool_full_name)
        if not original_name:
            return f"Error: Invalid MCP tool name '{tool_full_name}'"

        try:
            return await client.call_tool(original_name, arguments)
        except Exception as e:
            return f"MCP tool error: {e}"

    async def read_resource(self, server_name: str, uri: str) -> str:
        """读取 MCP 资源。"""
        client = self._clients.get(server_name)
        if not client:
            return f"Error: MCP server '{server_name}' not found"
        if not client.is_connected:
            return f"Error: MCP server '{server_name}' not connected"

        try:
            return await client.read_resource(uri)
        except Exception as e:
            return f"MCP resource error: {e}"

    async def shutdown(self) -> None:
        """关闭所有 MCP 连接。"""
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.debug("MCP server '%s' closed", name)
            except Exception as e:
                logger.warning("Error closing MCP server '%s': %s", name, e)
        self._clients.clear()
        self._connected = False
