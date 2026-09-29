# --- generate-map-mcp.py ---
import traceback
from dataclasses import dataclass
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    ALLOWED_DIR,
    IGNORED_DIRS,
    MAX_OUTPUT_CHARS,
    access_denied_message,
    is_ignored,
    is_path_allowed,
    load_gitignore_patterns,
    resolve_path,
    walk
)
from mcp_outline import (
    FULL,
    MAX_PARSE_CHARS,
    NAMES,
    OUTLINE_SUFFIXES,
    PYTHON_SUFFIXES,
    SUMMARIES,
    TOP_LEVEL,
    Outline,
    count_lines,
    fit_lines,
    outline_text,
    python_symbols,
    render,
    section_count
)

# --- Constants & Config ---
mcp = MCPServer("Generate-Map-Server")

FILES = TOP_LEVEL + 1  # Detail level listing files without their sections
FOLDERS = TOP_LEVEL + 2  # Detail level listing the files directly in the folder and its subfolders with file counts
MAX_OUTLINED_FILES = 1000  # Folders with more files are listed with FOLDERS detail, without reading the files
MAX_FOLDER_SUGGESTIONS = 10  # Folders suggested for outlining separately when the output is limited
READ_HINT = "Line spans are first-last line numbers. Read a section with read_file_with_metadata(path, section=...) or with start_line and end_line."
LEVEL_NOTES = {
    NAMES: "summaries are left out",
    TOP_LEVEL: "only top-level sections are shown",
    FILES: "only the files are listed, without their sections",
    FOLDERS: "only the files directly in the folder and the subfolders with their file counts are listed",
}

@dataclass
class _FileEntry:
    path: Path
    rel: str
    size: int
    lines: int | None = None  # None for binary, unreadable or very large files
    outline: Outline | None = None
    note: str = ""  # Why the file has no line count or outline

# --- Internal Helpers ---

def _format_size(size: int) -> str:
    """
    Returns a file size in B, KB or MB.
    """
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"

def _read_entry(path: Path, known: dict | None = None) -> _FileEntry:
    """
    Reads a file's line count and outline. Binary, unreadable and very large files only get their size.
    """
    entry = _FileEntry(path, path.relative_to(ALLOWED_DIR).as_posix(), 0)
    try:
        entry.size = path.stat().st_size
        if entry.size > MAX_PARSE_CHARS:
            entry.note = "too large to outline"
            return entry
        text = path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        entry.note = "binary"
        return entry
    except OSError as e:
        entry.note = f"unreadable: {e.strerror or e}"
        return entry
    entry.lines = count_lines(text)
    entry.outline = outline_text(text, path.suffix, known)
    return entry

def _file_line(entry: _FileEntry, detail: int, depth: int = 0) -> str:
    """
    Returns the line naming a file, e.g. 'src/app.py (120 lines): Summary', with its section count at FILES detail.
    """
    if entry.lines is None:
        return f"{'  ' * depth}{entry.rel} ({entry.note}, {_format_size(entry.size)})"
    info = [f"{entry.lines} {'line' if entry.lines == 1 else 'lines'}"]
    if entry.outline and entry.outline.error:
        info.append(f"not outlined: {entry.outline.error}")
    elif entry.outline and entry.outline.syntax_error:
        info.append(entry.outline.syntax_error)
    if entry.outline and not entry.outline.error and detail >= FILES:
        count = section_count(entry.outline)
        info.append(f"{count} {'section' if count == 1 else 'sections'}")
    line = f"{'  ' * depth}{entry.rel} ({', '.join(info)})"
    if entry.outline and entry.outline.summary and detail <= SUMMARIES:
        line += f": {entry.outline.summary}"
    return line

