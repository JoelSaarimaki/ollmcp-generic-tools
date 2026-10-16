# --- common.py ---
"""
Shared configuration and helpers for the toolsets of this package.

All settings come from one JSON config file, whose path is given with the MCP_TOOLS_CONFIG
environment variable. The config is loaded when this module is first imported, before any toolset,
so that every toolset sees exactly the same allowed directory, forbidden paths and output limit.
"""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

# --- Constants & Config ---

CONFIG_ENV_VAR = "MCP_TOOLS_CONFIG"
CONFIG_KEYS = {"allowed_dir", "forbidden_paths", "max_output_chars", "gitignore_path", "command_registry", "command_suggestions", "context_folder", "read_only_files", "ignored_dirs", "backup_folder", "disabled_toolsets", "disabled_tools"}
# The user config file holds each person's own settings for all their projects, such as API keys,
# so that they are never committed with a project's tools config
USER_CONFIG_PATH = Path.home() / ".ollmcp-tools" / "user-config.json"
USER_CONFIG_KEYS = {"ollama_api_key"}
# Settings that were renamed or moved, with what to do instead, shown when an old config uses them
MOVED_SETTINGS = {
    "commands_config": "it is now called 'command_registry'",
    "ollama_api_key": f"it belongs in the user config file {USER_CONFIG_PATH}, so that it is not committed with the project",
}
# The toolsets, each a module of this package, and their tools, in the order they are served.
# Used to check the disabled_toolsets and disabled_tools settings before any toolset is imported.
TOOLSETS = {
    "filesystem": ["write_file", "edit_file", "create_file", "read_file_with_metadata", "read_image",
                   "list_directory", "move_file", "delete_file", "restore_file", "get_tools_config"],
    "outline": ["get_outline"],
    "search": ["search_text_in_files", "search_files_by_pattern", "search_relevant_files"],
    "git": ["get_git_diff", "get_git_history", "get_git_status"],
    "commands": ["list_available_commands", "run_predefined_command", "suggest_command"],
    "context": ["list_context_files", "read_context_file", "write_context_file", "remove_context_file"],
    "web": ["web_search", "web_fetch"],
}
DEFAULT_MAX_OUTPUT_CHARS = 40000
MIN_MAX_OUTPUT_CHARS = 2000
PROTECTED_FILE_NAMES = {".mcp.json"}  # MCP server configuration, never accessible regardless of forbidden_paths
# Backups of the files changed by the filesystem tools, for restore_file. Outside the project, so that they are not in git.
DEFAULT_BACKUP_ROOT = Path.home() / ".ollmcp-tools-backups"
# Folder names the map and search tools skip at any depth, unless the tools config sets its own ignored_dirs
DEFAULT_IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}

# Languages of fenced code blocks by file type, for syntax highlighting where responses are shown as Markdown
CODE_LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "jsx",
    ".ts": "typescript", ".tsx": "tsx", ".md": "markdown", ".json": "json", ".css": "css", ".scss": "scss",
    ".html": "html", ".xml": "xml", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".sql": "sql",
    ".sh": "bash", ".ps1": "powershell", ".bat": "batch", ".cmd": "batch"
}

# --- Config Loading ---

def _load_config() -> tuple[Path, dict]:
    """
    Loads the config file named by MCP_TOOLS_CONFIG. Raises an error if it is not set, does not exist,
    is not a JSON object or contains unknown settings, so that the server never starts with a wrong configuration.
    """
    raw_path = os.getenv(CONFIG_ENV_VAR)
    if not raw_path:
        raise RuntimeError(f"Environment variable '{CONFIG_ENV_VAR}' is not set. Set it in .mcp.json to the path of the tools config file.")
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{CONFIG_ENV_VAR}: config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: the config file must contain a JSON object.")
    moved = sorted(set(config) & set(MOVED_SETTINGS))
    if moved:
        raise ValueError(f"{path}: '{moved[0]}' is no longer a setting of the tools config file: {MOVED_SETTINGS[moved[0]]}.")
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}. Valid settings: {sorted(CONFIG_KEYS)}.")
    if config.get("allowed_dir") in (None, ""):
        raise ValueError(f"{path}: 'allowed_dir' is required.")
    return path, config

