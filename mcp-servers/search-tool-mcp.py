# --- search-tool-mcp.py ---
import os
import re
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    ALLOWED_DIR,
    IGNORED_DIRS,
    MAX_OUTPUT_CHARS,
    access_denied_message,
    prepare_tools,
    display_path,
    is_ignored,
    is_path_allowed,
    load_gitignore_patterns,
    resolve_path,
    walk
)

# --- Constants & Config ---
mcp = MCPServer("Search-Tool-Server")

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit
MAX_RESULTS = 100  # Matches shown at most
MAX_LINE_CHARS = 300  # Longer lines (e.g. minified code) are shortened around the match
MAX_CONTEXT_LINES = 5
BINARY_CHECK_BYTES = 8192  # Files with a null byte in this many first bytes are treated as binary

# --- Internal Helpers ---

def _resolve_scope(path: str, gitignore_patterns: list[str]) -> tuple[Path, str | None]:
    """
    Resolves the folder or file a search is limited to. Relative paths are resolved against ALLOWED_DIR.
    Returns the path and an error message, which is None if the path can be searched.
    """
    if not path:
        return ALLOWED_DIR, None
    p = resolve_path(path)
    if not is_path_allowed(p):
        return p, f"Error: {access_denied_message(p)}"
    if not p.exists():
        return p, f"Error: Path '{path}' does not exist in the allowed directory {ALLOWED_DIR.as_posix()}."
    if p != ALLOWED_DIR and is_ignored(p, ALLOWED_DIR, IGNORED_DIRS, gitignore_patterns):
        return p, f"Error: Path '{path}' is ignored: it is in an ignored folder or matched by .gitignore. Use get_config to see why."
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

def _format_file_matches(rel_path: str, lines: list[str], matches: dict[int, re.Match], context_lines: int, limit: int) -> list[tuple[str, bool]]:
    """
    Formats up to limit matches of one file, grep-style: 'path:line: text' for matching lines and
    'path-line- text' for context lines, with '--' between separate groups of lines.
    Returns (line, is_match) pairs, so that the caller can count the matches it shows.
    """
    shown = sorted(matches)[:limit]
    if not context_lines:
        return [(f"{rel_path}:{i + 1}: {_shorten_line(lines[i].strip(), matches[i])}", True) for i in shown]

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
            out.append(("--", False))
        for i in range(start, end + 1):
            if i in matches and i in shown:
                out.append((f"{rel_path}:{i + 1}: {_shorten_line(lines[i], matches[i])}", True))
            else:
                out.append((f"{rel_path}-{i + 1}- {_shorten_line(lines[i])}", False))
    return out

def _suggest_folder(scope: Path, file_matches: list[tuple[tuple[str, ...], int]]) -> tuple[str, int] | None:
    """
    Returns the subfolder of scope with the most matches and its match count, from (folder parts, match count)
    pairs of the matching files. While one subfolder holds all the matches, its subfolders are compared instead.
    Returns None if the matches are not in subfolders.
    """
    prefix = ()
    while True:
        counts, direct = {}, 0
        for folders, count in file_matches:
            if folders[:len(prefix)] != prefix:
                continue
            if len(folders) > len(prefix):
                counts[folders[len(prefix)]] = counts.get(folders[len(prefix)], 0) + count
            else:
                direct += count
        if len(counts) == 1 and not direct:
            prefix += (next(iter(counts)),)
            continue
        if counts:
            top = max(counts, key=counts.get)
            prefix, count = prefix + (top,), counts[top]
        else:
            count = direct
        break
    if not prefix:
        return None
    return scope.joinpath(*prefix).relative_to(ALLOWED_DIR).as_posix(), count

def _format_list(items: list[str], location: str) -> str:
    """
    Formats a list of matching paths, capped at MAX_RESULTS.
    """
    output = f"Found {len(items)} matches in {location}:\n\n"
    if len(items) > MAX_RESULTS:
        output += (f"Output limited: only the first {MAX_RESULTS} of {len(items)} matches are shown."
                   f" Use a more specific pattern to see the rest, e.g. 'src/**/*.py' instead of '*.py' with recursive=True.\n\n")
    return output + "\n".join(items[:MAX_RESULTS])

# --- Public MCP Tools ---

