# --- commands-mcp.py ---
import json
import os
import shlex
import subprocess
import traceback
from pathlib import Path
from typing import Dict, Any, List, Optional

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Commands-Server")

CONFIG_PATH = os.getenv("COMMANDS_CONFIG")
COMMAND_REGISTRY: Dict[str, Any] = {}

if CONFIG_PATH:
    try:
        config_file = Path(CONFIG_PATH)
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                COMMAND_REGISTRY = json.load(f)
    except Exception:
        COMMAND_REGISTRY = {}

# --- Internal Helpers ---

def _list_commands_logic() -> List[Dict[str, str]]:
    """
    Returns a list of available commands from the registry.

    Returns:
        List[Dict[str, str]]: A list of dictionaries, each containing 'name', 'template', and 'description'.
    """
    if not COMMAND_REGISTRY:
        return []
    
    return [
        {
            "name": name,
            "template": info["template"],
            "description": info["description"]
        }
        for name, info in COMMAND_REGISTRY.items()
    ]

def _execute_command_logic(command_key: str, argument: Optional[str] = None) -> Dict[str, Any]:
    """
    Handles the lookup, formatting, and execution of a command.

    Args:
        command_key (str): The key of the command.
        argument (Optional[str]): An optional argument for the command.

    Returns:
        Dict[str, Any]: A dictionary containing success status, stdout, stderr, and command used.
    """
    if command_key not in COMMAND_REGISTRY:
        available = list(COMMAND_REGISTRY.keys())
        return {
            "success": False,
            "error": "command_not_found",
            "message": f"'{command_key}' is not recognized. Available: {available}"
        }

    cmd_info = COMMAND_REGISTRY[command_key]
    template = cmd_info["template"]

    if "{arg}" in template:
        if not argument:
            return {
                "success": False,
                "error": "missing_argument",
                "message": f"Command '{command_key}' requires an argument."
            }
        safe_arg = shlex.quote(argument)
        cmd_to_run = template.format(arg=safe_arg)
    else:
        cmd_to_run = template

    try:
        result = subprocess.run(
            cmd_to_run,
            shell=True,
            capture_output=True,
            text=True,
            check=False
        )

        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": cmd_to_run
        }

    except Exception as e:
        return {
            "success": False,
            "error": "execution_failed",
            "message": str(e),
            "traceback": traceback.format_exc()
        }

# --- Public MCP Tools ---

@mcp.tool()
def list_available_commands() -> str:
    """
    Returns a list of all allowed console commands and their descriptions.

    Returns:
        str: A JSON-formatted string containing the list of commands or an error message.

    Usage Notes:
        Use this tool to discover what commands can be run via 'run_predefined_command'.
    """
    try:
        commands = _list_commands_logic()
        return json.dumps({"commands": commands}, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "listing_failed",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)

@mcp.tool()
def run_predefined_command(command_name: str, argument: Optional[str] = None) -> str:
    """
    Executes a specific console command from the allowed registry.
    
    Args:
        command_name (str): The key of the command (from list_available_commands).
        argument (str, optional): An optional string argument required by some commands.

    Returns:
        str: A JSON-formatted string containing the execution results or an error message.
    
    Usage Notes:
        Use 'list_available_commands' before calling this tool to ensure the command exists.
    """
    try:
        result = _execute_command_logic(command_name, argument)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "command_execution_wrapper_failed",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)

if __name__ == "__main__":
    mcp.run()