def _load_user_config() -> dict:
    """
    Loads the user config file, or returns an empty dict if it does not exist. Raises an error if it is
    not a JSON object or contains unknown settings, as the tools config file does.
    """
    if not USER_CONFIG_PATH.is_file():
        return {}
    try:
        with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"{USER_CONFIG_PATH}: the user config file is not valid JSON: {e}") from e
    if not isinstance(config, dict):
        raise ValueError(f"{USER_CONFIG_PATH}: the user config file must contain a JSON object.")
    unknown = set(config) - USER_CONFIG_KEYS
    if unknown:
        raise ValueError(f"{USER_CONFIG_PATH}: unknown settings {sorted(unknown)}. Valid settings: {sorted(USER_CONFIG_KEYS)}.")
    return config

def _setting_path(key: str, base_dir: Path) -> Path | None:
    """
    Returns a path setting resolved against base_dir, or None if it is not set.
    """
    value = CONFIG.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{CONFIG_PATH}: '{key}' must be a path string.")
    p = Path(value)
    return (p if p.is_absolute() else base_dir / p).resolve()

def _setting_paths(key: str, base_dir: Path) -> list[Path]:
    """
    Returns a list of paths setting resolved against base_dir, or an empty list if it is not set.
    """
    values = CONFIG.get(key) or []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"{CONFIG_PATH}: '{key}' must be a list of path strings.")
    paths = [Path(v.strip()) for v in values if v.strip()]
    return [(p if p.is_absolute() else base_dir / p).resolve() for p in paths]

def _setting_ignored_dirs() -> set[str]:
    """
    Returns the ignored_dirs setting as a set of folder names, or DEFAULT_IGNORED_DIRS if it is not set or null.
    A list given in the config replaces the defaults completely, so that a default such as 'build' can be un-ignored.
    """
    values = CONFIG.get("ignored_dirs")
    if values is None:
        return set(DEFAULT_IGNORED_DIRS)
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError(f"{CONFIG_PATH}: 'ignored_dirs' must be a list of folder names.")
    paths = [v for v in values if "/" in v or "\\" in v]
    if paths:
        raise ValueError(f"{CONFIG_PATH}: 'ignored_dirs' must contain folder names, not paths: {paths}. Folders with these names are skipped at any depth. To exclude one specific folder, add it to 'forbidden_paths' instead.")
    return {v.strip() for v in values}

def _setting_names(key: str, valid: list[str]) -> set[str]:
    """
    Returns a list of names setting as a set, or an empty set if it is not set or null. Raises an error
    for names not in valid, so that a typo cannot leave a toolset or tool enabled.
    """
    values = CONFIG.get(key)
    if values is None:
        return set()
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"{CONFIG_PATH}: '{key}' must be a list of names.")
    unknown = [v for v in values if v not in valid]
    if unknown:
        raise ValueError(f"{CONFIG_PATH}: '{key}' contains unknown names {unknown}. Valid names: {valid}.")
    return set(values)

def _user_secret(key: str) -> str | None:
    """
    Returns a secret string setting of the user config file, such as an API key, or None if it is not set, null or empty.
    """
    value = USER_CONFIG.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{USER_CONFIG_PATH}: '{key}' must be a string.")
    return value.strip() or None

def _setting_max_output_chars() -> int:
    """
    Returns the max_output_chars setting, at least MIN_MAX_OUTPUT_CHARS, or DEFAULT_MAX_OUTPUT_CHARS if it is not set or null.
    """
    value = CONFIG.get("max_output_chars")
    if value is None:
        return DEFAULT_MAX_OUTPUT_CHARS
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{CONFIG_PATH}: 'max_output_chars' must be a whole number.")
    return max(MIN_MAX_OUTPUT_CHARS, value)

