"""MCP client 测试。"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_python.mcp.client import MCPClient
from cc_python.mcp.transport import BaseTransport


class TestMCPClient:
    def test_init(self):
        transport = MagicMock(spec=BaseTransport)
        client = MCPClient(name="test-server", transport=transport)
        assert client.name == "test-server"
        assert client.is_connected is False

    def test_default_state(self):
        transport = MagicMock(spec=BaseTransport)
        client = MCPClient(name="test", transport=transport)
        assert client.tools == []
        assert client.resources == []
        assert client.server_info == {}
        assert client.instructions == ""
