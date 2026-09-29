# --- context-record-mcp.py ---
import os
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Context-Record-Server")

# The folder path is defined via an environment variable.
CONTEXT_FOLDER_PATH = os.getenv("CONTEXT_FOLDER_PATH")
# Largest response size in characters (about 4 characters per token). Lower it for models with small context windows.
MAX_OUTPUT_CHARS = max(2000, int(os.getenv("MAX_OUTPUT_CHARS") or 40000))

# --- Internal Helpers ---

def _load_read_only_files() -> dict[str, Path]:
    """
    Loads the files maintained by the user, such as project instructions, from the READ_ONLY_FILES env var.
    Paths are separated by commas and can be anywhere. Returns {lowercase file name: path}.
    Raises an error if a file does not exist or two files have the same name, so a typo cannot silently hide a file.
    """
    raw = os.getenv("READ_ONLY_FILES", "").strip()
    if not raw:
        return {}

    files = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        p = Path(entry).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"READ_ONLY_FILES: file not found: {p}")
        if p.name.lower() in files:
            raise ValueError(f"READ_ONLY_FILES: two files are named '{p.name}'. Read-only files are read by name, so their names must be unique.")
        files[p.name.lower()] = p
    return files

READ_ONLY_FILES = _load_read_only_files()

def _get_context_folder() -> Path:
    """
    Returns the resolved path to the context folder.
    Raises ValueError if CONTEXT_FOLDER_PATH is not set.
    """
    if not CONTEXT_FOLDER_PATH:
        raise ValueError("Environment variable 'CONTEXT_FOLDER_PATH' is not set.")

    return Path(CONTEXT_FOLDER_PATH).resolve()

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
    if filename.lower() in READ_ONLY_FILES:
        raise ValueError(f"'{filename}' is read-only: it is maintained by the user and cannot be changed or removed. Write your own notes to another file.")

def _limit_content(content: str, filename: str, read_only: bool = False) -> str:
    """
    Returns the content of a file, cut short at a line break with an 'Output limited'
    instruction if it is longer than MAX_OUTPUT_CHARS.
    """
    limit = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
    if len(content) <= limit:
        return content
    cut = content.rfind("\n", 0, limit)
    cut = cut if cut > 0 else limit
    next_line = content.count("\n", 0, cut) + 2
    advice = (" Tell the user that this read-only file is too long and should be shortened." if read_only else
              " Keep context files short: summarize long notes and split them into several files with write_context_file.")
    return (f"{content[:cut]}\n\n"
            f"Output limited: only the first {cut} of {len(content)} characters of '{filename}' are shown."
            f" If the file is inside the project, read the rest with read_file_with_metadata(start_line={next_line}).{advice}")

def _list_context_files(folder: Path) -> list[Path]:
    """
    Returns the Markdown files directly inside the context folder, sorted by name.
    Files named like a read-only file are left out, as that name always refers to the read-only file.
    """
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.md")
                  if p.is_file() and _is_context_file(p, folder) and p.name.lower() not in READ_ONLY_FILES)

def _file_block(name: str, content: str) -> str:
    """
    Returns a file's content with a header, as shown by read_all_context_files.
    """
    return "\n".join(["=" * 80, f"FILE: {name}", "=" * 80, content, "\n"])

# --- Public MCP Tools ---