# Paths in the config file are relative to its folder, except forbidden_paths, which are relative to allowed_dir
CONFIG_PATH, CONFIG = _load_config()
CONFIG_DIR = CONFIG_PATH.parent
USER_CONFIG = _load_user_config()
DISABLED_TOOLSETS = _setting_names("disabled_toolsets", list(TOOLSETS))
DISABLED_TOOLS = _setting_names("disabled_tools", [tool for tools in TOOLSETS.values() for tool in tools])
# A toolset whose tools are all disabled is not loaded either, so its settings are not checked
ENABLED_TOOLSETS = [name for name, tools in TOOLSETS.items()
                    if name not in DISABLED_TOOLSETS and not DISABLED_TOOLS.issuperset(tools)]
if not ENABLED_TOOLSETS:
    raise ValueError(f"{CONFIG_PATH}: 'disabled_toolsets' and 'disabled_tools' disable every tool.")
ALLOWED_DIR = _setting_path("allowed_dir", CONFIG_DIR)
if not ALLOWED_DIR.is_dir():
    raise NotADirectoryError(f"{CONFIG_PATH}: 'allowed_dir' is not an existing folder: {ALLOWED_DIR}."
                             f" '{CONFIG['allowed_dir']}' is relative to the folder of the config file, {CONFIG_DIR}.")
FORBIDDEN_PATHS = _setting_paths("forbidden_paths", ALLOWED_DIR)
# Largest response size in characters (about 4 characters per token). Lower it for models with small context windows.
MAX_OUTPUT_CHARS = _setting_max_output_chars()
GITIGNORE_PATH = _setting_path("gitignore_path", CONFIG_DIR) or ALLOWED_DIR / ".gitignore"
REGISTRY_PATH = _setting_path("command_registry", CONFIG_DIR)
# The file where suggest_command writes suggested commands for the user to review. It is never read as a registry.
COMMAND_SUGGESTIONS = _setting_path("command_suggestions", CONFIG_DIR)
# Files protected by their path: the tools config file, the user config file and the command registry. If the AI could
# change them, it could remove its own restrictions or register any command, which would take effect at the next server start.
PROTECTED_PATHS = [CONFIG_PATH, USER_CONFIG_PATH] + ([REGISTRY_PATH] if REGISTRY_PATH else [])
CONTEXT_FOLDER = _setting_path("context_folder", CONFIG_DIR)
READ_ONLY_FILES = _setting_paths("read_only_files", CONFIG_DIR)
IGNORED_DIRS = _setting_ignored_dirs()
# API key for Ollama's hosted web search and fetch (the web toolset). Never shown to the AI: the user config file is protected.
OLLAMA_API_KEY = _user_secret("ollama_api_key")
# One default backup folder per project, named after it and its path, so that projects with the same name do not share one
_PROJECT_KEY = hashlib.sha256(str(ALLOWED_DIR).lower().encode("utf-8")).hexdigest()[:12]
BACKUP_DIR = _setting_path("backup_folder", CONFIG_DIR) or DEFAULT_BACKUP_ROOT / f"{ALLOWED_DIR.name}-{_PROJECT_KEY}"
if ALLOWED_DIR.is_relative_to(BACKUP_DIR):
    raise ValueError(f"{CONFIG_PATH}: 'backup_folder' must not contain 'allowed_dir', as the backup folder is hidden from the tools: {BACKUP_DIR}")
if COMMAND_SUGGESTIONS and (COMMAND_SUGGESTIONS in PROTECTED_PATHS or COMMAND_SUGGESTIONS.name.lower() in PROTECTED_FILE_NAMES
                            or COMMAND_SUGGESTIONS.is_relative_to(BACKUP_DIR)):
    raise ValueError(f"{CONFIG_PATH}: 'command_suggestions' must be a file of its own, not the tools config file, the user config file, the 'command_registry', a '.mcp.json' file or in the backup folder: {COMMAND_SUGGESTIONS}")

