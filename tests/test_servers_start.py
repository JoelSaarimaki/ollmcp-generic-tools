# --- test_servers_start.py ---
"""
Starts the servers as real stdio MCP servers, as ollmcp does, and checks the tools they offer.
"""
import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import REPO_DIR, SERVER_FILES, SERVERS_DIR

EXPECTED_TOOLS = {
    "filesystem": ["write_file", "edit_file", "create_file", "read_file_with_metadata", "read_image",
                   "list_directory", "create_directory", "move_file", "delete_file", "get_config"],
    "map": ["generate_codebase_map", "generate_file_map"],
    "search": ["search_text_in_files", "search_files_by_pattern"],
    "git": ["get_file_diff", "get_all_changes_diff", "get_file_history", "get_git_status"],
    "commands": ["list_available_commands", "run_predefined_command"],
    "context": ["list_context_files", "read_context_file", "write_context_file", "append_to_context_file",
                "read_all_context_files", "remove_context_file"],
    "web": ["web_search", "web_fetch"],
}

def list_tools(command: str, args: list[str], env: dict[str, str], cwd) -> list[str]:
    async def run():
        params = StdioServerParameters(command=command, args=args, env={**os.environ, **env}, cwd=str(cwd))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [tool.name for tool in (await session.list_tools()).tools]
    return asyncio.run(run())

@pytest.mark.parametrize("server", SERVER_FILES)
def test_server_starts_over_stdio(project, server):
    config = project.configure()
    tools = list_tools(sys.executable, [str(SERVERS_DIR / SERVER_FILES[server])], {"MCP_TOOLS_CONFIG": str(config)}, project.root)
    assert tools == EXPECTED_TOOLS[server]

def test_repository_configuration_starts_every_server():
    """The repository's own .mcp.json and tools config must stay valid."""
    servers = json.loads((REPO_DIR / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    started = {}
    for name, cfg in servers.items():
        assert list(cfg.get("env", {})) == ["MCP_TOOLS_CONFIG"], f"{name} must only get MCP_TOOLS_CONFIG"
        command = sys.executable if cfg["command"] == "python" else cfg["command"]
        started[name] = list_tools(command, cfg["args"], cfg["env"], REPO_DIR)
    assert sum(len(tools) for tools in started.values()) == sum(len(tools) for tools in EXPECTED_TOOLS.values())
