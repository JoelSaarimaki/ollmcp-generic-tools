# --- commands-mcp.py ---
import json
import os
import shlex
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    ALLOWED_DIR,
    COMMANDS_CONFIG,
    MAX_OUTPUT_CHARS
)

# --- Constants & Config ---
mcp = MCPServer("Commands-Server")

ARG_PLACEHOLDER = "{arg}"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_STREAM_CHARS = int(MAX_OUTPUT_CHARS * 0.45)  # Per stream (stdout and stderr), leaving room for JSON escaping
OUTPUT_HEAD_RATIO = 0.25  # Share of truncated output kept from the start, the rest is kept from the end
IS_WINDOWS = os.name == "nt"
# Characters cmd.exe interprets even when Windows batch files (.bat/.cmd) are run without a shell
BATCH_UNSAFE_CHARS = set('&|<>^%!"()')

# --- Internal Helpers ---

def _load_registry() -> dict[str, dict[str, Any]]:
    """
    Loads the command registry named by commands_config, or returns an empty registry if it is not set.
    Raises an error if the file is missing, is not valid JSON or has an incomplete command, so that
    a broken registry does not silently look like a registry without commands.
    """
    if not COMMANDS_CONFIG:
        return {}
    if not COMMANDS_CONFIG.is_file():
        raise FileNotFoundError(f"commands_config: command registry not found: {COMMANDS_CONFIG}")
    with open(COMMANDS_CONFIG, "r", encoding="utf-8") as f:
        registry = json.load(f)
    if not isinstance(registry, dict):
        raise ValueError(f"{COMMANDS_CONFIG}: the command registry must be a JSON object of commands.")
    for name, info in registry.items():
        if not isinstance(info, dict) or not isinstance(info.get("template"), str) or not isinstance(info.get("description"), str):
            raise ValueError(f"{COMMANDS_CONFIG}: command '{name}' must have a 'template' and a 'description' string.")
        timeout = info.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError(f"{COMMANDS_CONFIG}: the 'timeout' of command '{name}' must be a positive number of seconds.")
    return registry

COMMAND_REGISTRY = _load_registry()

def _split_template(template: str) -> list[str]:
    """
    Splits a command template into the program and its arguments, keeping quoted parts together.
    Backslashes are kept as-is on Windows so that Windows paths work in templates.
    """
    if not IS_WINDOWS:
        return shlex.split(template)
    tokens = shlex.split(template, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in tokens]

def _format_command(args: list[str]) -> str:
    """
    Returns the command as a single string for display.
    """
    return subprocess.list2cmdline(args) if IS_WINDOWS else shlex.join(args)

def _validate_argument(argument: str, tokens: list[str], executable: str) -> str | None:
    """
    Returns the reason why the argument is unsafe to insert into the command, or None if it is safe.
    """
    if any(c in argument for c in "\0\r\n"):
        return "The argument must not contain line breaks or null characters."
    if ARG_PLACEHOLDER in tokens and argument.startswith("-"):
        return "The argument must not start with '-', as it could add unintended options to the command."
    if IS_WINDOWS and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        unsafe = sorted(BATCH_UNSAFE_CHARS.intersection(argument))
        if unsafe:
            return f"The argument must not contain the characters {' '.join(unsafe)}, as this command runs as a Windows batch file."
    return None

def _truncate_output(text: str) -> str:
    """
    Shortens output longer than MAX_STREAM_CHARS, keeping its start and its end.
    """
    if len(text) <= MAX_STREAM_CHARS:
        return text
    head_chars = int(MAX_STREAM_CHARS * OUTPUT_HEAD_RATIO)
    tail_chars = MAX_STREAM_CHARS - head_chars
    omitted = len(text) - MAX_STREAM_CHARS
    return f"{text[:head_chars]}\n... [{omitted} characters cut from the middle] ...\n{text[-tail_chars:]}"

def _limit_note(*outputs: str) -> str | None:
    """
    Returns the 'Output limited' instruction if any of the outputs is too long to show completely.
    """
    if all(len(output) <= MAX_STREAM_CHARS for output in outputs):
        return None
    return ("Output limited: the output is too long to show completely, so characters are cut from the middle."
            " The start and the end are shown, as errors and summaries are usually at the end."
            " To see more, run a narrower command, e.g. tests for a single file instead of all tests.")

def _decode_output(output: str | bytes | None) -> str:
    """
    Returns captured process output as text.
    """
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output

