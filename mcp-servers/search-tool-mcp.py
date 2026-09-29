# --- search-tool-mcp.py ---
import fnmatch
import json
import os
import re
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Search-Tool-Server")

INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd())).resolve()
GITIGNORE_PATH = os.getenv("GITIGNORE_PATH")
PROTECTED_FILE_NAMES = {".mcp.json"}  # MCP server configuration, never accessible regardless of FORBIDDEN_PATHS
IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit
MAX_RESULTS = 100  # Cap output to protect the context window
MAX_LINE_CHARS = 300  # Longer lines (e.g. minified code) are shortened around the match
MAX_CONTEXT_LINES = 5
BINARY_CHECK_BYTES = 8192  # Files with a null byte in this many first bytes are treated as binary

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

def _display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

def _get_gitignore_path(input_dir: Path) -> Path:
    """
    Returns GITIGNORE_PATH if set, otherwise input_dir/.gitignore.
    """
    return Path(GITIGNORE_PATH).resolve() if GITIGNORE_PATH else input_dir / ".gitignore"

def _load_gitignore_patterns(input_dir: Path) -> list[str]:
    """
    Loads the patterns from the .gitignore file returned by _get_gitignore_path.
    """
    gitignore_path = _get_gitignore_path(input_dir)

    if not gitignore_path.exists():
        return []

    patterns = []
    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except Exception:
        pass
    return patterns

def _is_ignored(path: Path, input_dir: Path, ignored_dirs: set[str], gitignore_patterns: list[str]) -> bool:
    """
    Checks if a path is excluded by FORBIDDEN_PATHS, ignored_dirs or .gitignore patterns.
    """
    if _is_forbidden(path):
        return True

    if any(ignored in path.parts for ignored in ignored_dirs):
        return True

    if not gitignore_patterns:
        return False

    try:
        rel_path = path.relative_to(input_dir).as_posix()
    except ValueError:
        return False

    for pattern in gitignore_patterns:
        if fnmatch.fnmatch(rel_path, pattern) or \
           fnmatch.fnmatch(f"{rel_path}/", pattern) or \
           any(fnmatch.fnmatch(part, pattern) for part in path.relative_to(input_dir).parts):
            return True

    return False

def _walk(input_dir: Path, gitignore_patterns: list[str], start_dir: Path, max_depth: int | None = None):
    """
    Yields (path, is_dir) for the folders and files within start_dir that are not ignored, in sorted order.
    Ignored folders are skipped without being entered, which keeps large folders such as node_modules fast.
    Folders deeper than max_depth levels below start_dir are not entered.
    """
    for dirpath, dirnames, filenames in os.walk(start_dir):
        current_dir = Path(dirpath)
        depth = len(current_dir.relative_to(start_dir).parts)
        dirnames[:] = sorted(
            (d for d in dirnames if not _is_ignored(current_dir / d, input_dir, IGNORED_DIRS, gitignore_patterns)),
            key=str.lower
        )
        for name in dirnames:
            yield current_dir / name, True
        if max_depth is not None and depth + 1 >= max_depth:
            dirnames[:] = []
        for name in sorted(filenames, key=str.lower):
            path = current_dir / name
            if not _is_ignored(path, input_dir, IGNORED_DIRS, gitignore_patterns):
                yield path, False

def _resolve_scope(path: str | None, gitignore_patterns: list[str]) -> tuple[Path, str | None]:
    """
    Resolves the folder or file a search is limited to. Relative paths are resolved against INPUT_DIR.
    Returns the path and an error message, which is None if the path can be searched.
    """
    if not path:
        return INPUT_DIR, None
    p = Path(path)
    if not p.is_absolute():
        p = INPUT_DIR / p
    p = p.resolve()
    if not p.is_relative_to(INPUT_DIR):
        return p, f"Error: Path '{path}' is not within the input directory {INPUT_DIR.as_posix()}."
    if not p.exists():
        return p, f"Error: Path '{path}' does not exist in the input directory {INPUT_DIR.as_posix()}."
    if p != INPUT_DIR and _is_ignored(p, INPUT_DIR, IGNORED_DIRS, gitignore_patterns):
        return p, f"Error: Path '{path}' is ignored or forbidden. Use get_search_config to see why."
    return p, None

def _glob_to_regex(pattern: str) -> re.Pattern:
    """
    Converts a glob pattern into a regex that matches a whole relative path.
    '*' and '?' do not match '/', while '**' matches any number of folders.
    Matching is case-insensitive on Windows, like the file system.
    """
    i, n, regex = 0, len(pattern), ""
    while i < n:
        if pattern.startswith("**/", i):
            regex += "(?:[^/]+/)*"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 1:]:
            end = pattern.index("]", i + 1)
            regex += "[" + pattern[i + 1:end].replace("!", "^", 1) + "]"
            i = end + 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.compile(f"{regex}$", re.IGNORECASE if os.name == "nt" else 0)

