# --- git-diff-mcp.py ---
import json
import subprocess
import traceback
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    ALLOWED_DIR,
    CONFIG_PATH,
    FORBIDDEN_PATHS,
    MAX_OUTPUT_CHARS,
    PROTECTED_FILE_NAMES,
    access_denied_message,
    display_path,
    get_repo_root,
    is_path_allowed,
    prepare_tools,
    resolve_path
)

# --- Constants & Config ---
mcp = MCPServer("Git-Diff-Server")

DIFF_MODES = {
    "unstaged": ["diff"],
    "staged": ["diff", "--cached"],
    "head": ["diff", "HEAD"]
}
MAX_LISTED_FILES = 200  # Changed and untracked files listed at most by get_diff
JSON_ESCAPE_RATIO = 0.9  # Share of the budget used for text inside JSON, as escaping line breaks and quotes adds characters

# --- Internal Helpers ---

def _access_denied(path: Path, label: str = "Path") -> str:
    """
    Returns the JSON access_denied response, stating why access to the given path was denied.
    """
    return json.dumps({"success": False, "error": "access_denied", "message": access_denied_message(path, label)}, indent=2, ensure_ascii=False)

def _run_git_command(args: list[str], cwd: str | Path | None = None) -> dict[str, Any]:
    """
    Executes a git command in cwd and returns the success status, stdout, command used and return code,
    and stderr if the command failed: on success it only holds warnings such as line ending notices,
    which a model might try to act on. Non-ASCII paths are shown as-is instead of escaped.
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

        response = {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "command": " ".join(cmd),
            "returncode": result.returncode
        }
        if result.returncode != 0:
            response["stderr"] = result.stderr
        return response

    except Exception as e:
        return {
            "success": False,
            "error": "execution_error",
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
    # Protected files are excluded by name at any depth, in any letter case, and the tools config file by its path
    for name in sorted(PROTECTED_FILE_NAMES):
        specs.append(f":(top,exclude,glob,icase)**/{name}")
    if CONFIG_PATH.is_relative_to(repo_root):
        specs.append(f":(top,exclude){CONFIG_PATH.relative_to(repo_root).as_posix()}")
    return specs

def _prepare(path: str) -> tuple[Path, Path, Path | None, str | None]:
    """
    Resolves the path (defaulting to ALLOWED_DIR) and finds its working directory and repository root.
    Returns (path, working directory, repository root, error response); the error response is None on success.
    """
    p = resolve_path(path) if path else ALLOWED_DIR
    if not is_path_allowed(p):
        return p, p, None, _access_denied(p)

    cwd = _existing_dir(p)
    repo_root = get_repo_root(cwd)
    if repo_root is None:
        return p, cwd, None, json.dumps({
            "success": False,
            "error": "not_a_repository",
            "message": f"{display_path(p, ALLOWED_DIR)} is not inside a git repository."
        }, indent=2, ensure_ascii=False)
    return p, cwd, repo_root, None

def _limit_text(text: str, limit: int, instruction: str) -> tuple[str, str | None]:
    """
    Shortens text longer than limit at a line break, keeping its start.
    Returns the text and an 'Output limited' note with the instruction, or None if the text fits.
    """
    if len(text) <= limit:
        return text, None
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > 0 else limit
    note = f"Output limited: only the first {cut} of {len(text)} characters are shown. {instruction}"
    return text[:cut] + "\n... (cut short)", note

def _limit_stdout(result: dict[str, Any], instruction: str) -> dict[str, Any]:
    """
    Limits the stdout of a git command result to MAX_OUTPUT_CHARS, adding an 'output_limited' note if needed.
    """
    stdout, note = _limit_text(result.get("stdout", ""), int((MAX_OUTPUT_CHARS - min(2000, MAX_OUTPUT_CHARS // 4)) * JSON_ESCAPE_RATIO), instruction)
    if note:
        result["stdout"] = stdout
        result["output_limited"] = note
    return result

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
    }, indent=2, ensure_ascii=False)

def _file_diff(path: str, p: Path, cwd: Path, repo_root: Path, mode: str) -> str:
    """
    Returns the JSON response with the diff of one file, with the same fields as the diff of all changes.
    """
    result = _run_git_command(DIFF_MODES[mode] + ["--"] + _pathspecs(repo_root, p), cwd=cwd)
    if not result["success"]:
        return json.dumps(result, indent=2, ensure_ascii=False)

    if not result["stdout"].strip() and not p.exists():
        return json.dumps({
            "success": False,
            "error": "file_not_found",
            "message": f"The file '{path}' was not found in the working tree and has no {mode} changes.",
            "command": result["command"]
        }, indent=2, ensure_ascii=False)

    message = ""
    if not result["stdout"].strip():
        # A new file that git does not track yet has no diff, which must not look like an unchanged file
        tracked = _run_git_command(["ls-files", "--error-unmatch", "--", p.relative_to(repo_root).as_posix()], cwd=repo_root)
        message = ("No differences detected." if tracked["success"] else
                   "The file is new and not tracked by git, so it has no diff. Read it with read_file_with_metadata.")

    result = _limit_stdout(result, "The diff is too long to show completely: read the current version of the file with read_file_with_metadata instead.")
    return json.dumps({
        "success": True,
        "mode": mode,
        "path": display_path(p, ALLOWED_DIR),
        "output_limited": result.get("output_limited"),
        "diff": result["stdout"],
        "message": message,
        "command": result["command"]
    }, indent=2, ensure_ascii=False)

def _all_changes_diff(p: Path, repo_root: Path, mode: str) -> str:
    """
    Returns the JSON response with the changed files (M = modified, A = added, D = deleted, R = renamed),
    the new untracked files, which are not in the diff, and the diff of all changed files within p.
    """
    specs = _pathspecs(repo_root, p)
    name_status = _run_git_command(DIFF_MODES[mode] + ["--name-status", "--"] + specs, cwd=repo_root)
    if not name_status["success"]:
        return json.dumps(name_status, indent=2, ensure_ascii=False)

    diff = _run_git_command(DIFF_MODES[mode] + ["--"] + specs, cwd=repo_root)
    if not diff["success"]:
        return json.dumps(diff, indent=2, ensure_ascii=False)

    untracked_files = []
    if mode != "staged":
        untracked = _run_git_command(["ls-files", "--others", "--exclude-standard", "--full-name", "--"] + specs, cwd=repo_root)
        if untracked["success"]:
            untracked_files = untracked["stdout"].splitlines()

    changed_files = name_status["stdout"].splitlines()
    notes = []
    if len(changed_files) > MAX_LISTED_FILES or len(untracked_files) > MAX_LISTED_FILES:
        notes.append(f"Output limited: {len(changed_files)} changed and {len(untracked_files)} untracked files were found, but at most {MAX_LISTED_FILES} of each are listed."
                     f" Limit the diff to one folder with path to see the rest.")
    listed_changed, listed_untracked = changed_files[:MAX_LISTED_FILES], untracked_files[:MAX_LISTED_FILES]

    listed_chars = sum(len(f) + 8 for f in listed_changed + listed_untracked)
    diff_text, diff_note = _limit_text(diff["stdout"], max(1000, int((MAX_OUTPUT_CHARS - min(3000, MAX_OUTPUT_CHARS // 4) - listed_chars) * JSON_ESCAPE_RATIO)),
                                       "All changed files are listed in changed_files: view the rest one file at a time with get_diff(mode, path='<file>'), or limit the diff to one folder with path.")
    if diff_note:
        notes.append(diff_note)

    return json.dumps({
        "success": True,
        "mode": mode,
        "repository_root": repo_root.as_posix(),
        "output_limited": " ".join(notes) or None,
        "changed_files": listed_changed,
        "untracked_files": listed_untracked,
        "diff": diff_text,
        "message": "No differences detected." if not changed_files and not untracked_files else "",
        "command": diff["command"]
    }, indent=2, ensure_ascii=False)

# --- Public MCP Tools ---

@mcp.tool()
def get_diff(mode: str, path: str = "") -> str:
    """
    Shows the git changes of one file, or of all changed files with lists of the changed and
    new untracked files (which have no diff). Use it to review changes.

    Args:
        mode: 'unstaged' (changes not staged yet), 'staged' (changes staged for commit) or
            'head' (all changes since the last commit).
        path: A file, or a folder to limit the changes to. Defaults to the whole repository.

    Returns:
        JSON with the diff, or an error.
    """
    error = _invalid_mode(mode)
    if error:
        return error

    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error
        # A path that is not a folder is a file, also if it was deleted
        if path and not p.is_dir():
            return _file_diff(path, p, cwd, repo_root, mode)
        return _all_changes_diff(p, repo_root, mode)
    except Exception as e:
        return json.dumps({"success": False, "error": "diff_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)

@mcp.tool()
def get_file_history(path: str, limit: int = 10) -> str:
    """
    Shows the recent commits of a file or folder with their changes.

    Args:
        path: The file or folder.
        limit: The number of commits. Defaults to 10.
    """
    try:
        limit = max(1, int(limit))
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        result = _run_git_command(["log", "-p", "-n", str(limit), "--"] + _pathspecs(repo_root, p), cwd=cwd)
        if not result["success"]:
            return json.dumps(result, indent=2, ensure_ascii=False)

        if not result["stdout"].strip():
            if not p.exists():
                return json.dumps({
                    "success": False,
                    "error": "file_not_found",
                    "message": f"The file '{path}' was not found in the working tree or its history.",
                    "command": result["command"]
                }, indent=2, ensure_ascii=False)
            return json.dumps({
                "success": True,
                "stdout": "",
                "message": "No commit history found. The file may not be committed yet.",
                "command": result["command"]
            }, indent=2, ensure_ascii=False)

        smaller = f"Show fewer commits with a smaller limit, e.g. limit={max(1, limit // 2)}." if limit > 1 else "The latest change alone is too long to show completely."
        result = _limit_stdout(result, smaller)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "history_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)

@mcp.tool()
def get_git_status(path: str = "") -> str:
    """
    Returns 'git status'. The error 'not_a_repository' means the folder is not in a git repository.

    Args:
        path: A folder. Defaults to the whole project.
    """
    try:
        p, cwd, repo_root, error = _prepare(path)
        if error:
            return error

        result = _run_git_command(["status", "--"] + _pathspecs(repo_root, p), cwd=cwd)
        result = _limit_stdout(result, "Limit the status to one folder with path, or list the changed files with get_diff.")
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "status_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)

prepare_tools(mcp)

if __name__ == "__main__":
    mcp.run()
