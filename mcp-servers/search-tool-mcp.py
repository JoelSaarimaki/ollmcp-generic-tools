# --- search-tool-mcp.py ---
import os
import re
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Search-Tool-Server")

INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd())).resolve()
IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit
MAX_RESULTS = 100  # Cap output to protect the context window

# --- Internal Helpers ---

def _load_forbidden_paths(base_dir: Path) -> list[Path]:
    """
    Loads the forbidden folders and files from the FORBIDDEN_PATHS env var.
    Paths are separated by commas, e.g. "folder/sub_folder/code-file.py, another_folder".
    Relative paths are resolved against base_dir. An empty or unset value means no restrictions.
    """
    raw = os.getenv("FORBIDDEN_PATHS", "").strip()
    if not raw:
        return []

    forbidden = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        p = Path(entry)
        if not p.is_absolute():
            p = base_dir / p
        forbidden.append(p.resolve())
    return forbidden

FORBIDDEN_PATHS = _load_forbidden_paths(INPUT_DIR)

def _is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a forbidden file or is located within a forbidden folder.
    """
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(resolved.is_relative_to(forbidden) for forbidden in FORBIDDEN_PATHS)

def _is_ignored(path: Path) -> bool:
    """
    Checks if a path is excluded by FORBIDDEN_PATHS or IGNORED_DIRS.
    """
    return _is_forbidden(path) or any(ignored in path.parts for ignored in IGNORED_DIRS)

def _conduct_search(root_dir: Path, query: str, case_sensitive: bool = False) -> list[str]:
    """
    Performs a recursive regex search through the files in root_dir.
    Raises ValueError if the query is not a valid regular expression.
    """
    results = []
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        pattern = re.compile(query, flags)
    except re.error:
        raise ValueError(f"Invalid regular expression: '{query}'")

    for path in root_dir.rglob("*"):
        if path.is_dir() or _is_ignored(path):
            continue

        try:
            if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                continue

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, 1):
                    if pattern.search(line):
                        rel_path = path.relative_to(root_dir)
                        results.append(f"{rel_path.as_posix()}:{line_num}: {line.strip()}")
        except Exception:
            continue  # Skip unreadable or binary files

    return results

def _format_results(results: list[str]) -> str:
    """
    Formats a list of matches, capped at MAX_RESULTS.
    """
    output = f"Found {len(results)} matches in {INPUT_DIR.as_posix()}:\n\n"
    output += "\n".join(results[:MAX_RESULTS])
    if len(results) > MAX_RESULTS:
        output += f"\n... (and {len(results) - MAX_RESULTS} more matches)"
    return output

# --- Public MCP Tools ---

@mcp.tool()
def search_text_in_files(query: str, case_sensitive: bool = False) -> str:
    """
    Search for text or regex patterns from code files in the codebase.
    This tool performs a recursive search through all files in the project.

    Args:
        query (str): The text or regex pattern to search for.
        case_sensitive (bool): Whether the search should be case-sensitive. The default is False

    Returns:
        str: A list of matching lines with file paths and line numbers, or an error message.
    """
    try:
        results = _conduct_search(INPUT_DIR, query, case_sensitive)
        if not results:
            return f"No matches found for '{query}' in {INPUT_DIR.as_posix()}"
        return _format_results(results)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Searching text in files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def search_files_by_pattern(pattern: str, recursive: bool = False) -> str:
    """
    Searches for files and directories that match a glob-style pattern.

    Args:
        pattern (str): A glob pattern (e.g., `*.ts`, `src/utils/*.py`, `README*`).
        recursive (bool): If True, performs a recursive search (equivalent to using rglob). Defaults to False.

    Returns:
        str: A string list of matching file paths or an error message.
    """
    try:
        paths = INPUT_DIR.rglob(pattern) if recursive else INPUT_DIR.glob(pattern)
        matches = [p.relative_to(INPUT_DIR).as_posix() for p in paths if not _is_ignored(p)]

        if not matches:
            msg = f"No files found matching pattern: '{pattern}'"
            if not recursive:
                msg += ". Try setting recursive=True to search subdirectories."
            return msg

        return _format_results(matches)
    except Exception as e:
        return f"Error: Searching files by pattern failed:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
