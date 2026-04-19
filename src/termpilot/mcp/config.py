"""MCP 配置读取。

对应 TS: services/mcp/config.ts (getAllMcpConfigs)
从 settings.json 和项目级 .mcp.json 读取 MCP server 配置。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypedDict

from termpilot.config import get_settings

logger = logging.getLogger(__name__)


class McpStdioConfig(TypedDict, total=False):
    type: str  # "stdio"
    command: str
    args: list[str]
    env: dict[str, str]


class McpSSEConfig(TypedDict, total=False):
    type: str  # "sse"
    url: str
    headers: dict[str, str]


# 统一配置类型
McpServerConfig = McpStdioConfig | McpSSEConfig


def _parse_single_config(name: str, raw_config: Any) -> McpServerConfig | None:
    """解析单个 MCP server 配置。"""
    if not isinstance(raw_config, dict):
        logger.warning("Invalid MCP config for '%s': expected dict", name)
        return None

    server_type = raw_config.get("type", "stdio")

    if server_type == "stdio":
        command = raw_config.get("command")
        if not command:
            logger.warning("MCP server '%s' missing 'command'", name)
            return None
        return McpStdioConfig(
            type="stdio",
            command=command,
            args=raw_config.get("args", []),
            env=raw_config.get("env"),
        )

    elif server_type == "sse":
        url = raw_config.get("url")
        if not url:
            logger.warning("MCP server '%s' missing 'url'", name)
            return None
        return McpSSEConfig(
            type="sse",
            url=url,
            headers=raw_config.get("headers"),
        )

    else:
        logger.warning("Unsupported MCP transport type '%s' for server '%s'", server_type, name)
        return None


def get_mcp_configs(cwd: str | Path | None = None) -> dict[str, McpServerConfig]:
    """从 settings.json 和 .mcp.json 读取 MCP server 配置。

    对应 TS: config.ts getAllMcpConfigs()

    读取来源（合并，.mcp.json 覆盖 settings.json 同名项）：
    1. ~/.termpilot/settings.json 的 mcpServers 字段
    2. 项目级 .mcp.json 文件
    """
    configs: dict[str, McpServerConfig] = {}

    # 来源 1: settings.json
    settings = get_settings()
    mcp_servers = settings.get("mcpServers", {})
    if isinstance(mcp_servers, dict):
        for name, raw_config in mcp_servers.items():
            parsed = _parse_single_config(name, raw_config)
            if parsed:
                configs[name] = parsed

    # 来源 2: 项目级 .mcp.json
    work_dir = Path(cwd) if cwd else Path.cwd()
    mcp_json = work_dir / ".mcp.json"
    if mcp_json.exists():
        try:
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            project_servers = data.get("mcpServers", {})
            if isinstance(project_servers, dict):
                for name, raw_config in project_servers.items():
                    parsed = _parse_single_config(name, raw_config)
                    if parsed:
                        configs[name] = parsed
                logger.debug("loaded MCP configs from .mcp.json: %s", ", ".join(project_servers.keys()))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read .mcp.json: %s", e)

    if configs:
        logger.info("Loaded %d MCP server config(s): %s", len(configs), ", ".join(configs.keys()))

    return configs
