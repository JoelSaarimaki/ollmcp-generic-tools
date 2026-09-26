from mcp.server.mcpserver import MCPServer
import os
import re
from pathlib import Path

mcp = MCPServer("ContentSearch")

# --- Constants & Config ---
INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd()))
IGNORED_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", ".next"}

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

    for path in root_dir.rglob("*"):
        # Skip directories, files without extensions, and ignored directories
        if path.is_dir() or not path.suffix or any(ignored in path.parts for ignored in IGNORED_DIRS):
            continue
            
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, 1):
                    if pattern.search(line):
                        rel_path = path.relative_to(root_dir)
                        results.append(f"{rel_path}:{line_num}: {line.strip()}")
        except Exception:
            continue  # Skip unreadable or binary files

    if not results:
        return f"No matches found for '{query}' in {root_dir}"
        
    # Cap output to protect context window limit
    output_lines = results[:100]
    output = "\n".join(output_lines)
    if len(results) > 100:
        output += f"\n... (and {len(results) - 100} more matches)"
        
    return output

# --- Public MCP Tools ---

@mcp.tool()
def search_text_in_files(query: str, case_sensitive: bool = False) -> str:
    """
    Search for text or regex patterns from code files in the codebase.
    Use this to find all mentions of something in the codebase.

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
def search_files_by_pattern(pattern: str) -> str:
    """
    Searches for files and directories that match a glob-style pattern.

    Args:
        pattern (str): A glob pattern (e.g., `**/*.ts`, `src/utils/*.py`, `README*`).

    Returns:
        str: A string list of matching file paths or an error message.
    """
    try:
        # We use INPUT_DIR as the base for searching
        # Use rglob for recursive globbing if the pattern doesn't specify it, 
        # but pathlib's glob/rglob behavior is what we want.
        
        matches = []
        # Use rglob if pattern starts with ** or just glob if it's a simple pattern
        # For simplicity and following spec, we'll use rglob if possible
        # or just iterate and match.
        
        # A more robust way to handle both glob and rglob:
        if "**" in pattern:
            for p in INPUT_DIR.rglob(pattern):
                if not any(ignored in p.parts for ignored in IGNORED_DIRS):
                    matches.append(str(p.relative_to(INPUT_DIR)))
        else:
            for p in INPUT_DIR.glob(pattern):
                if not any(ignored in p.parts for ignored in IGNORED_DIRS):
                    matches.append(str(p.relative_to(INPUT_DIR)))

        if not matches:
            return f"No files found matching pattern: '{pattern}'"

        # Cap the results
        if len(matches) > 100:
            return "\n".join(matches[:100]) + f"\n... (and {len(matches) - 100} more matches)"
        
        return "\n".join(matches)
    except Exception as e:
        return f"Error searching for files: {str(e)}"

if __name__ == "__main__":
    mcp.run()
