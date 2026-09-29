# --- mcp_common.py ---
"""
Shared configuration and helpers for the MCP servers in this folder.

All settings come from one JSON config file, whose path is given with the MCP_TOOLS_CONFIG
environment variable, so that every server sees exactly the same allowed directory,
forbidden paths and output limit. The servers import this module, which works because
Python adds a script's own folder to its import path.
"""
import fnmatch
import json
import os
import subprocess
from pathlib import Path

# --- Constants & Config ---

CONFIG_ENV_VAR = "MCP_TOOLS_CONFIG"
CONFIG_KEYS = {"allowed_dir", "forbidden_paths", "max_output_chars", "gitignore_path", "commands_config", "context_folder", "read_only_files", "ignored_dirs"}
DEFAULT_MAX_OUTPUT_CHARS = 40000
MIN_MAX_OUTPUT_CHARS = 2000
PROTECTED_FILE_NAMES = {".mcp.json"}  # MCP server configuration, never accessible regardless of forbidden_paths
# Folder names the map and search tools skip at any depth, unless the tools config sets its own ignored_dirs
DEFAULT_IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}
# File types included in the codebase maps
PYTHON_SUFFIXES = {".py"}
JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
OTHER_SUFFIXES = {".md", ".json", ".css", ".scss", ".html"}
ALL_ALLOWED_SUFFIXES = PYTHON_SUFFIXES | JS_TS_SUFFIXES | OTHER_SUFFIXES

# --- Config Loading ---

def _load_config() -> tuple[Path, dict]:
    """
    Loads the config file named by MCP_TOOLS_CONFIG. Raises an error if it is not set, does not exist,
    is not a JSON object or contains unknown settings, so that a server never starts with a wrong configuration.
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
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown settings {sorted(unknown)}. Valid settings: {sorted(CONFIG_KEYS)}.")
    if config.get("allowed_dir") in (None, ""):
        raise ValueError(f"{path}: 'allowed_dir' is required.")
    return path, config

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
ALLOWED_DIR = _setting_path("allowed_dir", CONFIG_DIR)
if not ALLOWED_DIR.is_dir():
    raise NotADirectoryError(f"{CONFIG_PATH}: 'allowed_dir' is not an existing folder: {ALLOWED_DIR}")
FORBIDDEN_PATHS = _setting_paths("forbidden_paths", ALLOWED_DIR)
# Largest response size in characters (about 4 characters per token). Lower it for models with small context windows.
MAX_OUTPUT_CHARS = _setting_max_output_chars()
GITIGNORE_PATH = _setting_path("gitignore_path", CONFIG_DIR) or ALLOWED_DIR / ".gitignore"
COMMANDS_CONFIG = _setting_path("commands_config", CONFIG_DIR)
CONTEXT_FOLDER = _setting_path("context_folder", CONFIG_DIR)
READ_ONLY_FILES = _setting_paths("read_only_files", CONFIG_DIR)
IGNORED_DIRS = _setting_ignored_dirs()

# --- Shared Helpers ---

def is_protected(path: Path) -> bool:
    """
    Checks if the given path is a protected file: a file named in PROTECTED_FILE_NAMES or the tools config file,
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
        if name == CONFIG_PATH.name.lower():
            try:
                if path.resolve() == CONFIG_PATH:
                    return True
            except OSError:
                return True
    return False

def is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a protected file, a forbidden file or is located within a forbidden folder.
    """
    if is_protected(path):
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
    if CONFIG_PATH.is_relative_to(resolved):
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
        return f"{label} is protected: '{path.name}' contains the MCP server or tools configuration and cannot be accessed."
    if is_forbidden(path):
        return f"{label} is forbidden: {path}"
    return f"{label} is not within the allowed directory: {ALLOWED_DIR}"

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
