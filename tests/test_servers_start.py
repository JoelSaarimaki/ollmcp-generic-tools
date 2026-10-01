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
                   "list_directory", "move_file", "delete_file", "get_config"],
    "map": ["get_outline"],
    "search": ["search_text_in_files", "search_files_by_pattern", "search_relevant_files"],
    "git": ["get_diff", "get_file_history", "get_git_status"],
    "commands": ["list_available_commands", "run_predefined_command"],
    "context": ["list_context_files", "read_context_file", "write_context_file", "remove_context_file"],
    "web": ["web_search", "web_fetch"],
}

def list_tool_definitions(command: str, args: list[str], env: dict[str, str], cwd) -> list:
    async def run():
        params = StdioServerParameters(command=command, args=args, env={**os.environ, **env}, cwd=str(cwd))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools
    return asyncio.run(run())

def list_tools(command: str, args: list[str], env: dict[str, str], cwd) -> list[str]:
    return [tool.name for tool in list_tool_definitions(command, args, env, cwd)]

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

# Tool definitions are sent to the model with every request, so they must stay small
MAX_TOOL_DEFINITION_CHARS = 10800

def test_tool_definitions_stay_small():
    servers = json.loads((REPO_DIR / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    tools = [tool for cfg in servers.values()
             for tool in list_tool_definitions(sys.executable, cfg["args"], cfg["env"], REPO_DIR)]
    schemas = [json.dumps(tool.input_schema) for tool in tools]
    assert not any('"title"' in schema or '"anyOf"' in schema for schema in schemas)
    total = sum(len(tool.description or "") for tool in tools) + sum(len(schema) for schema in schemas)
    assert total <= MAX_TOOL_DEFINITION_CHARS, f"The tool definitions take {total} characters"

def test_null_arguments_use_the_defaults(project):
    """Small models often send null for optional parameters they do not use."""
    project.write("a.txt", "one\ntwo\n")
    config = project.configure()
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(SERVERS_DIR / SERVER_FILES["filesystem"])],
                                       env={**os.environ, "MCP_TOOLS_CONFIG": str(config)}, cwd=str(project.root))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("read_file_with_metadata", {"path": "a.txt", "section": None, "start_line": None, "line_numbers": None})
    result = asyncio.run(run())
    assert not result.is_error and "Lines: 1-2 of 2" in result.content[0].text
