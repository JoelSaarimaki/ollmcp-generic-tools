# --- test_servers_start.py ---
"""
Starts the server as a real stdio MCP server, as ollmcp does, and checks the tools it offers.
"""
import asyncio
import importlib
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conftest import PACKAGE, REPO_DIR

# Listed here separately from TOOLSETS in common.py, so that a changed tool name is noticed
EXPECTED_TOOLS = {
    "filesystem": ["write_file", "edit_file", "create_file", "read_file_with_metadata", "read_image",
                   "list_directory", "move_file", "delete_file", "restore_file", "get_tools_config"],
    "outline": ["get_outline"],
    "search": ["search_text_in_files", "search_files_by_pattern", "search_relevant_files"],
    "git": ["get_git_diff", "get_git_history", "get_git_status"],
    "commands": ["list_available_commands", "run_predefined_command", "suggest_command"],
    "context": ["list_context_files", "read_context_file", "write_context_file", "remove_context_file"],
    "web": ["web_search", "web_fetch"],
}
ALL_TOOLS = [tool for tools in EXPECTED_TOOLS.values() for tool in tools]
# The package is found in the repository, also if it is not installed
PACKAGE_ENV = {"PYTHONPATH": str(REPO_DIR)}

def list_tool_definitions(command: str, args: list[str], env: dict[str, str], cwd) -> list:
    async def run():
        params = StdioServerParameters(command=command, args=args, env={**os.environ, **env}, cwd=str(cwd))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools
    return asyncio.run(run())

def list_tools(config, cwd) -> list[str]:
    env = {**PACKAGE_ENV, "MCP_TOOLS_CONFIG": str(config)}
    return [tool.name for tool in list_tool_definitions(sys.executable, ["-m", PACKAGE], env, cwd)]

def test_server_starts_over_stdio_with_every_tool(project):
    assert list_tools(project.configure(), project.root) == ALL_TOOLS

def test_disabled_toolsets_and_tools_are_left_out(project):
    config = project.configure(disabled_toolsets=["web", "git"], disabled_tools=["write_file", "get_outline"])
    expected = [tool for name, tools in EXPECTED_TOOLS.items() if name not in ("web", "git", "outline")
                for tool in tools if tool != "write_file"]
    assert list_tools(config, project.root) == expected

def test_every_toolset_defines_the_tools_listed_in_common(project):
    project.configure()
    common = project.common()
    assert common.TOOLSETS == EXPECTED_TOOLS
    for name, tools in EXPECTED_TOOLS.items():
        toolset = importlib.import_module(f"{PACKAGE}.{name}")
        assert [tool.name for tool in toolset.mcp._tool_manager.list_tools()] == tools

def test_repository_configuration_starts_every_tool():
    """The repository's own .mcp.json and tools config must stay valid."""
    servers = json.loads((REPO_DIR / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert list(servers) == ["dev"]
    cfg = servers["dev"]
    assert cfg["command"] == "python" and cfg["args"] == ["-m", PACKAGE]
    assert list(cfg.get("env", {})) == ["MCP_TOOLS_CONFIG"], "The server must only get MCP_TOOLS_CONFIG"
    tools = list_tool_definitions(sys.executable, cfg["args"], cfg["env"], REPO_DIR)
    assert [tool.name for tool in tools] == ALL_TOOLS

# Tool definitions are sent to the model with every request, so they must stay small
MAX_TOOL_DEFINITION_CHARS = 11500

def test_tool_definitions_stay_small(project):
    tools = list_tool_definitions(sys.executable, ["-m", PACKAGE], {**PACKAGE_ENV, "MCP_TOOLS_CONFIG": str(project.configure())}, project.root)
    schemas = [json.dumps(tool.input_schema) for tool in tools]
    assert not any('"title"' in schema or '"anyOf"' in schema for schema in schemas)
    total = sum(len(tool.description or "") for tool in tools) + sum(len(schema) for schema in schemas)
    assert total <= MAX_TOOL_DEFINITION_CHARS, f"The tool definitions take {total} characters"

def test_null_arguments_use_the_defaults(project):
    """Small models often send null for optional parameters they do not use."""
    project.write("a.txt", "one\ntwo\n")
    config = project.configure()
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", PACKAGE],
                                       env={**os.environ, **PACKAGE_ENV, "MCP_TOOLS_CONFIG": str(config)}, cwd=str(project.root))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("read_file_with_metadata", {"path": "a.txt", "section": None, "start_line": None, "line_numbers": None})
    result = asyncio.run(run())
    assert not result.is_error and "Lines: 1-2 of 2" in result.content[0].text