def _list_commands_logic() -> list[dict[str, Any]]:
    """
    Returns the name, template, description, argument requirement and timeout of each command in the registry.
    """
    if not COMMAND_REGISTRY:
        return []

    return [
        {
            "name": name,
            "template": info["template"],
            "description": info["description"],
            "requires_argument": ARG_PLACEHOLDER in info["template"],
            "timeout_seconds": info.get("timeout", DEFAULT_TIMEOUT_SECONDS)
        }
        for name, info in COMMAND_REGISTRY.items()
    ]

def _execute_command_logic(command_key: str, argument: str | None = None) -> dict[str, Any]:
    """
    Looks up, formats and executes a command from the registry without a shell, in ALLOWED_DIR.
    The argument is inserted as a single command-line argument, so it cannot add further commands.
    Returns the success status (exit code 0), exit code, stdout, stderr and the command used.
    """
    if command_key not in COMMAND_REGISTRY:
        available = ", ".join(COMMAND_REGISTRY) or "(none)"
        return {
            "success": False,
            "error": "command_not_found",
            "message": f"'{command_key}' is not a registered command. Available commands: {available}. Use list_available_commands to see what each one does."
        }

    cmd_info = COMMAND_REGISTRY[command_key]
    template = cmd_info["template"]
    timeout = cmd_info.get("timeout", DEFAULT_TIMEOUT_SECONDS)

    tokens = _split_template(template)
    if not tokens:
        return {"success": False, "error": "invalid_template", "message": f"Command '{command_key}' has an empty template."}

    executable = shutil.which(tokens[0])
    if not executable:
        return {"success": False, "error": "program_not_found", "message": f"Program '{tokens[0]}' was not found on PATH. Tell the user that it is not installed or not on PATH."}

    if ARG_PLACEHOLDER in template:
        if not argument:
            return {
                "success": False,
                "error": "missing_argument",
                "message": f"Command '{command_key}' requires an argument. {cmd_info['description']}"
            }
        reason = _validate_argument(argument, tokens, executable)
        if reason:
            return {"success": False, "error": "invalid_argument", "message": reason}
        tokens = [t.replace(ARG_PLACEHOLDER, argument) for t in tokens]

    args = [executable] + tokens[1:]
    command = _format_command(tokens)

    try:
        result = subprocess.run(
            args,
            cwd=ALLOWED_DIR,  # Commands such as tests and linters work on the project, like the other servers
            # Python programs (e.g. pytest, poetry) write their output as UTF-8, as it is read, instead of the Windows code page
            env={**os.environ, "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", "utf-8")},
            capture_output=True,
            stdin=subprocess.DEVNULL,  # Interactive prompts get end-of-input instead of hanging
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False
        )

        return {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "output_limited": _limit_note(result.stdout, result.stderr),
            "stdout": _truncate_output(result.stdout),
            "stderr": _truncate_output(result.stderr),
            "command": command
        }

    except subprocess.TimeoutExpired as e:
        return {
            "success": False,
            "error": "timeout",
            "message": f"Command did not finish within {timeout} seconds and was stopped.",
            "output_limited": _limit_note(_decode_output(e.stdout), _decode_output(e.stderr)),
            "stdout": _truncate_output(_decode_output(e.stdout)),
            "stderr": _truncate_output(_decode_output(e.stderr)),
            "command": command
        }
    except Exception as e:
        return {
            "success": False,
            "error": "execution_error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }

# --- Public MCP Tools ---

@mcp.tool()
def list_available_commands() -> str:
    """
    Returns a list of all allowed console commands and their descriptions.
    Use this tool to discover what commands can be run with 'run_predefined_command'.

    Returns:
        str: A JSON-formatted string containing the list of commands or an error message.
    """
    try:
        commands = _list_commands_logic()
        return json.dumps({"success": True, "commands": commands}, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "list_error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2, ensure_ascii=False)

@mcp.tool()
def run_predefined_command(command_name: str, argument: str | None = None) -> str:
    """
    Executes a specific console command from the allowed registry, in the project directory.
    Use 'list_available_commands' first to see which commands exist.
    The command succeeds only if it exits with code 0. Long output is cut from the middle:
    then output_limited is set and tells how to see more.

    Args:
        command_name (str): The name of the command (from list_available_commands).
        argument (str, optional): An optional string argument required by some commands.
            It is passed to the command as a single argument and must not start with '-'.

    Returns:
        str: A JSON-formatted string containing the success status, exit code, stdout and stderr,
            or an error message (e.g., timeout or invalid_argument).
    """
    try:
        result = _execute_command_logic(command_name, argument)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "command_error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run()
