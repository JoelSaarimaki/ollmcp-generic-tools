# --- git-diff-mcp.py ---
import json
import os
import subprocess
import traceback
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Git-Diff-Server")

ALLOWED_DIR = Path(os.getenv("ALLOWED_DIR", os.getcwd())).resolve()
PROTECTED_FILE_NAMES = {".mcp.json"}  # MCP server configuration, never accessible regardless of FORBIDDEN_PATHS
DIFF_MODES = {
    "unstaged": ["diff"],
    "staged": ["diff", "--cached"],
    "head": ["diff", "HEAD"]
}
MAX_DIFF_CHARS = 50000  # Cap output to protect the context window

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

FORBIDDEN_PATHS = _load_forbidden_paths(ALLOWED_DIR)

def _is_protected(path: Path) -> bool:
    """
    Checks if the given path is a protected file (see PROTECTED_FILE_NAMES), also through a symlink
    or a Windows alias of the name, such as a trailing dot or a '::$DATA' stream suffix.
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
    return False

def _is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a protected file, a forbidden file or is located within a forbidden folder.
    """
    if _is_protected(path):
        return True
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(resolved.is_relative_to(forbidden) for forbidden in FORBIDDEN_PATHS)

def _resolve_path(path: str) -> Path:
    """
    Resolves a path given to a tool. Relative paths are resolved against ALLOWED_DIR, as in all servers.
    """
    p = Path(path)
    return (p if p.is_absolute() else ALLOWED_DIR / p).resolve()

def _is_path_allowed(path: Path) -> bool:
    """
    Checks if the given path is within ALLOWED_DIR and not forbidden.
    """
    try:
        return path.resolve().is_relative_to(ALLOWED_DIR) and not _is_forbidden(path)
    except (ValueError, OSError):
        return False

def _access_denied(path: Path, label: str = "Path") -> str:
    """
    Returns the JSON access_denied response, stating why access to the given path was denied.
    """
    if _is_protected(path):
        message = f"{label} is protected: '{path.name}' files contain the MCP server configuration and cannot be accessed."
    elif _is_forbidden(path):
        message = f"{label} is forbidden: {path}"
    else:
        message = f"{label} is not within the allowed directory: {ALLOWED_DIR}"
    return json.dumps({"success": False, "error": "access_denied", "message": message}, indent=2)

def _display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

