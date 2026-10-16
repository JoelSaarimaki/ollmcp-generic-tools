# --- __main__.py ---
"""
The entry point:
- 'python -m ollmcp_tools' starts one MCP server over stdio with the tools of the toolsets enabled
  in the tools config, except the disabled tools.
- 'python -m ollmcp_tools init' sets up the tools for the project in the current folder (see init.py).

Each toolset is a module that defines its tools on its own MCPServer object. Only the enabled toolsets
are imported, so a disabled toolset's settings (e.g. the command registry) are not checked either.
"""
import importlib
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

SERVER_NAME = "ollmcp-tools"
USAGE = "Usage: python -m ollmcp_tools [init]\n  (no command)  Start the MCP server, as ollmcp does.\n  init          Set up the tools for the project in the current folder."

def create_server() -> MCPServer:
    """
    Imports the enabled toolsets and returns a server with their tools, except the disabled ones.
    """
    # Imported here, as loading common.py loads the tools config, which init does not need
    from .common import DISABLED_TOOLS, ENABLED_TOOLSETS, prepare_tools

    tools = []
    for name in ENABLED_TOOLSETS:
        toolset = importlib.import_module(f"{__package__}.{name}")
        tools += [tool for tool in toolset.mcp._tool_manager.list_tools() if tool.name not in DISABLED_TOOLS]
    server = MCPServer(SERVER_NAME, tools=tools)
    prepare_tools(server)
    return server

def main(args: list[str]) -> int:
    """
    Runs the command given on the command line and returns the exit code.
    """
    if not args:
        create_server().run()
        return 0
    if args == ["init"]:
        from .init import run
        print(run(Path.cwd()))
        return 0
    print(USAGE, file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
