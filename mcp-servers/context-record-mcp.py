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

def _limit_content(content: str, filename: str) -> str:
    """
    Returns the content of a context file, cut short at a line break with an 'Output limited'
    instruction if it is longer than MAX_OUTPUT_CHARS.
    """
    limit = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
    if len(content) <= limit:
        return content
    cut = content.rfind("\n", 0, limit)
    cut = cut if cut > 0 else limit
    next_line = content.count("\n", 0, cut) + 2
    return (f"{content[:cut]}\n\n"
            f"Output limited: only the first {cut} of {len(content)} characters of '{filename}' are shown."
            f" If the context folder is inside the project, read the rest with read_file_with_metadata(start_line={next_line})."
            f" Keep context files short: summarize long notes and split them into several files with write_context_file.")

def _list_context_files(folder: Path) -> list[Path]:
    """
    Returns the Markdown files directly inside the context folder, sorted by name.
    """
    return sorted(p for p in folder.glob("*.md") if p.is_file() and _is_context_file(p, folder))

# --- Public MCP Tools ---

@mcp.tool()
def list_context_files() -> str:
    """
    Lists all Markdown files in the context folder.

    Returns:
        str: A newline-separated list of filenames, or an error message.
    """
    try:
        folder = _get_context_folder()
        if not folder.exists():
            return f"Error: Folder does not exist at {folder.as_posix()}"
        if not folder.is_dir():
            return f"Error: Path {folder.as_posix()} is not a directory."

        files = [p.name for p in _list_context_files(folder)]
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
    Reads the content of a specific Markdown file in the context folder.

    Args:
        filename (str): The name of the file to read (e.g., 'example.md').

    Returns:
        str: The content of the file, or an error message.
    """
    try:
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

    Args:
        filename (str): The name of the file to write to (e.g., 'example.md').
        content (str): The content to write.

    Returns:
        str: A success message or an error message.
    """
    try:
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
    If the file doesn't exist, it will be created.

    Args:
        filename (str): The name of the file to append to (e.g., 'example.md').
        content (str): The content to append.

    Returns:
        str: A success message or an error message.
    """
    try:
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
    Reads the content of all Markdown files in the context folder and returns them
    all at once. If they do not all fit in one response, the files left out are listed
    so that they can be read one at a time with read_context_file.

    Returns:
        str: The concatenated content of all files, or an error message.
    """
    try:
        folder = _get_context_folder()
        if not folder.exists():
            return f"Error: Folder does not exist at {folder.as_posix()}"

        md_files = _list_context_files(folder)
        if not md_files:
            return "No Markdown files found in the context folder."

        # Include whole files until MAX_OUTPUT_CHARS, leaving room for the note
        budget = MAX_OUTPUT_CHARS - min(1000, MAX_OUTPUT_CHARS // 4)
        output, skipped, used = [], [], 0
        for file_path in md_files:
            block = "\n".join(["=" * 80, f"FILE: {file_path.name}", "=" * 80, file_path.read_text(encoding="utf-8"), "\n"])
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
    Removes a specific Markdown file from the context folder.

    Args:
        filename (str): The name of the file to remove (e.g., 'example.md').

    Returns:
        str: A success message or an error message.
    """
    try:
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
