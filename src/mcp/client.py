"""MCP Client Manager — connects to configured MCP servers and loads their tools."""

import json
import logging
import os
from typing import List

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages connections to one or more MCP servers defined in MCP_SERVERS_CONFIG."""

    def __init__(self) -> None:
        self._client: MultiServerMCPClient | None = None
        self._tools: list = []

    async def connect_all(self) -> list:
        """Parse MCP_SERVERS_CONFIG, connect to all servers, and return tool list."""
        config_raw = os.environ.get("MCP_SERVERS_CONFIG", "")
        if not config_raw:
            logger.warning("MCP_SERVERS_CONFIG not set — no MCP servers will be loaded.")
            return []

        try:
            config = json.loads(config_raw)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse MCP_SERVERS_CONFIG JSON: %s", exc)
            return []

        servers = config.get("servers", [])
        if not servers:
            logger.warning("MCP_SERVERS_CONFIG contains no 'servers' entries.")
            return []

        # Build the dict expected by MultiServerMCPClient
        server_map: dict = {}
        for srv in servers:
            srv_id = srv.get("id") or srv.get("name")
            if not srv_id:
                logger.warning("MCP server entry missing 'id': %s", srv)
                continue
            transport = srv.get("transport", "stdio")
            if transport == "stdio":
                entry: dict = {
                    "command": srv["command"],
                    "args": srv.get("args", []),
                    "transport": "stdio",
                }
                if srv.get("env"):
                    entry["env"] = srv["env"]
            elif transport in ("sse", "streamable_http"):
                entry = {
                    "url": srv["url"],
                    "transport": transport,
                }
                if srv.get("headers"):
                    entry["headers"] = srv["headers"]
            else:
                logger.warning("Unknown MCP transport '%s' for server '%s'", transport, srv_id)
                continue
            server_map[srv_id] = entry

        if not server_map:
            return []

        try:
            self._client = MultiServerMCPClient(server_map)
            all_tools = await self._client.get_tools()
        except Exception as exc:
            logger.error("Failed to connect to MCP servers: %s", exc)
            return []

        self._tools = all_tools

        # Log per-server tool counts
        for srv_id in server_map:
            srv_tools = [t for t in all_tools if getattr(t, "server_name", None) == srv_id]
            count = len(srv_tools)
            names = [t.name for t in srv_tools]
            logger.info(
                "Loaded %d tool(s) from MCP server '%s': %s",
                count, srv_id, names,
            )

        return self._tools

    async def cleanup(self) -> None:
        pass
