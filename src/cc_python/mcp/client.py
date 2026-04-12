"""MCP 客户端。

对应 TS: services/mcp/client.ts
使用 JSON-RPC 2.0 通过 transport 与 MCP server 通信。

核心能力：
- connect(): 初始化连接（initialize + initialized 握手）
- list_tools(): 发现 server 暴露的工具
- call_tool(): 调用工具
- list_resources() / read_resource(): 资源管理
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from cc_python.mcp.transport import BaseTransport

logger = logging.getLogger(__name__)

# MCP 协议版本
_MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 超时（秒）
_DEFAULT_TIMEOUT = 30.0


class MCPClient:
    """MCP 客户端。

    对应 TS: services/mcp/client.ts 中的连接逻辑。
    通过 transport 发送 JSON-RPC 请求，解析响应。
    """

    def __init__(self, name: str, transport: BaseTransport) -> None:
        self._name = name
        self._transport = transport
        self._is_connected = False
        self._server_info: dict[str, Any] = {}
        self._instructions: str = ""
        self._tools: list[dict[str, Any]] = []
        self._resources: list[dict[str, Any]] = []
        self._pending_requests: dict[str, asyncio.Future] = {}  # noqa: F821
        self._request_counter = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def server_info(self) -> dict[str, Any]:
        return self._server_info

    @property
    def instructions(self) -> str:
        return self._instructions

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    @property
    def resources(self) -> list[dict[str, Any]]:
        return self._resources

    def _next_id(self) -> str:
        """生成请求 ID。"""
        self._request_counter += 1
        return str(self._request_counter)

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应。"""
        import asyncio

        request_id = self._next_id()
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            message["params"] = params

        logger.debug("MCP → %s.%s (id=%s)", self._name, method, request_id)
        await self._transport.send(message)

        # 等待响应（循环读取直到拿到匹配的 id）
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"MCP request '{method}' timed out after {timeout}s")

            response = await self._transport.receive()

            # 检查是否是通知（无 id）
            if "id" not in response:
                continue

            if response["id"] == request_id:
                logger.debug("MCP ← %s.%s response (id=%s)", self._name, method, request_id)
                if "error" in response:
                    error = response["error"]
                    raise RuntimeError(
                        f"MCP error [{error.get('code', -1)}]: {error.get('message', 'Unknown error')}"
                    )
                return response.get("result")

            # 不匹配的响应，跳过（可能是并发的通知）
            logger.debug("Skipping unmatched response id=%s", response.get("id"))

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（无 id，不期望响应）。"""
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            message["params"] = params
        await self._transport.send(message)

    async def connect(self) -> None:
        """建立连接。

        对应 TS: services/mcp/client.ts connectToMcpServer()
        流程：
        1. 启动 transport
        2. 发送 initialize 请求
        3. 发送 initialized 通知
        4. 发现工具和资源
        """
        await self._transport.start()

        # 1. initialize 握手
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "cc_python",
                    "version": "0.1.0",
                },
            },
        )

        self._server_info = result.get("serverInfo", {})
        self._instructions = result.get("instructions", "")
        logger.info(
            "MCP server '%s' initialized: %s v%s",
            self._name,
            self._server_info.get("name", "unknown"),
            self._server_info.get("version", "?"),
        )

        # 2. initialized 通知
        await self._send_notification("notifications/initialized")

        self._is_connected = True

        # 3. 发现工具和资源
        await self._discover_tools()
        await self._discover_resources()

    async def _discover_tools(self) -> None:
        """发现 server 暴露的工具列表。"""
        try:
            result = await self._send_request("tools/list", {})
            self._tools = result.get("tools", [])
            logger.debug(
                "MCP server '%s' has %d tools: %s",
                self._name,
                len(self._tools),
                ", ".join(t["name"] for t in self._tools),
            )
        except Exception as e:
            logger.warning("Failed to discover tools from '%s': %s", self._name, e)
            self._tools = []

    async def _discover_resources(self) -> None:
        """发现 server 暴露的资源列表。"""
        try:
            result = await self._send_request("resources/list", {})
            self._resources = result.get("resources", [])
            logger.debug(
                "MCP server '%s' has %d resources",
                self._name,
                len(self._resources),
            )
        except Exception as e:
            logger.debug("No resources from '%s': %s", self._name, e)
            self._resources = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用 MCP 工具。

        对应 TS: services/mcp/client.ts callTool()
        返回工具结果的文本表示。
        """
        logger.debug("MCP call_tool: %s.%s(%s)", self._name, name, list(arguments.keys()))
        if not self._is_connected:
            return f"Error: MCP server '{self._name}' is not connected"

        try:
            result = await self._send_request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=60.0,  # 工具调用给更长超时
            )
            return self._format_tool_result(result)
        except Exception as e:
            return f"Error calling MCP tool '{name}': {e}"

    def _format_tool_result(self, result: dict[str, Any]) -> str:
        """格式化工具结果为文本。"""
        content = result.get("content", [])
        if isinstance(content, str):
            return content

        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type", "text")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "image":
                    parts.append(f"[Image: {block.get('mimeType', 'unknown')}]")
                elif block_type == "resource":
                    resource = block.get("resource", {})
                    parts.append(f"[Resource: {resource.get('uri', 'unknown')}]")
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))

        return "\n".join(parts) if parts else str(result)

    async def read_resource(self, uri: str) -> str:
        """读取 MCP 资源。

        对应 TS: ReadMcpResourceTool
        """
        if not self._is_connected:
            return f"Error: MCP server '{self._name}' is not connected"

        try:
            result = await self._send_request(
                "resources/read",
                {"uri": uri},
            )
            contents = result.get("contents", [])
            parts = []
            for item in contents:
                if isinstance(item, dict):
                    text = item.get("text", "")
                    if text:
                        parts.append(text)
                    elif "blob" in item:
                        parts.append(f"[Binary resource: {item.get('mimeType', 'unknown')}]")
            return "\n".join(parts) if parts else str(result)
        except Exception as e:
            return f"Error reading resource '{uri}': {e}"

    async def close(self) -> None:
        """关闭连接。"""
        self._is_connected = False
        await self._transport.close()
        self._tools.clear()
        self._resources.clear()
