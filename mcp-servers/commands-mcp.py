# --- commands-mcp.py ---
import json
import os
import subprocess
import traceback
import shlex
from pathlib import Path
from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Commands-Server")

CONFIG_PATH = os.getenv("COMMANDS_CONFIG")
COMMAND_REGISTRY = {}

if CONFIG_PATH:
    try:
        config_file = Path(CONFIG_PATH)
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                COMMAND_REGISTRY = json.load(f)
    except Exception:
        COMMAND_REGISTRY = {}

# --- Internal Helpers ---
def _list_commands_logic() -> str:
    """Formats the registry into a list of available commands and descriptions."""
    if not COMMAND_REGISTRY:
        return "No commands are currently configured."
    
    lines = ["Available predefined commands:"]
    lines.append("-" * 30 + "\n")
    for name, info in COMMAND_REGISTRY.items():
        lines.append(f"COMMAND NAME: {name}")
        lines.append(f"TEMPLATE: {info['template']}")
        lines.append(f"DESCRIPTION: {info['description']}")
        lines.append("")
    lines.append("-" * 30)
    return "\n".join(lines)

def _execute_command_logic(command_key: str, argument: str = None) -> str:
    """Handles the lookup, formatting, and execution of a command."""
    if command_key not in COMMAND_REGISTRY:
        available = list(COMMAND_REGISTRY.keys())
        return f"Error: '{command_key}' is not recognized. Available: {available}"

    cmd_info = COMMAND_REGISTRY[command_key]
    template = cmd_info["template"]

    if "{arg}" in template:
        if not argument:
            return f"Error: Command '{command_key}' requires an argument."
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

        output = []
        if result.stdout:
            output.append(f"--- STDOUT ---\n{result.stdout}")
        if result.stderr:
            output.append(f"--- STDERR ---\n{result.stderr}")
        
        if not output:
            return f"Command executed successfully (no output returned).\nCommand: {cmd_to_run}"
            
        return "\n".join(output)

    except Exception as e:
        return f"Failed to execute '{cmd_to_run}':\n{e}\n{traceback.format_exc()}"

# --- Public MCP Tools ---
@mcp.tool()
def list_available_commands() -> str:
    """
    Returns a list of all allowed console commands and their descriptions.
    Use this tool to see what console commands are available to be used.

    Returns:
        str: A formatted list of available commands or an error message.
    """
    try:
        return _list_commands_logic()
    except Exception as e:
        return f"Error listing commands:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def run_predefined_command(command_name: str, argument: str = None) -> str:
    """
    Executes a specific console command from the allowed registry.
    Use this to run a console command.
    Use list_available_commands before using this tool to see what console commands are available.
    
    Args:
        command_name (str): The key of the command (from list_available_commands).
        argument (str, optional): An optional string argument required by some commands.

    Returns:
        str: The command output, success message, or an error message.
    """
    try:
        return _execute_command_logic(command_name, argument)
    except Exception as e:
        return f"Error in command execution wrapper:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
