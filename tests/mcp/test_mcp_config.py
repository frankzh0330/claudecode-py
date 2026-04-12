"""MCP config 测试。"""

import json

import pytest

from cc_python.mcp.config import get_mcp_configs


class TestGetMcpConfigs:
    def test_empty(self, tmp_settings, tmp_path):
        tmp_settings({})
        assert get_mcp_configs(cwd=tmp_path) == {}

    def test_stdio(self, tmp_settings, tmp_path):
        tmp_settings({"mcpServers": {
            "my-server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        }})
        configs = get_mcp_configs(cwd=tmp_path)
        assert "my-server" in configs
        assert configs["my-server"]["type"] == "stdio"
        assert configs["my-server"]["command"] == "npx"

    def test_sse(self, tmp_settings, tmp_path):
        tmp_settings({"mcpServers": {
            "sse-server": {
                "type": "sse",
                "url": "http://localhost:3000/sse",
            }
        }})
        configs = get_mcp_configs(cwd=tmp_path)
        assert "sse-server" in configs
        assert configs["sse-server"]["type"] == "sse"

    def test_defaults_to_stdio(self, tmp_settings, tmp_path):
        tmp_settings({"mcpServers": {
            "no-type": {"command": "echo"},
        }})
        configs = get_mcp_configs(cwd=tmp_path)
        assert configs["no-type"]["type"] == "stdio"
