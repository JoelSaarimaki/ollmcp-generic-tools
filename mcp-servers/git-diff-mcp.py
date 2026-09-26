# --- git-diff-mcp.py ---
import json
import os
import subprocess
import traceback
from pathlib import Path
from typing import Dict, Any, List, Optional

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Git-Diff-Server")

# --- Internal Helpers ---

def _run_git_command(args: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a git command and returns the result.

    Args:
        args (List[str]): The command arguments.
        cwd (Optional[str]): The directory to run the command in.

    Returns:
        Dict[str, Any]: A dictionary containing success status, stdout, stderr, and command used.
    """
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": " ".join(cmd),
            "returncode": result.returncode
        }

    except Exception as e:
        return {
            "success": False,
            "error": "execution_failed",
            "message": str(e),
            "traceback": traceback.format_exc()
        }

# --- Public MCP Tools ---

@mcp.tool()
def get_file_diff(path: str, mode: str) -> str:
    """
    Retrieves the differences for a specified file.

    Args:
        path (str): The path to the file.
        mode (str): The type of diff to retrieve. Options:
            - 'unstaged': Changes in the working directory that are not yet staged (i.e., git diff <path>).
            - 'staged': Changes that are staged for commit but not yet committed (i.e., git diff --cached <path>).
            - 'head': Changes that have been committed compared to the current working directory/index (i.e., git diff HEAD <path>).

    Returns:
        str: A JSON-formatted string containing the git diff output or an error message.
    """
    if mode not in ["unstaged", "staged", "head"]:
        return json.dumps({
            "success": False,
            "error": "invalid_mode",
            "message": f"Invalid mode '{mode}'. Must be one of: unstaged, staged, head"
        }, indent=2)

    args = []
    if mode == "unstaged":
        args = ["diff", path]
    elif mode == "staged":
        args = ["diff", "--cached", path]
    elif mode == "head":
        args = ["diff", "HEAD", "--", path]

    result = _run_git_command(args)
    
    # Handle case where file does not exist
    if not result["success"] and "not in the working tree" in result["stderr"]:
        return json.dumps({
            "success": False,
            "error": "file_not_found",
            "message": f"The file '{path}' was not found in the git working tree.",
            "stderr": result["stderr"].strip()
        }, indent=2)

    # Handle case where there are no differences
    if result["success"] and not result["stdout"].strip():
        return json.dumps({
            "success": True,
            "stdout": "",
            "message": "No differences detected.",
            "command": result["command"]
        }, indent=2)

    return json.dumps(result, indent=2)

@mcp.tool()
def get_file_history(path: str, limit: int = 10) -> str:
    """
    Retrieves the commit history and associated diffs for a specified file.

    Args:
        path (str): The path to the file.
        limit (int, optional): The number of recent commits to show. Defaults to 10.

    Returns:
        str: A JSON-formatted string containing the git log with patch information or an error message.
    """
    args = ["log", "-p", "-n", str(limit), "--", path]

    result = _run_git_command(args)
    
    # Handle case where file does not exist
    if not result["success"] and "not in the working tree" in result["stderr"]:
        return json.dumps({
            "success": False,
            "error": "file_not_found",
            "message": f"The file '{path}' was not found in the git working tree.",
            "stderr": result["stderr"].strip()
        }, indent=2)

    return json.dumps(result, indent=2)

@mcp.tool()
def is_git_repository(path: Optional[str] = None) -> str:
    """
    Checks if the current working directory or a specified directory is a Git repository.

    Args:
        path (str, optional): The directory to check. Defaults to the current working directory.

    Returns:
        str: A JSON-formatted string indicating if it is a git repository.
    """
    target_path = path if path else "."
    
    try:
        result = _run_git_command(["rev-parse", "--is-inside-work-tree"], cwd=target_path)
        
        if result["success"] and "true" in result["stdout"].lower():
            return json.dumps({
                "success": True,
                "is_repository": True,
                "path": str(Path(target_path).resolve())
            }, indent=2)
        
        # If it's not a git repo, git returns non-zero and an error message in stderr.
        if not result["success"] and ("not a git repository" in result["stderr"].lower() or "not in a git repository" in result["stderr"].lower()):
            return json.dumps({
                "success": True,
                "is_repository": False,
                "path": str(Path(target_path).resolve())
            }, indent=2)
            
        # Otherwise, it's a real error (e.g. directory not found, permission denied, etc.)
        return json.dumps(result, indent=2)
            
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "wrapper_failed",
            "message": str(e),
            "traceback": traceback.format_exc()
        }, indent=2)

if __name__ == "__main__":
    mcp.run()