def _render_entries(entries: list[_FileEntry], detail: int, scope_dir: Path) -> list[str]:
    """
    Returns the lines of a folder outline at the given detail level. At FOLDERS detail, a subfolder
    holding all the files is descended into, as listing only that one folder would not help.
    """
    if detail == FOLDERS:
        while True:
            subfolders = {entry.path.relative_to(scope_dir).parts[0] for entry in entries}
            if len(subfolders) != 1 or (scope_dir / next(iter(subfolders))).is_file():
                break
            scope_dir = scope_dir / next(iter(subfolders))
        direct, counts = [], {}
        for entry in entries:
            parts = entry.path.relative_to(scope_dir).parts
            if len(parts) > 1:
                folder = (scope_dir / parts[0]).relative_to(ALLOWED_DIR).as_posix()
                counts[folder] = counts.get(folder, 0) + 1
            else:
                direct.append(_file_line(entry, FILES))
        folders = [f"{folder}/ ({count} {'file' if count == 1 else 'files'})" for folder, count in counts.items()]
        return folders + direct

    lines = []
    for entry in entries:
        lines.append(_file_line(entry, detail))
        if entry.outline and not entry.outline.error and detail <= TOP_LEVEL:
            lines.extend(render(entry.outline, detail, depth=1))
    return lines

def _folder_suggestions(scope_dir: Path, files: list[Path]) -> str:
    """
    Returns suggestions for outlining the subfolders of scope_dir separately, with the most files first.
    If all files are in one subfolder, its subfolders are suggested instead, since that subfolder would not fit either.
    """
    while True:
        counts, direct = {}, 0
        for path in files:
            parts = path.relative_to(scope_dir).parts
            if len(parts) > 1:
                counts[scope_dir / parts[0]] = counts.get(scope_dir / parts[0], 0) + 1
            else:
                direct += 1
        if len(counts) == 1 and not direct:
            scope_dir = next(iter(counts))
            continue
        break

    suggestions = [f"path='{folder.relative_to(ALLOWED_DIR).as_posix()}' ({count} {'file' if count == 1 else 'files'})"
                   for folder, count in sorted(counts.items(), key=lambda item: -item[1])[:MAX_FOLDER_SUGGESTIONS]]
    instructions = []
    if suggestions:
        instructions.append(f"See more detail one folder at a time with get_outline(path=...), for example: {', '.join(suggestions)}.")
    instructions.append("See all sections of one file with get_outline(path='<file path>').")
    return " ".join(instructions)

def _resolve_target(path: str | None, gitignore_patterns: list[str]) -> tuple[Path, str | None]:
    """
    Resolves the folder or file to outline. Relative paths are resolved against ALLOWED_DIR.
    Returns the path and an error message, which is None if the path can be outlined.
    """
    if not path:
        return ALLOWED_DIR, None
    p = resolve_path(path)
    if not is_path_allowed(p):
        return p, f"Error: {access_denied_message(p)}"
    if not p.exists():
        return p, f"Error: Path '{path}' does not exist within the allowed directory {ALLOWED_DIR.as_posix()}. Use get_outline() to see the files."
    if p.is_dir() and p != ALLOWED_DIR and is_ignored(p, ALLOWED_DIR, IGNORED_DIRS, gitignore_patterns):
        return p, f"Error: Folder '{path}' is ignored: it is an ignored folder or matched by .gitignore. Use get_config to see why."
    return p, None

