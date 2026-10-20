# --- init.py ---
"""
'python -m ollmcp_tools init' sets up the tools for the project in the current folder: .mcp.json and the
.ollmcp-tools folder, from the templates in this package, and the user config file and the project's
backup folder if they do not exist yet.

Existing files are never overwritten, so it is safe to run again, e.g. after cloning a project whose
.ollmcp-tools folder is committed: it then only creates the files each person keeps for themselves.
This module does not import common.py, as there is no tools config to load yet.
"""
import json
from importlib.resources import files
from pathlib import Path

from .locations import USER_CONFIG_PATH, default_backup_folder

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
    # Created empty, so that the user sees where the AI's suggestions will be
    "suggested-commands.json": f"{TOOLS_FOLDER}/suggested-commands.json",
}
CONTEXT_FOLDER = f"{TOOLS_FOLDER}/context"
# Starting the session with a tool call also keeps qwen3-coder's tool calls working (see "Starting a session" in the README).
# The tool name is written as ollmcp sends it to the model.
SYSTEM_PROMPT = f"At the start of a session, call dev_read_file_with_metadata with path '{TOOLS_FOLDER}/instructions.md' and follow it."
# The first message of every session: with the system prompt, it got the instructions read in every test (see local_ai_test_results.md)
FIRST_MESSAGE = "Read the project instructions."

# --- Internal Helpers ---

def _read_config(project: Path) -> tuple[dict | None, str]:
    """
    Returns the project's tools config, or None and the reason if it cannot be read.
    """
    try:
        return json.loads((project / CONFIG_FILE).read_text(encoding="utf-8")), ""
    except Exception as e:
        return None, str(e)

def _project_dir(project: Path, config: dict) -> Path:
    """
    Returns the project_dir of the tools config, resolved against the config file's folder as the server does.
    """
    return ((project / CONFIG_FILE).parent / config["project_dir"]).resolve()

def _backup_folder(project: Path, config: dict) -> Path:
    """
    Returns the backup folder the server will use: backup_folder resolved against project_dir, or the default one.
    """
    project_dir = _project_dir(project, config)
    if config.get("backup_folder"):
        return (project_dir / config["backup_folder"]).resolve()
    return default_backup_folder(project_dir)

def _project_dir_line(project: Path) -> str:
    """
    Returns the checklist line that explains the project_dir of the project's tools config, resolved to a folder.
    """
    config, error = _read_config(project)
    try:
        resolved = _project_dir(project, config) if config else None
    except Exception as e:
        resolved, error = None, str(e)
    if not resolved:
        return f"{CONFIG_FILE} could not be read to check its 'project_dir' ({error}). Fix the file before starting ollmcp."
    return (f"{CONFIG_FILE}: 'project_dir' is \"{config['project_dir']}\", as it is relative to the config file's own folder,"
            f" {TOOLS_FOLDER}. It is the project folder the tools can access, and every other path in the config is relative to it: {resolved}."
            f" Add the folders and files the AI must never access to 'forbidden_paths'.")

def _create(project: Path) -> tuple[list[str], list[str]]:
    """
    Creates the missing project files from the templates, the context folder, the user config file and the
    project's backup folder. Returns the created and the skipped (already existing) paths.
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

    # Created now, so that the user sees where restore_file keeps the backups, including deleted files
    config, _ = _read_config(project)
    try:
        backups = _backup_folder(project, config) if config else None
    except Exception:
        backups = None  # The checklist reports a broken config
    if backups and backups.is_dir():
        skipped.append(f"{backups} (backup folder)")
    elif backups:
        backups.mkdir(parents=True)
        created.append(f"{backups} (backup folder)")
    return created, skipped

def _checklist(project: Path) -> list[str]:
    """
    Returns the steps the user should take after init, in order.
    """
    return [
        _project_dir_line(project),
        f"{TOOLS_FOLDER}/commands.json: keep the commands that fit this project, and change or delete the rest."
        f" The AI can suggest missing commands with suggest_command, into {TOOLS_FOLDER}/suggested-commands.json, for you to copy here.",
        f"{TOOLS_FOLDER}/instructions.md: adapt the instructions to the project, and remove the tools you disable.",
        f"Web search: add your Ollama API key to {USER_CONFIG_PATH} (one key for all your projects)."
        " Without a key the web tools are not offered to the AI.",
        f"Set ollmcp's system prompt with /model-config and save it with /save-config as 'default': \"{SYSTEM_PROMPT}\""
        " It also keeps the tool calls working.",
        f"Start every session, also after /clear, with \"{FIRST_MESSAGE}\" and give the AI its task after that:"
        " the AI often skips the system prompt when the first message is already a task.",
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
