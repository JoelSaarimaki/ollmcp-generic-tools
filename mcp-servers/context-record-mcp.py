from mcp.server.mcpserver import MCPServer
import os
import traceback
from pathlib import Path

mcp = MCPServer("ContextFolderManager")

# --- Constants & Config ---
# The folder path is defined via an environment variable.
CONTEXT_FOLDER_PATH = os.getenv("CONTEXT_FOLDER_PATH")

# --- Internal Helpers ---

def _get_context_folder() -> Path:
    """
    Returns the Path object for the context folder, 
    raising an error if the environment variable is not set.
    """
    if not CONTEXT_FOLDER_PATH:
        raise ValueError("Environment variable 'CONTEXT_FOLDER_PATH' is not set.")
    
    path = Path(CONTEXT_FOLDER_PATH)
    return path

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
        
        files = [f.name for f in folder.glob("*.md")]
        if not files:
            return "No Markdown (.md) files found in the context folder."
        
        return "\n".join(sorted(files))
    except Exception as e:
        return f"Error listing context files:\n{e}\n{traceback.format_exc()}"

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
        folder = _get_context_folder()
        file_path = folder / filename
        
        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."
            
        content = file_path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        return f"Error reading context file:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def write_context_file(filename: str, content: str) -> str:
    """
    Creates a new Markdown file or overwrites an existing one in the context folder.

    Args:
        filename (str): The name of the file to write to.
        content (str): The content to write.

    Returns:
        str: A success message or an error message.
    """
    try:
        folder = _get_context_folder()
        file_path = folder / filename
        
        # Ensure the directory exists
        folder.mkdir(parents=True, exist_ok=True)
        
        file_path.write_text(content, encoding='utf-8')
            
        return f"Successfully wrote to context file: {filename} (in {folder.as_posix()})"
    except Exception as e:
        return f"Error writing to context file:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def append_to_context_file(filename: str, content: str) -> str:
    """
    Appends content to a specific Markdown file in the context folder. 
    If the file doesn't exist, it will be created.

    Args:
        filename (str): The name of the file to append to.
        content (str): The content to append.

    Returns:
        str: A success message or an error message.
    """
    try:
        folder = _get_context_folder()
        file_path = folder / filename
        
        # Ensure the directory exists
        folder.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, mode='a', encoding='utf-8') as f:
            # Ensure we start on a new line if the file is not empty
            if file_path.exists() and file_path.stat().st_size > 0:
                f.write("\n")
            f.write(content)
            
        return f"Successfully appended to context file: {filename} (in {folder.as_posix()})"
    except Exception as e:
        return f"Error appending to context file:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def read_all_context_files() -> str:
    """
    Reads the content of all Markdown files in the context folder and returns them
    all at once.

    Returns:
        str: The concatenated content of all files, or an error message.
    """
    try:
        folder = _get_context_folder()
        if not folder.exists():
            return f"Error: Folder does not exist at {folder.as_posix()}"
            
        md_files = sorted(list(folder.glob("*.md")))
        if not md_files:
            return "No Markdown files found in the context folder."
            
        output = []
        for file_path in md_files:
            output.append(f"================================================================================")
            output.append(f"FILE: {file_path.name}")
            output.append(f"================================================================================")
            output.append(file_path.read_text(encoding="utf-8"))
            output.append(f"\n") # Add extra newline after content
            
        return "\n".join(output)
    except Exception as e:
        return f"Error reading all context files:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def remove_context_file(filename: str) -> str:
    """
    Removes a specific Markdown file from the context folder.

    Args:
        filename (str): The name of the file to remove.

    Returns:
        str: A success message or an error message.
    """
    try:
        folder = _get_context_folder()
        file_path = folder / filename
        
        if not file_path.exists():
            return f"Error: File '{filename}' not found in {folder.as_posix()}"
        if not file_path.is_file():
            return f"Error: '{filename}' is not a file."
            
        file_path.unlink()
        return f"Successfully removed context file: {filename} (from {folder.as_posix()})"
    except Exception as e:
        return f"Error removing context file:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
