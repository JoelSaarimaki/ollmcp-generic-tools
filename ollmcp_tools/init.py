# --- init.py ---
"""
'python -m ollmcp_tools init' sets up the tools for the project in the current folder: .mcp.json and the
.ollmcp-tools folder, from the templates in this package, and the user config file if it does not exist yet.

Existing files are never overwritten, so it is safe to run again, e.g. after cloning a project whose
.ollmcp-tools folder is committed: it then only creates the files each person keeps for themselves.
This module does not import common.py, as there is no tools config to load yet.
"""
import json
from importlib.resources import files
from pathlib import Path

TOOLS_FOLDER = ".ollmcp-tools"
CONFIG_FILE = f"{TOOLS_FOLDER}/tools-config.json"
# Template name in the package -> path in the project. Templates have no leading dot, so that the
# tools do not treat the package's own mcp.json as a protected .mcp.json file.
TEMPLATE_FILES = {
    "mcp.json": ".mcp.json",
    "tools-config.json": CONFIG_FILE,
    "commands.json": f"{TOOLS_FOLDER}/commands.json",
    "instructions.md": f"{TOOLS_FOLDER}/instructions.md",
    "gitignore": f"{TOOLS_FOLDER}/.gitignore",
}
CONTEXT_FOLDER = f"{TOOLS_FOLDER}/context"
# Same path as USER_CONFIG_PATH in common.py, which cannot be imported here
USER_CONFIG_PATH = Path.home() / ".ollmcp-tools" / "user-config.json"
# Starting the session with a tool call also keeps qwen3-coder's tool calls working (see "Set a system prompt" in the README).
# The tool name is written as ollmcp sends it to the model.
SYSTEM_PROMPT = f"At the start of a session, call dev_read_file_with_metadata with path '{TOOLS_FOLDER}/instructions.md' and follow it."

# --- Internal Helpers ---

def _project_dir_line(project: Path) -> str:
    """
    Returns the checklist line that explains the project_dir of the project's tools config, resolved to a folder.
    """
    config_path = project / CONFIG_FILE
    try:
        project_dir = json.loads(config_path.read_text(encoding="utf-8"))["project_dir"]
        resolved = (config_path.parent / project_dir).resolve()
    except Exception as e:
        return f"{CONFIG_FILE} could not be read to check its 'project_dir' ({e}). Fix the file before starting ollmcp."
    return (f"{CONFIG_FILE}: 'project_dir' is \"{project_dir}\", as paths in the config are relative to its own folder,"
            f" {TOOLS_FOLDER}. It is the project folder the tools can access: {resolved}."
            f" Add the folders and files the AI must never access to 'forbidden_paths'.")

def _create(project: Path) -> tuple[list[str], list[str]]:
    """
    Creates the missing project files from the templates, the context folder and the user config file.
    Returns the created and the skipped (already existing) paths.
    """
    created, skipped = [], []
    templates = files(__package__) / "templates"
    for template, rel in TEMPLATE_FILES.items():
        target = project / rel
        if target.exists():
            skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((templates / template).read_bytes())
        created.append(rel)

    context = project / CONTEXT_FOLDER
    if context.is_dir():
        skipped.append(f"{CONTEXT_FOLDER}/")
    else:
        context.mkdir(parents=True)
        created.append(f"{CONTEXT_FOLDER}/")

    if USER_CONFIG_PATH.exists():
        skipped.append(str(USER_CONFIG_PATH))
    else:
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_CONFIG_PATH.write_text(json.dumps({"ollama_api_key": None}, indent=4) + "\n", encoding="utf-8")
        created.append(str(USER_CONFIG_PATH))
    return created, skipped

def _checklist(project: Path) -> list[str]:
    """
    Returns the steps the user should take after init, in order.
    """
    return [
        _project_dir_line(project),
        f"{TOOLS_FOLDER}/commands.json: keep the commands that fit this project, and change or delete the rest."
        " The AI can suggest missing commands with suggest_command, for you to copy here.",
        f"{TOOLS_FOLDER}/instructions.md: adapt the instructions to the project, and remove the tools you disable.",
        f"Web search: add your Ollama API key to {USER_CONFIG_PATH} (one key for all your projects)."
        " Without a key the web tools are not offered to the AI.",
        f"Set ollmcp's system prompt with /model-config and save it with /save-config as 'default': \"{SYSTEM_PROMPT}\""
        " It also keeps the tool calls working.",
        f"Commit .mcp.json and {TOOLS_FOLDER}. {TOOLS_FOLDER}/.gitignore leaves out the context folder and the"
        " suggested commands, which are each person's own. Others who clone the project run 'python -m ollmcp_tools init' to create them.",
        "Start ollmcp in this folder.",
    ]

# --- Public Entry Point ---

def run(project: Path) -> str:
    """
    Sets up the tools for the project folder and returns the report to print: what was created,
    what already existed and what to do next.
    """
    created, skipped = _create(project)
    lines = [f"Setting up ollmcp-tools in {project}"]
    lines += [f"  Created: {path}" for path in created]
    lines += [f"  Already exists, not changed: {path}" for path in skipped]
    lines += ["", "Next steps:"]
    lines += [f"  {number}. {step}" for number, step in enumerate(_checklist(project), start=1)]
    return "\n".join(lines)
