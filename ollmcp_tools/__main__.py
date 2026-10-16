# --- __main__.py ---
"""
The entry point: 'python -m ollmcp_tools' starts one MCP server over stdio with the tools of the
toolsets enabled in the tools config, except the disabled tools.

Each toolset is a module that defines its tools on its own MCPServer object. Only the enabled toolsets
are imported, so a disabled toolset's settings (e.g. the command registry) are not checked either.
"""
import importlib

from mcp.server.mcpserver import MCPServer

from .common import DISABLED_TOOLS, ENABLED_TOOLSETS, prepare_tools

SERVER_NAME = "ollmcp-tools"

def create_server() -> MCPServer:
    """
    Imports the enabled toolsets and returns a server with their tools, except the disabled ones.
    """
    tools = []
    for name in ENABLED_TOOLSETS:
        toolset = importlib.import_module(f"{__package__}.{name}")
        tools += [tool for tool in toolset.mcp._tool_manager.list_tools() if tool.name not in DISABLED_TOOLS]
    server = MCPServer(SERVER_NAME, tools=tools)
    prepare_tools(server)
    return server

def main() -> None:
    create_server().run()

if __name__ == "__main__":
    main()
