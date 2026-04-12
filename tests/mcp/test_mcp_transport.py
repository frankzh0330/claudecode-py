"""MCP transport 测试。"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from cc_python.mcp.transport import StdioTransport, SSETransport


class TestStdioTransport:
    def test_init(self):
        t = StdioTransport(command="echo", args=["hello"])
        assert t._command == "echo"
        assert t._args == ["hello"]

    @pytest.mark.asyncio
    async def test_close_without_start(self):
        t = StdioTransport(command="echo")
        await t.close()  # 应该不报错


class TestSSETransport:
    def test_init(self):
        t = SSETransport(url="http://localhost:3000/sse")
        assert t._url == "http://localhost:3000/sse"

    @pytest.mark.asyncio
    async def test_close_without_start(self):
        t = SSETransport(url="http://localhost:3000/sse")
        await t.close()  # 应该不报错
