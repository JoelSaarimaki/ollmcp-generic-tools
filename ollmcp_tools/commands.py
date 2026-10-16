# --- commands.py ---
import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import traceback
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from .common import (
    ALLOWED_DIR,
    COMMAND_SUGGESTIONS,
    REGISTRY_PATH,
    MAX_OUTPUT_CHARS
)

# --- Constants & Config ---
mcp = MCPServer("commands")  # Holds the tools of this toolset; __main__.py serves the enabled toolsets in one server

ARG_PLACEHOLDER = "{arg}"
# Anything in braces that looks like a named placeholder (e.g. {args}, {ARG}, { file }), but not {} or code such as {1: 2}
PLACEHOLDER_PATTERN = re.compile(r"\{\s*[A-Za-z_][\w\s-]*\}")
COMMAND_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# Programs and options that run any command or code given to them, which the user must review with extra care
SHELL_PROGRAMS = {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "wsl"}
CODE_OPTIONS = {"-c", "-e", "/c", "/k", "-command", "--command", "--eval"}
DEFAULT_TIMEOUT_SECONDS = 120
MAX_STREAM_CHARS = int(MAX_OUTPUT_CHARS * 0.45)  # Per stream (stdout and stderr), leaving room for JSON escaping
OUTPUT_HEAD_RATIO = 0.25  # Share of truncated output kept from the start, the rest is kept from the end
IS_WINDOWS = os.name == "nt"
# Characters cmd.exe interprets even when Windows batch files (.bat/.cmd) are run without a shell
BATCH_UNSAFE_CHARS = set('&|<>^%!"()')

# --- Internal Helpers ---

