from mcp.server.mcpserver import MCPServer
import os
import re
import traceback
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
        case_sensitive (bool): Whether the search should be case sensitive. Default is False.

    Returns:
        str: A string containing the search results or an error message.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR} does not exist."

    try:
        output = _conduct_search(root_dir=INPUT_DIR, query=query, case_sensitive=case_sensitive)
        return f"Search results for '{query}' in '{INPUT_DIR}':\n\n{output}"
    except Exception as e:
        return f"Error searching text in files:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