@mcp.tool()
def list_context_files() -> str:
    """
    Lists all Markdown files in the context folder, and the read-only files maintained by
    the user (such as project instructions), which are marked '(read-only)'.

    Returns:
        str: A newline-separated list of filenames, or an error message.
    """
    try:
        folder = _get_context_folder()
        if folder.exists() and not folder.is_dir():
            return f"Error: Path {folder.as_posix()} is not a directory."

        files = [f"{p.name} (read-only)" for p in READ_ONLY_FILES.values()]
        files += [p.name for p in _list_context_files(folder)]
        if not files:
            return "No Markdown (.md) files found in the context folder."

        return "\n".join(files)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Listing context files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def read_context_file(filename: str) -> str:
    """
    Reads the content of a specific Markdown file in the context folder, or of a read-only file.

    Args:
        filename (str): The name of the file to read (e.g., 'example.md').

    Returns:
        str: The content of the file, or an error message.
    """
    try:
        read_only = READ_ONLY_FILES.get(filename.lower())
        if read_only:
            return _limit_content(read_only.read_text(encoding="utf-8"), read_only.name, read_only=True)

        folder, file_path = _get_context_file(filename)

        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."

        return _limit_content(file_path.read_text(encoding="utf-8"), filename)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Reading context file failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def write_context_file(filename: str, content: str) -> str:
    """
    Creates a new Markdown file or overwrites an existing one in the context folder.
    Read-only files cannot be written.

    Args:
        filename (str): The name of the file to write to (e.g., 'example.md').
        content (str): The content to write.

    Returns:
        str: A success message or an error message.
    """
    try:
        _check_writable(filename)
        folder, file_path = _get_context_file(filename)

        # Ensure the directory exists
        folder.mkdir(parents=True, exist_ok=True)

        file_path.write_text(content, encoding="utf-8")

        return f"Successfully wrote to context file: {filename} (in {folder.as_posix()})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Writing to context file failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def append_to_context_file(filename: str, content: str) -> str:
    """
    Appends content to a specific Markdown file in the context folder.
    If the file doesn't exist, it will be created. Read-only files cannot be appended to.

    Args:
        filename (str): The name of the file to append to (e.g., 'example.md').
        content (str): The content to append.

    Returns:
        str: A success message or an error message.
    """
    try:
        _check_writable(filename)
        folder, file_path = _get_context_file(filename)

        # Ensure the directory exists
        folder.mkdir(parents=True, exist_ok=True)

        with open(file_path, mode="a", encoding="utf-8") as f:
            # Ensure we start on a new line if the file is not empty
            if file_path.exists() and file_path.stat().st_size > 0:
                f.write("\n")
            f.write(content)

        return f"Successfully appended to context file: {filename} (in {folder.as_posix()})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Appending to context file failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def read_all_context_files() -> str:
    """
    Call this at the start of a session: it returns the project instructions and other
    read-only files maintained by the user, followed by all Markdown files in the context
    folder with the existing plans and records. If they do not all fit in one response,
    the context files left out are listed so that they can be read one at a time with
    read_context_file.

    Returns:
        str: The concatenated content of all files, or an error message.
    """
    try:
        folder = _get_context_folder()
        md_files = _list_context_files(folder)
        if not md_files and not READ_ONLY_FILES:
            if not folder.exists():
                return f"Error: Folder does not exist at {folder.as_posix()}"
            return "No Markdown files found in the context folder."

        # Read-only files come first and are never left out; whole context files are then included
        # until MAX_OUTPUT_CHARS, leaving room for the note
        budget = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
        output, skipped, used = [], [], 0
        for path in READ_ONLY_FILES.values():
            block = _file_block(f"{path.name} (read-only)", _limit_content(path.read_text(encoding="utf-8"), path.name, read_only=True))
            output.append(block)
            used += len(block) + 1
        for file_path in md_files:
            block = _file_block(file_path.name, file_path.read_text(encoding="utf-8"))
            if used + len(block) > budget:
                skipped.append(file_path.name)
                continue
            output.append(block)
            used += len(block) + 1

        if skipped:
            note = (f"Output limited: {len(skipped)} of {len(md_files)} context files are not shown, as the output is limited to {MAX_OUTPUT_CHARS} characters:"
                    f" {', '.join(skipped)}. Read them one at a time with read_context_file.")
            output.insert(0, note + "\n")

        return "\n".join(output)
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Reading all context files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def remove_context_file(filename: str) -> str:
    """
    Removes a specific Markdown file from the context folder. Read-only files cannot be removed.

    Args:
        filename (str): The name of the file to remove (e.g., 'example.md').

    Returns:
        str: A success message or an error message.
    """
    try:
        _check_writable(filename)
        folder, file_path = _get_context_file(filename)

        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."

        file_path.unlink()
        return f"Successfully removed context file: {filename} (from {folder.as_posix()})"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Removing context file failed:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