def _load_registry() -> dict[str, dict[str, Any]]:
    """
    Loads the command registry named by command_registry, or returns an empty registry if it is not set.
    Raises an error if the file is missing, is not valid JSON or has an incomplete command, so that
    a broken registry does not silently look like a registry without commands.
    """
    if not REGISTRY_PATH:
        return {}
    if not REGISTRY_PATH.is_file():
        raise FileNotFoundError(f"command_registry: command registry not found: {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        registry = json.load(f)
    if not isinstance(registry, dict):
        raise ValueError(f"{REGISTRY_PATH}: the command registry must be a JSON object of commands.")
    for name, info in registry.items():
        problem = _command_problem(name, info)
        if problem:
            raise ValueError(f"{REGISTRY_PATH}: {problem}")
    return registry

def _command_problem(name: str, info: Any) -> str | None:
    """
    Returns the reason why a command of the registry is invalid, or None if it is valid.
    """
    if not isinstance(info, dict) or not isinstance(info.get("template"), str) or not isinstance(info.get("description"), str):
        return f"command '{name}' must have a 'template' and a 'description' string."
    for placeholder in PLACEHOLDER_PATTERN.findall(info["template"]):
        if placeholder != ARG_PLACEHOLDER:
            return (f"command '{name}' has an unknown placeholder '{placeholder}' in its template."
                    f" The only placeholder is {ARG_PLACEHOLDER}, which is replaced with the AI's argument.")
    timeout = info.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        return f"the 'timeout' of command '{name}' must be a positive number of seconds."
    return None

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

def _suggestion_warnings(tokens: list[str]) -> list[str]:
    """
    Returns what the user should check before adding a suggested command: a missing program, and templates
    that let the AI run any command or code.
    """
    warnings = []
    if not shutil.which(tokens[0]):
        warnings.append(f"Program '{tokens[0]}' was not found on PATH. Install it before adding the command.")
    if Path(tokens[0]).stem.lower() in SHELL_PROGRAMS:
        warnings.append(f"The program '{tokens[0]}' is a shell, which can run any command. Check the whole template carefully.")
    if any(ARG_PLACEHOLDER in t for t in tokens) and any(t.lower() in CODE_OPTIONS for t in tokens[1:]):
        warnings.append(f"The template runs code given on the command line, and {ARG_PLACEHOLDER} can become part of that code,"
                        " so the AI could run any code through this command.")
    return warnings

def _save_suggestion(name: str, entry: dict[str, Any]) -> bool:
    """
    Adds the suggested command to the COMMAND_SUGGESTIONS file, in the format of the registry.
    Returns True if it replaced an earlier suggestion with the same name.
    """
    suggestions = {}
    if COMMAND_SUGGESTIONS.is_file():
        try:
            with open(COMMAND_SUGGESTIONS, "r", encoding="utf-8") as f:
                suggestions = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"The suggestions file {COMMAND_SUGGESTIONS} is not valid JSON ({e}). Ask the user to fix or remove it.") from e
        if not isinstance(suggestions, dict):
            raise ValueError(f"The suggestions file {COMMAND_SUGGESTIONS} must contain a JSON object. Ask the user to fix or remove it.")
    replaced = name in suggestions
    suggestions[name] = entry
    COMMAND_SUGGESTIONS.parent.mkdir(parents=True, exist_ok=True)
    temporary = COMMAND_SUGGESTIONS.with_name(COMMAND_SUGGESTIONS.name + ".tmp")
    temporary.write_text(json.dumps(suggestions, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, COMMAND_SUGGESTIONS)  # Replaced in one step, so that a failed write cannot leave a broken file
    return replaced

def _suggest_command_logic(name: str, template: str, description: str, timeout: int | float | None = None) -> dict[str, Any]:
    """
    Validates a suggested command as the registry is validated, and saves it to COMMAND_SUGGESTIONS if that is set.
    Never changes the registry: the user reviews the suggestion and copies it there.
    Returns the entry formatted for the registry, the warnings for the user and what the AI should tell the user.
    """
    if not COMMAND_NAME_PATTERN.fullmatch(name):
        return {"success": False, "error": "invalid_name", "message": "The name must start with a letter and contain only letters, digits and '_', e.g. 'run_e2e_tests'."}
    if name in COMMAND_REGISTRY:
        return {"success": False, "error": "already_registered", "message": f"'{name}' is already a registered command. Run it with run_predefined_command, or suggest the new command with another name."}

    entry: dict[str, Any] = {"template": template.strip(), "description": description.strip()}
    if timeout is not None:
        entry["timeout"] = timeout
    if not entry["description"]:
        return {"success": False, "error": "invalid_command", "message": "The description must say when to use the command, and what its argument is if it takes one."}
    problem = _command_problem(name, entry)
    if problem:
        return {"success": False, "error": "invalid_command", "message": problem[0].upper() + problem[1:]}
    try:
        tokens = _split_template(entry["template"])
    except ValueError as e:
        return {"success": False, "error": "invalid_template", "message": f"The template cannot be split into a program and its arguments: {e}. Check the quotes."}
    if not tokens:
        return {"success": False, "error": "invalid_template", "message": "The template is empty. Give the program and its arguments, e.g. 'npm run e2e'."}
    if ARG_PLACEHOLDER in tokens[0]:
        return {"success": False, "error": "invalid_template", "message": f"The program cannot be {ARG_PLACEHOLDER}: the template must name the program to run."}

    replaced = _save_suggestion(name, entry) if COMMAND_SUGGESTIONS else False
    formatted = json.dumps({name: entry}, indent=4, ensure_ascii=False).splitlines()[1:-1]
    where = f"is saved in {COMMAND_SUGGESTIONS.as_posix()}" if COMMAND_SUGGESTIONS else "is shown below"
    registry = f"the command registry {REGISTRY_PATH.as_posix()}" if REGISTRY_PATH else "a command registry, set as command_registry in the tools config file"
    return {
        "success": True,
        "name": name,
        "requires_argument": ARG_PLACEHOLDER in entry["template"],
        "warnings": _suggestion_warnings(tokens),
        "suggestions_file": COMMAND_SUGGESTIONS.as_posix() if COMMAND_SUGGESTIONS else None,
        "replaced_earlier_suggestion": replaced,
        "entry": textwrap.dedent("\n".join(formatted)),
        "message": (f"The command was not added and cannot be run yet. Tell the user that the suggestion {where}, show them the entry"
                    f" and any warnings, and that they can copy it into {registry} and restart ollmcp to use it."
                    " Do not try to run the command another way.")
    }

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
            "message": f"'{command_key}' is not a registered command. Available commands: {available}. Use list_available_commands to see what each one does, or suggest_command to suggest a missing one to the user."
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
            cwd=ALLOWED_DIR,  # Commands such as tests and linters work on the project, like the other toolsets
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
    Lists the commands run_predefined_command can run, with what each one is for.
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
def run_predefined_command(command_name: str, argument: str = "") -> str:
    """
    Runs a command listed by list_available_commands, in the project folder. It succeeds only with
    exit code 0. Long output is cut from the middle.

    Args:
        command_name: The command's name.
        argument: The argument, for commands that take one. It must not start with '-'.
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

@mcp.tool()
def suggest_command(name: str, template: str, description: str, timeout: int | None = None) -> str:
    """
    Suggests a missing command to the user, who decides whether to add it. Tell the user the result.

    Args:
        name: e.g. 'run_e2e_tests'.
        template: e.g. 'npm run e2e'. Put {arg} where the argument goes, if any.
        description: When to use it, and what its argument is.
        timeout: Seconds, if it needs more than 120.
    """
    try:
        result = _suggest_command_logic(name, template, description, timeout)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "suggestion_error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2, ensure_ascii=False)