def _outline_folder(scope_dir: Path, gitignore_patterns: list[str]) -> str:
    """
    Returns the outline of all files in the folder, with the most detail that fits in MAX_OUTPUT_CHARS.
    """
    files = [path for path, is_dir in walk(ALLOWED_DIR, gitignore_patterns, scope_dir) if not is_dir]
    rel = scope_dir.relative_to(ALLOWED_DIR).as_posix()
    title = f"Outline of {'the allowed directory' if rel == '.' else rel} ({len(files)} {'file' if len(files) == 1 else 'files'})"
    if not files:
        return f"{title}\nNo files found. Use get_config to see which files are ignored or forbidden."

    if len(files) > MAX_OUTLINED_FILES:
        entries = [_read_entry(p) if p.parent == scope_dir else _FileEntry(p, p.relative_to(ALLOWED_DIR).as_posix(), 0) for p in files]
        levels = [FOLDERS]
    else:
        entries = [_read_entry(p) for p in files]
        levels = [SUMMARIES, NAMES, TOP_LEVEL, FILES, FOLDERS]

    budget = MAX_OUTPUT_CHARS - min(2500, MAX_OUTPUT_CHARS // 4)
    for detail in levels:
        lines = _render_entries(entries, detail, scope_dir)
        if sum(len(line) + 1 for line in lines) <= budget:
            break
    else:
        lines = fit_lines(lines, budget)

    header = [title, READ_HINT]
    if detail != SUMMARIES or lines[-1].startswith("... ("):
        reason = f"the folder has more than {MAX_OUTLINED_FILES} files" if len(files) > MAX_OUTLINED_FILES else f"the full outline is longer than {MAX_OUTPUT_CHARS} characters"
        header.append(f"Output limited: {LEVEL_NOTES.get(detail, 'the list is cut short')}, as {reason}. {_folder_suggestions(scope_dir, files)}")
    return "\n".join(header + [""] + lines)

def _outline_file(path: Path, gitignore_patterns: list[str]) -> str:
    """
    Returns the outline of one file with all its sections, their summaries and, for Python, the
    project's own functions and classes each function uses.
    """
    known = None
    if path.suffix.lower() in PYTHON_SUFFIXES:
        modules = []
        for p, is_dir in walk(ALLOWED_DIR, gitignore_patterns, ALLOWED_DIR):
            if not is_dir and p.suffix.lower() in PYTHON_SUFFIXES and p.stat().st_size <= MAX_PARSE_CHARS:
                try:
                    modules.append((p.stem, p.read_text(encoding="utf-8-sig")))
                except (OSError, UnicodeDecodeError):
                    continue
        known = python_symbols(modules)

    entry = _read_entry(path, known)
    title = _file_line(entry, FULL)
    read = f"read_file_with_metadata(path='{entry.rel}'"
    if entry.outline is None:
        if entry.note == "binary":
            advice = "It is not a text file. View PNG, JPEG, GIF and WebP images with read_image."
        elif entry.lines is None:
            advice = f"Find the lines you need with search_text_in_files(path='{entry.rel}'), then read them with {read}, start_line=..., end_line=...)."
        else:
            advice = f"Outlines are only available for {', '.join(sorted(OUTLINE_SUFFIXES))} files. Read the file with {read})."
        return f"{title}\n{advice}"
    if entry.outline.error:
        return f"{title}\nRead the file with {read}, start_line=..., end_line=...)."
    if not entry.outline.sections and not entry.outline.notes:
        return f"{title}\nNo sections found. Read the file with {read})."

    budget = MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4)
    for detail in (FULL, SUMMARIES, NAMES, TOP_LEVEL):
        lines = render(entry.outline, detail)
        if sum(len(line) + 1 for line in lines) <= budget:
            break
    else:
        lines = fit_lines(lines, budget, "sections")

    header = [title, f"Line spans are first-last line numbers. Read a section with {read}, section=...) or with start_line and end_line."]
    if detail > SUMMARIES or lines[-1].startswith("... ("):
        header.append(f"Output limited: {LEVEL_NOTES.get(detail, 'the list is cut short')}, as the full outline is longer than {MAX_OUTPUT_CHARS} characters."
                      f" Read the parts you need with {read}, start_line=..., end_line=...).")
    return "\n".join(header + [""] + lines)

# --- Public MCP Tools ---

@mcp.tool()
def get_outline(path: str | None = None) -> str:
    """
    Returns an outline with line spans. For a folder: every file with its line count, and the classes,
    functions and Markdown headings in it. For a file: all its sections with their summaries and, in
    Python, the project's own functions each function calls.
    Start here to find the code you need, then read only that part with
    read_file_with_metadata(path, section=...) or with start_line and end_line.
    Large folders are shown with less detail: follow the 'Output limited' message to see more.

    Args:
        path (str, optional): A folder or file, relative to the allowed directory (e.g. 'src' or
            'src/app.py'). Defaults to the whole allowed directory.

    Returns:
        str: The outline as plain text, or an error message.
    """
    if not ALLOWED_DIR.exists():
        return f"Error: Allowed directory {ALLOWED_DIR.as_posix()} does not exist."

    try:
        gitignore_patterns = load_gitignore_patterns()
        target, error = _resolve_target(path, gitignore_patterns)
        if error:
            return error
        if target.is_dir():
            return _outline_folder(target, gitignore_patterns)
        return _outline_file(target, gitignore_patterns)
    except Exception as e:
        return f"Error: Generating outline failed:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
