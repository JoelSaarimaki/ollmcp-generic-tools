# --- context-record-mcp.py ---
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    CONFIG_PATH,
    CONTEXT_FOLDER,
    MAX_OUTPUT_CHARS,
    READ_ONLY_FILES,
    prepare_tools
)
from mcp_outline import (
    NAMES,
    TOP_LEVEL,
    count_lines,
    fit_lines,
    locate_section,
    outline_after_write,
    outline_text,
    render
)

# --- Constants & Config ---
mcp = MCPServer("Context-Record-Server")

CONTENT_START = "----- BEGIN CONTENT -----"
CONTENT_END = "----- END CONTENT -----"
OUTLINE_AFTER_WRITE_CHARS = min(2500, MAX_OUTPUT_CHARS // 8)  # Room for the outline shown after a write
KEEP_SHORT_ADVICE = " Keep context files short: summarize long notes and split them into several files with write_context_file."
READ_ONLY_ADVICE = " Tell the user that this read-only file is long and should be shortened."

# --- Internal Helpers ---

def _index_read_only_files() -> dict[str, Path]:
    """
    Returns the read_only_files of the tools config, such as project instructions, as {lowercase file name: path}.
    Raises an error if a file does not exist or two files have the same name, so a typo cannot silently hide a file.
    """
    files = {}
    for p in READ_ONLY_FILES:
        if not p.is_file():
            raise FileNotFoundError(f"{CONFIG_PATH}: read_only_files: file not found: {p}")
        if p.name.lower() in files:
            raise ValueError(f"{CONFIG_PATH}: read_only_files: two files are named '{p.name}'. Read-only files are read by name, so their names must be unique.")
        files[p.name.lower()] = p
    return files

READ_ONLY_BY_NAME = _index_read_only_files()

def _get_context_folder() -> Path:
    """
    Returns the context folder.
    Raises ValueError if context_folder is not set in the tools config.
    """
    if not CONTEXT_FOLDER:
        raise ValueError(f"'context_folder' is not set in the tools config file {CONFIG_PATH}.")

    return CONTEXT_FOLDER

def _is_context_file(path: Path, folder: Path) -> bool:
    """
    Checks if the path, after following symlinks, is a Markdown file directly inside the context folder.
    """
    resolved = path.resolve()
    return resolved.parent == folder and resolved.suffix.lower() == ".md"

def _get_context_file(filename: str) -> tuple[Path, Path]:
    """
    Returns the context folder and the path of the named Markdown file directly inside it.
    Raises ValueError if the name contains folders, is not a .md file or leads outside the folder,
    so that the tools can never reach other files such as .mcp.json.
    """
    folder = _get_context_folder()
    if not filename or any(c in filename for c in "/\\:"):
        raise ValueError(f"'{filename}' is not a plain file name. Use a name such as 'notes.md', without folders.")
    if not filename.lower().endswith(".md"):
        raise ValueError(f"'{filename}' is not a Markdown file name. Context file names must end with '.md'.")

    file_path = folder / filename
    if not _is_context_file(file_path, folder):
        raise ValueError(f"'{filename}' leads outside the context folder.")
    return folder, file_path

def _check_writable(filename: str):
    """
    Raises ValueError if the file name belongs to a read-only file, which the tools must not change or remove.
    Also prevents creating a context file that would hide a read-only file with the same name.
    """
    if filename.lower() in READ_ONLY_BY_NAME:
        raise ValueError(f"'{filename}' is read-only: it is maintained by the user and cannot be changed or removed. Write your own notes to another file.")

def _normalize_newlines(text: str) -> str:
    """
    Converts all line endings to LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")

def _read_text(path: Path) -> str:
    """
    Reads a UTF-8 text file with its line endings converted to LF.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return _normalize_newlines(f.read())

def _write_text(path: Path, text: str):
    """
    Writes text with LF line endings as UTF-8, keeping CRLF line endings if the existing file has them.
    """
    crlf = path.is_file() and b"\r\n" in path.read_bytes()
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", "\r\n") if crlf else text)

def _file_outline(text: str, detail: int = NAMES) -> list[str]:
    """
    Returns the heading outline of a Markdown file with line spans.
    """
    outline = outline_text(text, ".md")
    return render(outline, detail) if outline else []

def _content_response(name: str, text: str, read_only: bool, start_line: int | None = None,
                      end_line: int | None = None, section: str | None = None, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """
    Returns lines of a file as plain text: a header with the line numbers shown, and the content between
    the content markers. The lines are a section, a line range or the whole file, limited to max_chars.
    """
    lines = text.split("\n")[:count_lines(text)]
    total = len(lines)

    section_line = ""
    if section:
        if start_line is not None or end_line is not None:
            return "Error: Give either section, or start_line and end_line, not both."
        found, error, message = locate_section(outline_text(text, ".md"), section)
        if not found:
            if error == "section_not_found":
                message += " list_context_files shows the headings of each file with their line spans."
            return f"Error: {message}"
        start_line, end_line = found.start, found.end
        section_line = f"Section: {found.label} (lines {found.start}-{found.end})"

    start = 1 if start_line is None else int(start_line)
    end = total if end_line is None else min(int(end_line), total)
    if total and (start < 1 or start > total):
        return f"Error: start_line must be between 1 and {total}, the number of lines in '{name}'."
    if total and end < start:
        return f"Error: end_line must not be smaller than start_line ({start})."

    # Keep the read within max_chars, leaving room for the header and the note (at least one line is shown)
    budget = max_chars - min(1000, max(450, max_chars // 4))
    shown, chars = [], 0
    for line in lines[start - 1:end]:
        chars += len(line) + 1
        if chars > budget and shown:
            break
        shown.append(line)
    last = start + len(shown) - 1

    header = [f"File: {name}" + (" (read-only)" if read_only else "")]
    if not total:
        header.append("Lines: 0 (empty file)")
    else:
        header.append(f"Lines: {start}-{last} of {total}" + (" (partial)" if start > 1 or last < total else ""))
    if section_line:
        header.append(section_line)
    if total and last < end:
        next_range = f"start_line={last + 1}" + (f", end_line={end}" if end < total else "")
        header.append(f"Output limited: lines {start}-{last} of {total} are shown, as the output is limited to {MAX_OUTPUT_CHARS} characters."
                      f" Read the next part with read_context_file('{name}', {next_range}).{READ_ONLY_ADVICE if read_only else KEEP_SHORT_ADVICE}")
    return "\n".join(header + [CONTENT_START, *shown, CONTENT_END])

def _list_context_files(folder: Path) -> list[Path]:
    """
    Returns the Markdown files directly inside the context folder, sorted by name.
    Files named like a read-only file are left out, as that name always refers to the read-only file.
    """
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.md")
                  if p.is_file() and _is_context_file(p, folder) and p.name.lower() not in READ_ONLY_BY_NAME)

def _all_files(folder: Path) -> list[tuple[str, Path, bool]]:
    """
    Returns (name, path, read-only) for the read-only files followed by the context files.
    """
    return ([(p.name, p, True) for p in READ_ONLY_BY_NAME.values()] +
            [(p.name, p, False) for p in _list_context_files(folder)])

def _file_title(name: str, text: str, read_only: bool) -> str:
    """
    Returns the line naming a file in a listing, e.g. 'plan.md (read-only, 40 lines)'.
    """
    lines = count_lines(text)
    return f"{name} ({'read-only, ' if read_only else ''}{lines} {'line' if lines == 1 else 'lines'})"

def _file_block(name: str, content: str) -> str:
    """
    Returns a file's content with a header, as shown when all files are read.
    """
    return f"===== FILE: {name} =====\n{content.rstrip()}\n"

def _write_response(action: str, filename: str, before: str | None, after: str) -> str:
    """
    Returns the response of a write: the success message, warnings about removed headings and the new outline.
    """
    lines = [f"Successfully {action} context file: {filename}"]
    report = outline_after_write(before, after, ".md", OUTLINE_AFTER_WRITE_CHARS)
    if report:
        lines.extend(report["warnings"])
        if report["outline"]:
            lines.extend(["Outline after the write:", *report["outline"]])
    return "\n".join(lines)

def _read_all() -> str:
    """
    Returns the read-only files followed by all context files. Read-only files are never left out;
    context files are included whole while they fit in MAX_OUTPUT_CHARS, then as their outlines.
    """
    folder = _get_context_folder()
    files = _all_files(folder)
    if not files:
        if not folder.exists():
            return f"Error: Folder does not exist at {folder.as_posix()}"
        return "No Markdown files found in the context folder."

    budget = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
    output, outlined, skipped, used = [], [], [], 0
    read_only_left = len(READ_ONLY_BY_NAME)
    for name, path, read_only in files:
        text = _read_text(path)
        if read_only:
            # A read-only file that does not fit is shown as its first part, with how to read the rest,
            # sharing the rest of the budget equally with the read-only files after it
            share = (budget - used) // read_only_left - 200
            block = _file_block(f"{name} (read-only)", text if len(text) <= share else _content_response(name, text, True, max_chars=share))
            read_only_left -= 1
        else:
            block = _file_block(name, text)
            if used + len(block) > budget:
                outline = _file_outline(text)
                block = _file_block(f"{name} (outline only, {count_lines(text)} lines)", "\n".join(outline or ["(no headings)"]))
                if used + len(block) > budget:
                    skipped.append(name)
                    continue
                outlined.append(name)
        output.append(block)
        used += len(block) + 1

    notes = []
    if outlined:
        notes.append(f"{len(outlined)} context files are too long to include and are shown as their headings with line spans: {', '.join(outlined)}."
                     " Read the sections you need with read_context_file(filename, section=...).")
    if skipped:
        notes.append(f"{len(skipped)} context files are not shown at all: {', '.join(skipped)}. Read them with read_context_file(filename).")
    if notes:
        output.insert(0, f"Output limited: the output is limited to {MAX_OUTPUT_CHARS} characters. {' '.join(notes)}\n")
    return "\n".join(output)

# --- Public MCP Tools ---

@mcp.tool()
def list_context_files() -> str:
    """
    Lists the read-only files (e.g. project instructions) and the context files (plans and notes),
    with their headings and line spans.
    """
    try:
        folder = _get_context_folder()
        if folder.exists() and not folder.is_dir():
            return f"Error: Path {folder.as_posix()} is not a directory."

        files = [(name, _read_text(path), read_only) for name, path, read_only in _all_files(folder)]
        if not files:
            return "No Markdown (.md) files found in the context folder."

        budget = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
        for detail in (NAMES, TOP_LEVEL, None):
            lines = []
            for name, text, read_only in files:
                lines.append(_file_title(name, text, read_only))
                if detail is not None:
                    lines.extend(_file_outline(text, detail))
            if sum(len(line) + 1 for line in lines) <= budget:
                break
        else:
            lines = fit_lines(lines, budget, "files")
        if detail != NAMES:
            lines.insert(0, "Output limited: " + ("only the top-level headings are shown." if detail == TOP_LEVEL else "the headings are not shown.")
                         + " Read a file to see its headings.")
        return "\n".join(lines)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Listing context files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def read_context_file(filename: str = "", section: str = "", start_line: int | None = None, end_line: int | None = None) -> str:
    """
    Reads a context file (plans and notes) or a read-only file (e.g. project instructions).
    Without filename, reads all of them: do this at the start of a session.

    Args:
        filename: e.g. 'plan.md'. Defaults to all files.
        section: A heading to read, as listed by list_context_files.
        start_line: The first line to read.
        end_line: The last line to read.
    """
    try:
        if not filename:
            if section or start_line is not None or end_line is not None:
                return "Error: section, start_line and end_line need a filename. Use list_context_files to see the files and their headings."
            return _read_all()

        read_only = READ_ONLY_BY_NAME.get(filename.lower())
        if read_only:
            return _content_response(read_only.name, _read_text(read_only), True, start_line, end_line, section)

        folder, file_path = _get_context_file(filename)

        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."

        return _content_response(filename, _read_text(file_path), False, start_line, end_line, section)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Reading context file failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def write_context_file(filename: str, content: str, append: bool = False) -> str:
    """
    Writes a Markdown file in the context folder, or adds content to its end. Read-only files cannot
    be changed. The response shows the file's headings and warns if headings were removed.

    Args:
        filename: e.g. 'plan.md'.
        content: The content to write.
        append: Add content to the end of the file instead of replacing it. Defaults to False.
    """
    try:
        _check_writable(filename)
        folder, file_path = _get_context_file(filename)
        before = _read_text(file_path) if file_path.is_file() else None

        # Ensure the directory exists
        folder.mkdir(parents=True, exist_ok=True)

        after = _normalize_newlines(content)
        if append and before:
            after = f"{before}\n{after}"  # The appended content starts on a new line
        _write_text(file_path, after)

        return _write_response("appended to" if append else "wrote to", filename, before, after)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Writing to context file failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def remove_context_file(filename: str) -> str:
    """
    Removes a context file. Read-only files cannot be removed.

    Args:
        filename: e.g. 'plan.md'.
    """
    try:
        _check_writable(filename)
        folder, file_path = _get_context_file(filename)

        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."

        file_path.unlink()
        return f"Successfully removed context file: {filename}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Removing context file failed:\n{e}\n{traceback.format_exc()}"

prepare_tools(mcp)

if __name__ == "__main__":
    mcp.run()