# --- Shared Helpers ---

def is_protected(path: Path) -> bool:
    """
    Checks if the given path is a protected file: a file named in PROTECTED_FILE_NAMES or in PROTECTED_PATHS,
    also through a symlink or a Windows alias of the name, such as a trailing dot or a '::$DATA' stream suffix.
    """
    names = [path.name]
    try:
        if path.is_symlink():
            names.append(path.resolve().name)
    except OSError:
        return True
    for name in names:
        name = name.lower()
        if os.name == "nt":
            name = name.split(":")[0].rstrip(" .")
        if name in PROTECTED_FILE_NAMES:
            return True
        for protected in PROTECTED_PATHS:
            if name == protected.name.lower():
                try:
                    if path.resolve() == protected:
                        return True
                except OSError:
                    return True
    return False

def is_backup(path: Path) -> bool:
    """
    Checks if the given path is in the backup folder, which only restore_file may change.
    """
    try:
        return path.resolve().is_relative_to(BACKUP_DIR)
    except OSError:
        return True

def is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a protected file, in the backup folder, a forbidden file or is located within a forbidden folder.
    """
    if is_protected(path) or is_backup(path):
        return True
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(resolved.is_relative_to(forbidden) for forbidden in FORBIDDEN_PATHS)

def contains_forbidden(path: Path) -> bool:
    """
    Checks if the given directory contains any forbidden or protected files or folders.
    Used to stop recursive operations (delete, move) from touching forbidden or protected paths.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if any(forbidden.is_relative_to(resolved) for forbidden in FORBIDDEN_PATHS):
        return True
    if any(protected.is_relative_to(resolved) for protected in PROTECTED_PATHS) or BACKUP_DIR.is_relative_to(resolved):
        return True
    if resolved.is_dir():
        for dirpath, _, filenames in os.walk(resolved):
            if any(is_protected(Path(dirpath) / name) for name in filenames):
                return True
    return False

def resolve_path(path: str) -> Path:
    """
    Resolves a path given to a tool. Relative paths are resolved against ALLOWED_DIR.
    """
    p = Path(path)
    return (p if p.is_absolute() else ALLOWED_DIR / p).resolve()

def is_path_allowed(path: Path) -> bool:
    """
    Checks if the given path is within ALLOWED_DIR and not forbidden.
    """
    try:
        return path.resolve().is_relative_to(ALLOWED_DIR) and not is_forbidden(path)
    except (ValueError, OSError):
        return False

def access_denied_message(path: Path, label: str = "Path") -> str:
    """
    Returns the reason why access to the given path was denied.
    """
    if is_protected(path):
        return f"{label} is protected: '{path.name}' contains the MCP server, tools or user configuration or the command registry, and cannot be accessed."
    if is_backup(path):
        return f"{label} is protected: it is in the backup folder of restore_file, which cannot be accessed directly."
    if is_forbidden(path):
        return f"{label} is forbidden: {display_path(path, ALLOWED_DIR)}"
    return f"{label} is not within the allowed directory: {ALLOWED_DIR.as_posix()}"

def display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