def _is_binary(path: Path) -> bool:
    """
    Checks if a file looks binary, i.e. has a null byte near its start.
    """
    with open(path, "rb") as f:
        return b"\0" in f.read(BINARY_CHECK_BYTES)

def _shorten_line(line: str, match: re.Match | None = None) -> str:
    """
    Shortens a line longer than MAX_LINE_CHARS, keeping the part around the match.
    """
    if len(line) <= MAX_LINE_CHARS:
        return line
    center = match.start() if match else 0
    start = max(0, min(center - MAX_LINE_CHARS // 3, len(line) - MAX_LINE_CHARS))
    end = start + MAX_LINE_CHARS
    return ("..." if start > 0 else "") + line[start:end] + ("..." if end < len(line) else "")

def _search_file(path: Path, pattern: re.Pattern) -> tuple[list[str], dict[int, re.Match]]:
    """
    Returns the lines of a text file and its matches as {line index: match}.
    Binary, too large and unreadable files return no matches.
    """
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES or _is_binary(path):
            return [], {}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except Exception:
        return [], {}
    matches = {}
    for i, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            matches[i] = match
    return lines, matches

def _format_file_matches(rel_path: str, lines: list[str], matches: dict[int, re.Match], context_lines: int, limit: int) -> list[str]:
    """
    Formats up to limit matches of one file, grep-style: 'path:line: text' for matching lines and
    'path-line- text' for context lines, with '--' between separate groups of lines.
    """
    shown = sorted(matches)[:limit]
    if not context_lines:
        return [f"{rel_path}:{i + 1}: {_shorten_line(lines[i].strip(), matches[i])}" for i in shown]

    # Merge overlapping context ranges into groups
    groups = []
    for i in shown:
        start, end = max(0, i - context_lines), min(len(lines) - 1, i + context_lines)
        if groups and start <= groups[-1][1] + 1:
            groups[-1][1] = max(groups[-1][1], end)
        else:
            groups.append([start, end])

    out = []
    for start, end in groups:
        if out:
            out.append("--")
        for i in range(start, end + 1):
            if i in matches and i in shown:
                out.append(f"{rel_path}:{i + 1}: {_shorten_line(lines[i], matches[i])}")
            else:
                out.append(f"{rel_path}-{i + 1}- {_shorten_line(lines[i])}")
    return out

def _format_list(items: list[str], location: str) -> str:
    """
    Formats a list of matching paths, capped at MAX_RESULTS.
    """
    output = f"Found {len(items)} matches in {location}:\n\n"
    output += "\n".join(items[:MAX_RESULTS])
    if len(items) > MAX_RESULTS:
        output += f"\n... (and {len(items) - MAX_RESULTS} more matches)"
    return output

# --- Public MCP Tools ---

@mcp.tool()
def search_text_in_files(query: str, case_sensitive: bool = False, path: str | None = None, file_pattern: str | None = None, context_lines: int = 0) -> str:
    """
    Search for text or regex patterns from code files in the codebase.
    This tool performs a recursive search through all text files in the project, skipping
    binary files, ignored folders (such as node_modules) and files matched by .gitignore.
    Very long lines are shortened around the match.

    Args:
        query (str): The text or regex pattern to search for. Escape regex special characters
            such as '(' or '.' to search for them literally.
        case_sensitive (bool): Whether the search should be case-sensitive. The default is False
        path (str, optional): A folder or file to limit the search to, relative to the input
            directory (e.g., 'src/components'). Defaults to the whole input directory.
        file_pattern (str, optional): A glob pattern the file path must end with (e.g., '*.py',
            '*.test.ts' or 'tests/*.py'). Defaults to all files.
        context_lines (int): The number of lines to show before and after each match, up to 5.
            Defaults to 0.

    Returns:
        str: The matching lines as 'path:line: text' (context lines as 'path-line- text'),
            grouped by file, with the total number of matches and files, or an error message.
    """
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            return f"Error: Invalid regular expression '{query}': {e}. Escape special characters to search for them literally."

        gitignore_patterns = _load_gitignore_patterns(INPUT_DIR)
        scope, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error

        context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
        # Anchored at a '/' so that 'tests/*.py' matches 'src/tests/a.py' but not 'src/mytests/a.py'
        file_regex = _glob_to_regex("/" + file_pattern.replace("\\", "/").lstrip("/")) if file_pattern else None
        files = [scope] if scope.is_file() else [p for p, is_dir in _walk(INPUT_DIR, gitignore_patterns, scope) if not is_dir]

        total_matches, matched_files, remaining = 0, 0, MAX_RESULTS
        output = []
        for file in files:
            rel_path = file.relative_to(INPUT_DIR).as_posix()
            if file_regex and not file_regex.search("/" + rel_path):
                continue
            lines, matches = _search_file(file, pattern)
            if not matches:
                continue
            total_matches += len(matches)
            matched_files += 1
            if remaining > 0:
                output.extend(_format_file_matches(rel_path, lines, matches, context_lines, remaining))
                output.append("")
                remaining -= min(len(matches), remaining)

        location = _display_path(scope, INPUT_DIR) if scope != INPUT_DIR else INPUT_DIR.as_posix()
        if not total_matches:
            filter_note = f" (files matching '{file_pattern}')" if file_pattern else ""
            return f"No matches found for '{query}' in {location}{filter_note}"

        result = f"Found {total_matches} matches in {matched_files} files in {location}:\n\n" + "\n".join(output).rstrip()
        if total_matches > MAX_RESULTS:
            result += f"\n\n... (and {total_matches - MAX_RESULTS} more matches. Narrow the search with path or file_pattern.)"
        return result
    except Exception as e:
        return f"Error: Searching text in files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def search_files_by_pattern(pattern: str, recursive: bool = False) -> str:
    """
    Searches for files and directories that match a glob-style pattern.
    Ignored folders (such as node_modules) and files matched by .gitignore are skipped.
    Directories are listed with a trailing '/'.

    Args:
        pattern (str): A glob pattern relative to the input directory (e.g., `*.ts`, `src/utils/*.py`,
            `README*` or `src/**/*.test.ts`). `**` matches any number of folders.
        recursive (bool): If True, the pattern is matched in all subdirectories too (equivalent to rglob).
            Defaults to False.

    Returns:
        str: A string list of matching file paths or an error message.
    """
    try:
        glob = pattern.strip().replace("\\", "/").removeprefix("./")
        if not glob:
            return "Error: The pattern must not be empty."
        if recursive:
            glob = "**/" + glob
        regex = _glob_to_regex(glob)
        # Without '**', only paths with as many parts as the pattern can match, so deeper folders are not entered
        max_depth = None if "**" in glob else len(glob.split("/"))

        gitignore_patterns = _load_gitignore_patterns(INPUT_DIR)
        matches = []
        for path, is_dir in _walk(INPUT_DIR, gitignore_patterns, INPUT_DIR, max_depth):
            rel_path = path.relative_to(INPUT_DIR).as_posix()
            if regex.match(rel_path):
                matches.append(rel_path + ("/" if is_dir else ""))

        if not matches:
            msg = f"No files found matching pattern: '{pattern}'"
            if not recursive:
                msg += ". Try setting recursive=True to search subdirectories."
            return msg

        return _format_list(matches, INPUT_DIR.as_posix())
    except Exception as e:
        return f"Error: Searching files by pattern failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def get_search_config() -> str:
    """
    Returns the configuration that decides which files the search tools can see:
    the input directory, ignored directories, forbidden paths, .gitignore patterns and search limits.
    Use this tool to find out why a file or match is missing from search_text_in_files
    or search_files_by_pattern.

    Returns:
        str: A JSON-formatted string containing the configuration or an error message.
    """
    try:
        gitignore_path = _get_gitignore_path(INPUT_DIR)
        return json.dumps({
            "success": True,
            "input_dir": INPUT_DIR.as_posix(),
            "input_dir_exists": INPUT_DIR.exists(),
            "ignored_dirs": sorted(IGNORED_DIRS),
            "forbidden_paths": [_display_path(p, INPUT_DIR) for p in FORBIDDEN_PATHS],
            "protected_file_names": sorted(PROTECTED_FILE_NAMES),
            "gitignore": {
                "applied": True,
                "path": gitignore_path.as_posix(),
                "found": gitignore_path.exists(),
                "patterns": _load_gitignore_patterns(INPUT_DIR)
            },
            "max_file_size_bytes": MAX_FILE_SIZE_BYTES,
            "max_results_shown": MAX_RESULTS,
            "max_line_chars": MAX_LINE_CHARS,
            "max_context_lines": MAX_CONTEXT_LINES,
            "notes": [
                "Files named in protected_file_names (the MCP server configuration) are always denied and hidden at any depth, even if forbidden_paths is empty.",
                "Folders named in ignored_dirs are skipped at any depth within input_dir.",
                "Forbidden paths and everything inside forbidden folders are skipped.",
                "Files and folders matching the .gitignore patterns are skipped.",
                "search_text_in_files skips binary files and files larger than max_file_size_bytes.",
                "Lines longer than max_line_chars are shortened around the match.",
                "Only the first max_results_shown matches are listed; the total count is always reported."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

if __name__ == "__main__":
    mcp.run()
