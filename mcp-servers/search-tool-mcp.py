from mcp.server.mcpserver import MCPServer
import os
import re
from pathlib import Path

mcp = MCPServer("ContentSearch")

# --- Constants & Config ---
INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd()))
# Expanded ignored directories for better AI experience
IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next", 
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode", 
    "target", "out", ".mypy_cache", ".ruff_cache"
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit

# --- Internal Helpers ---

def _conduct_search(root_dir: Path, query: str, case_sensitive: bool = False) -> str:
    """
    The 'meat' of the tool. Performs regex search through the file system.
    """
    results = []
    flags = 0 if case_sensitive else re.IGNORECASE
    
    try:
        pattern = re.compile(query, flags)
    except re.error:
        return f"Invalid regular expression: '{query}'"

    # Iterate through all files recursively
    for path in root_dir.rglob("*"):
        # Skip directories and ignored directories/files
        if path.is_dir() or any(ignored in path.parts for ignored in IGNORED_DIRS):
            continue
            
        try:
            # Skip files that are too large
            if path.stat().st_size > MAX_FILE_SIZE_BYTES:
                continue

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, 1):
                    if pattern.search(line):
                        rel_path = path.relative_to(root_dir)
                        results.append(f"{rel_path.as_posix()}:{line_num}: {line.strip()}")
        except Exception:
            continue  # Skip unreadable or binary files

    if not results:
        return f"No matches found for '{query}' in {root_dir.as_posix()}"
        
    # Cap output to protect context window limit
    output_lines = results[:100]
    output = f"Found {len(results)} matches:\n"
    output += "\n".join(output_lines)
    if len(results) > 100:
        output += f"\n... (and {len(results) - 100} more matches)"
        
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
        return _conduct_search(INPUT_DIR, query, case_sensitive)
    except Exception as e:
        return f"Error searching text in files: {str(e)}"

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
        matches = []
        
        if recursive:
            # Use rglob for recursive search
            for p in INPUT_DIR.rglob(pattern):
                if not any(ignored in p.parts for ignored in IGNORED_DIRS):
                    matches.append(p.relative_to(INPUT_DIR).as_posix())
        else:
            # Use glob for non-recursive search
            for p in INPUT_DIR.glob(pattern):
                if not any(ignored in p.parts for ignored in IGNORED_DIRS):
                    matches.append(p.relative_to(INPUT_DIR).as_posix())

        if not matches:
            msg = f"No files found matching pattern: '{pattern}'"
            if not recursive:
                msg += ". Try setting recursive=True to search subdirectories."
            return msg

        # Cap the results
        if len(matches) > 100:
            result_text = "\n".join(matches[:100]) + f"\n... (and {len(matches) - 100} more matches)"
        else:
            result_text = "\n".join(matches)
            
        return f"Found {len(matches)} matches:\n{result_text}"
        
    except Exception as e:
        return f"Error searching for files: {str(e)}"

if __name__ == "__main__":
    mcp.run()