def load_gitignore_patterns() -> list[str]:
    """
    Loads the patterns from the .gitignore file at GITIGNORE_PATH.
    """
    if not GITIGNORE_PATH.exists():
        return []

    patterns = []
    try:
        with open(GITIGNORE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except Exception:
        pass
    return patterns

def is_ignored(path: Path, base_dir: Path, ignored_dirs: set[str], gitignore_patterns: list[str]) -> bool:
    """
    Checks if a path is excluded by forbidden paths, ignored_dirs or .gitignore patterns.
    Only the part of the path within base_dir is compared, so that a project located in a folder
    named like an ignored folder (e.g. 'C:/work/out/project') is not ignored as a whole.
    """
    if is_forbidden(path):
        return True

    try:
        rel_parts = path.relative_to(base_dir).parts
    except ValueError:
        return False

    if any(ignored in rel_parts for ignored in ignored_dirs):
        return True

    rel_path = "/".join(rel_parts)
    for pattern in gitignore_patterns:
        if fnmatch.fnmatch(rel_path, pattern) or \
           fnmatch.fnmatch(f"{rel_path}/", pattern) or \
           any(fnmatch.fnmatch(part, pattern) for part in rel_parts):
            return True

    return False

def walk(base_dir: Path, gitignore_patterns: list[str], start_dir: Path, max_depth: int | None = None):
    """
    Yields (path, is_dir) for the folders and files within start_dir that are not ignored, in sorted order.
    Ignored folders are skipped without being entered, which keeps large folders such as node_modules fast.
    Folders deeper than max_depth levels below start_dir are not entered.
    """
    for dirpath, dirnames, filenames in os.walk(start_dir):
        current_dir = Path(dirpath)
        depth = len(current_dir.relative_to(start_dir).parts)
        dirnames[:] = sorted(
            (d for d in dirnames if not is_ignored(current_dir / d, base_dir, IGNORED_DIRS, gitignore_patterns)),
            key=str.lower
        )
        for name in dirnames:
            yield current_dir / name, True
        if max_depth is not None and depth + 1 >= max_depth:
            dirnames[:] = []
        for name in sorted(filenames, key=str.lower):
            path = current_dir / name
            if not is_ignored(path, base_dir, IGNORED_DIRS, gitignore_patterns):
                yield path, False

def get_repo_root(directory: Path) -> Path | None:
    """
    Returns the root of the git repository containing the directory, or None if it is not in a repository.
    """
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "rev-parse", "--show-toplevel"],
            cwd=directory, capture_output=True, encoding="utf-8", errors="replace", check=False
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()

def prepare_tools(server) -> None:
    """
    Prepares a server's tools for small models, after they are defined:
    - Shortens the parameter schemas, which are sent to the model with every request: removes the titles
      generated from the parameter names, and turns 'anyOf: [type, null]' of optional parameters into the type.
    - Makes an argument given as null use the parameter's default, as small models often send null for
      optional parameters they do not use. A required parameter given as null is reported as missing.
    Uses the server's tool manager, as MCPServer has no options for this. Arguments are still validated
    from the function signatures.
    """
    manager = server._tool_manager
    for tool in manager.list_tools():
        schema = tool.parameters
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
            types = [option for option in prop.get("anyOf", []) if option.get("type") != "null"]
            if "anyOf" in prop and len(types) == 1:
                del prop["anyOf"]
                prop.update(types[0])

    call_tool = manager.call_tool

    async def call_tool_without_nulls(name, arguments, *args, **kwargs):
        return await call_tool(name, {key: value for key, value in (arguments or {}).items() if value is not None}, *args, **kwargs)

    manager.call_tool = call_tool_without_nulls

# --- Response Formatting ---
# ollmcp shows a text response as Markdown if it contains enough Markdown-like patterns, and otherwise
# as plain text. Responses are written to read well both ways: header lines as a list, so that Markdown
# does not join them into one paragraph, and file content and other line-based output in fenced code
# blocks, so that it keeps its lines and '#' comments do not become headings.

def code_language(path: Path | str) -> str:
    """
    Returns the language of a fenced code block for the file type of path, or 'text'.
    """
    return CODE_LANGUAGES.get(Path(path).suffix.lower(), "text")

def code_block(text: str, language: str = "text") -> str:
    """
    Returns text as a fenced code block. The fence is longer than any run of backticks in the text,
    so that text containing code blocks, such as a Markdown file, stays inside it.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"

def text_response(header: list[str], *parts: str) -> str:
    """
    Returns a text response: the header lines as a list, followed by the parts (such as code blocks),
    separated by blank lines. Empty lines and parts are left out.
    """
    blocks = ["\n".join(f"- {line}" for line in header if line)] + list(parts)
    return "\n\n".join(block for block in blocks if block)