def _run_git_command(args: list[str], cwd: str | Path | None = None) -> dict[str, Any]:
    """
    Executes a git command in cwd and returns the success status, stdout, stderr,
    command used and return code. Non-ASCII paths are shown as-is instead of escaped.
    """
    cmd = ["git", "-c", "core.quotePath=false"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
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

def _existing_dir(path: Path) -> Path:
    """
    Returns the path itself if it is a directory, otherwise its closest existing parent directory.
    Used as the working directory for git, so that deleted files can still be inspected.
    """
    current = path if path.is_dir() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current

def _get_repo_root(directory: Path) -> Path | None:
    """
    Returns the root of the git repository containing the directory, or None if it is not in a repository.
    """
    result = _run_git_command(["rev-parse", "--show-toplevel"], cwd=directory)
    if not result["success"]:
        return None
    return Path(result["stdout"].strip()).resolve()

def _pathspecs(repo_root: Path, target: Path) -> list[str]:
    """
    Returns git pathspecs that limit a command to the target within the repository,
    excluding all forbidden and protected paths. If the target contains the whole repository, the whole
    repository is included.
    """
    scope = target if target.is_relative_to(repo_root) else repo_root
    rel_scope = scope.relative_to(repo_root).as_posix()
    specs = [":(top)" if rel_scope == "." else f":(top){rel_scope}"]

    for forbidden in FORBIDDEN_PATHS:
        if forbidden.is_relative_to(repo_root) and forbidden != repo_root:
            specs.append(f":(top,exclude){forbidden.relative_to(repo_root).as_posix()}")
    # Protected files are excluded by name at any depth, in any letter case
    for name in sorted(PROTECTED_FILE_NAMES):
        specs.append(f":(top,exclude,glob,icase)**/{name}")
    return specs

def _prepare(path: str | None) -> tuple[Path, Path, Path | None, str | None]:
    """
    Resolves the path (defaulting to ALLOWED_DIR) and finds its working directory and repository root.
    Returns (path, working directory, repository root, error response); the error response is None on success.
    """
    p = _resolve_path(path) if path else ALLOWED_DIR
    if not _is_path_allowed(p):
        return p, p, None, _access_denied(p)

    cwd = _existing_dir(p)
    repo_root = _get_repo_root(cwd)
    if repo_root is None:
        return p, cwd, None, json.dumps({
            "success": False,
            "error": "not_a_repository",
            "message": f"{p} is not inside a git repository."
        }, indent=2)
    return p, cwd, repo_root, None

def _truncate_diff(text: str) -> tuple[str, bool]:
    """
    Shortens diff output longer than MAX_DIFF_CHARS, keeping its start.
    Returns the text and whether it was truncated.
    """
    if len(text) <= MAX_DIFF_CHARS:
        return text, False
    omitted = len(text) - MAX_DIFF_CHARS
    return f"{text[:MAX_DIFF_CHARS]}\n... ({omitted} characters truncated, use get_file_diff for individual files) ...", True

def _invalid_mode(mode: str) -> str | None:
    """
    Returns the JSON invalid_mode response if the diff mode is not supported, otherwise None.
    """
    if mode in DIFF_MODES:
        return None
    return json.dumps({
        "success": False,
        "error": "invalid_mode",
        "message": f"Invalid mode '{mode}'. Must be one of: {', '.join(DIFF_MODES)}"
    }, indent=2)

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
    error = _invalid_mode(mode)
    if error:
        return error

    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        result = _run_git_command(DIFF_MODES[mode] + ["--"] + _pathspecs(repo_root, p), cwd=cwd)
        if not result["success"]:
            return json.dumps(result, indent=2)

        if not result["stdout"].strip():
            if not p.exists():
                return json.dumps({
                    "success": False,
                    "error": "file_not_found",
                    "message": f"The file '{path}' was not found in the working tree and has no {mode} changes.",
                    "command": result["command"]
                }, indent=2)
            return json.dumps({
                "success": True,
                "stdout": "",
                "message": "No differences detected.",
                "command": result["command"]
            }, indent=2)

        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "diff_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def get_all_changes_diff(mode: str, path: str | None = None) -> str:
    """
    Retrieves the differences for all changed files in the repository at once.
    Use this to review all changes before committing, instead of calling get_file_diff for each file.

    Args:
        mode (str): The type of diff to retrieve. Options:
            - 'unstaged': Changes in the working directory that are not yet staged (i.e., git diff).
            - 'staged': Changes that are staged for commit but not yet committed (i.e., git diff --cached).
            - 'head': All staged and unstaged changes compared to the last commit (i.e., git diff HEAD).
        path (str, optional): A directory to limit the diff to. Defaults to the allowed directory.

    Returns:
        str: A JSON-formatted string containing the changed files with their status (M = modified,
            A = added, D = deleted, R = renamed), new untracked files (not included in the diff),
            and the diff output, or an error message. Long diffs are truncated.
    """
    error = _invalid_mode(mode)
    if error:
        return error

    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        specs = _pathspecs(repo_root, p)
        name_status = _run_git_command(DIFF_MODES[mode] + ["--name-status", "--"] + specs, cwd=repo_root)
        if not name_status["success"]:
            return json.dumps(name_status, indent=2)

        diff = _run_git_command(DIFF_MODES[mode] + ["--"] + specs, cwd=repo_root)
        if not diff["success"]:
            return json.dumps(diff, indent=2)

        untracked_files = []
        if mode != "staged":
            untracked = _run_git_command(["ls-files", "--others", "--exclude-standard", "--full-name", "--"] + specs, cwd=repo_root)
            if untracked["success"]:
                untracked_files = untracked["stdout"].splitlines()

        diff_text, truncated = _truncate_diff(diff["stdout"])
        changed_files = name_status["stdout"].splitlines()
        return json.dumps({
            "success": True,
            "mode": mode,
            "repository_root": repo_root.as_posix(),
            "changed_files": changed_files,
            "untracked_files": untracked_files,
            "diff": diff_text,
            "truncated": truncated,
            "message": "No differences detected." if not changed_files and not untracked_files else "",
            "command": diff["command"]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "diff_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

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
    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        result = _run_git_command(["log", "-p", "-n", str(limit), "--"] + _pathspecs(repo_root, p), cwd=cwd)
        if not result["success"]:
            return json.dumps(result, indent=2)

        if not result["stdout"].strip():
            if not p.exists():
                return json.dumps({
                    "success": False,
                    "error": "file_not_found",
                    "message": f"The file '{path}' was not found in the working tree or its history.",
                    "command": result["command"]
                }, indent=2)
            return json.dumps({
                "success": True,
                "stdout": "",
                "message": "No commit history found. The file may not be committed yet.",
                "command": result["command"]
            }, indent=2)

        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "history_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def get_git_status(path: str | None = None) -> str:
    """
    Returns the results of 'git status'.

    Args:
        path (str, optional): The directory to run the command in. Defaults to the allowed directory.

    Returns:
        str: A JSON-formatted string containing the git status output or an error message.
    """
    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        result = _run_git_command(["status", "--"] + _pathspecs(repo_root, p), cwd=cwd)
        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "status_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def is_git_repository(path: str | None = None) -> str:
    """
    Checks if the allowed directory or a specified directory is a Git repository.

    Args:
        path (str, optional): The directory to check. Defaults to the allowed directory.

    Returns:
        str: A JSON-formatted string indicating if it is a git repository.
    """
    try:
        p = _resolve_path(path) if path else ALLOWED_DIR
        if not _is_path_allowed(p):
            return _access_denied(p)

        result = _run_git_command(["rev-parse", "--is-inside-work-tree"], cwd=p)

        if result["success"] and "true" in result["stdout"].lower():
            return json.dumps({
                "success": True,
                "is_repository": True,
                "path": str(p)
            }, indent=2)

        # If it's not a git repo, git returns non-zero and an error message in stderr.
        if not result["success"] and ("not a git repository" in result["stderr"].lower() or "not in a git repository" in result["stderr"].lower()):
            return json.dumps({
                "success": True,
                "is_repository": False,
                "path": str(p)
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

@mcp.tool()
def get_git_config() -> str:
    """
    Returns the configuration that decides which files the git tools can see:
    the allowed directory, which relative paths are resolved against, its repository root
    and forbidden paths.
    Use this tool to find out why a path is denied or missing from the git tools' output.

    Returns:
        str: A JSON-formatted string containing the configuration or an error message.
    """
    try:
        repo_root = _get_repo_root(ALLOWED_DIR) if ALLOWED_DIR.exists() else None
        return json.dumps({
            "success": True,
            "allowed_dir": ALLOWED_DIR.as_posix(),
            "allowed_dir_exists": ALLOWED_DIR.exists(),
            "repository_root": repo_root.as_posix() if repo_root else None,
            "ignored_dirs": [],
            "forbidden_paths": [_display_path(p, ALLOWED_DIR) for p in FORBIDDEN_PATHS],
            "protected_file_names": sorted(PROTECTED_FILE_NAMES),
            "gitignore": {
                "applied": True
            },
            "notes": [
                "Files named in protected_file_names (the MCP server configuration) are always denied and hidden at any depth, even if forbidden_paths is empty.",
                "Only paths within allowed_dir can be inspected.",
                "Relative paths given to the tools are resolved against allowed_dir.",
                "Forbidden paths and everything inside forbidden folders are denied and left out of status and diff output.",
                "Git's own .gitignore rules decide which untracked files are listed.",
                "If allowed_dir is a subfolder of the repository, changes outside it are left out."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

if __name__ == "__main__":
    mcp.run()