@mcp.tool()
def search_text_in_files(query: str, case_sensitive: bool = False, path: str = "", file_pattern: str = "", context_lines: int = 0) -> str:
    """
    Searches the project's text files for a regex, skipping ignored folders and .gitignored files.
    A query that is not a valid regex, such as 'foo(', is searched for as literal text.

    Args:
        query: The regex or text.
        case_sensitive: Match upper and lower case exactly. Defaults to False.
        path: A folder or file to search in. Defaults to the whole project.
        file_pattern: A glob the file path must match, e.g. '*.py' or 'tests/*.ts'.
        context_lines: Lines to show around each match, up to 5. Defaults to 0.

    Returns:
        Matches as 'path:line: text', context lines as 'path-line- text'.
    """
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        # Small models often search for code such as 'functionName(', which is not a valid regex
        literal_note = ""
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            pattern = re.compile(re.escape(query), flags)
            literal_note = f"Note: '{query}' is not a valid regular expression ({e}), so it was searched for as literal text.\n"

        gitignore_patterns = load_gitignore_patterns()
        scope, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error

        context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
        # Anchored at a '/' so that 'tests/*.py' matches 'src/tests/a.py' but not 'src/mytests/a.py'
        file_regex = _glob_to_regex("/" + file_pattern.replace("\\", "/").lstrip("/")) if file_pattern else None
        files = [scope] if scope.is_file() else [p for p, is_dir in walk(ALLOWED_DIR, gitignore_patterns, scope) if not is_dir]

        # Show matches until MAX_RESULTS matches or MAX_OUTPUT_CHARS characters, leaving room for the notes
        budget = MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4)
        total_matches, matched_files, shown_matches, used, full = 0, 0, 0, 0, False
        file_matches = []
        output = []
        for file in files:
            rel_path = file.relative_to(ALLOWED_DIR).as_posix()
            if file_regex and not file_regex.search("/" + rel_path):
                continue
            lines, matches = _search_file(file, pattern)
            if not matches:
                continue
            total_matches += len(matches)
            matched_files += 1
            folders = file.relative_to(scope).parts[:-1] if scope.is_dir() else ()
            file_matches.append((folders, len(matches)))

            if full or shown_matches >= MAX_RESULTS:
                continue
            for line, is_match in _format_file_matches(rel_path, lines, matches, context_lines, MAX_RESULTS - shown_matches):
                if used + len(line) + 1 > budget:
                    full = True
                    break
                output.append(line)
                used += len(line) + 1
                shown_matches += is_match
            output.append("")

        location = display_path(scope, ALLOWED_DIR) if scope != ALLOWED_DIR else ALLOWED_DIR.as_posix()
        if not total_matches:
            filter_note = f" (files matching '{file_pattern}')" if file_pattern else ""
            return f"{literal_note}No matches found for '{query}' in {location}{filter_note}"

        result = f"{literal_note}Found {total_matches} matches in {matched_files} files in {location}:\n\n"
        if shown_matches < total_matches:
            hints = []
            suggestion = _suggest_folder(scope, file_matches) if scope.is_dir() else None
            if suggestion:
                hints.append(f"path (e.g. path='{suggestion[0]}', which has {suggestion[1]} matches)")
            if not file_pattern:
                hints.append("file_pattern (e.g. file_pattern='*.py')")
            if context_lines:
                hints.append("fewer context_lines")
            hints.append("a more specific query")
            result += (f"Output limited: only {shown_matches} of {total_matches} matches are shown, as the output is limited to {MAX_RESULTS} matches or {MAX_OUTPUT_CHARS} characters."
                       f" Narrow the search with {', '.join(hints[:-1])} or {hints[-1]}.\n\n")
        return result + "\n".join(output).rstrip()
    except Exception as e:
        return f"Error: Searching text in files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def search_files_by_pattern(pattern: str, recursive: bool = False) -> str:
    """
    Finds files and folders by a glob pattern relative to the project, e.g. '*.ts', 'src/utils/*.py'
    or 'src/**/*.test.ts' ('**' matches any number of folders). Folders end with '/'.

    Args:
        pattern: The glob pattern.
        recursive: Also match in all subfolders, e.g. '*.py' anywhere. Defaults to False.
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

        gitignore_patterns = load_gitignore_patterns()
        matches = []
        for path, is_dir in walk(ALLOWED_DIR, gitignore_patterns, ALLOWED_DIR, max_depth):
            rel_path = path.relative_to(ALLOWED_DIR).as_posix()
            if regex.match(rel_path):
                matches.append(rel_path + ("/" if is_dir else ""))

        if not matches:
            msg = f"No files found matching pattern: '{pattern}'"
            if not recursive:
                msg += ". Try setting recursive=True to search subdirectories."
            return msg

        return _format_list(matches, ALLOWED_DIR.as_posix())
    except Exception as e:
        return f"Error: Searching files by pattern failed:\n{e}\n{traceback.format_exc()}"

prepare_tools(mcp)

if __name__ == "__main__":
    mcp.run()
