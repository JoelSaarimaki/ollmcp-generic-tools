# --- safe-filesystem-mcp.py ---
import base64
import datetime
import hashlib
import json
import os
import shutil
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Safe-Filesystem-Server")

ALLOWED_DIR = Path(os.getenv("ALLOWED_DIR", os.getcwd())).resolve()

@dataclass
class FileMetadata:
    path: str
    sha256: str
    size_bytes: int
    line_ending: str
    encoding: str
    has_bom: bool
    modified_at: str

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

def _is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a forbidden file or is located within a forbidden folder.
    """
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(resolved.is_relative_to(forbidden) for forbidden in FORBIDDEN_PATHS)

def _contains_forbidden(path: Path) -> bool:
    """
    Checks if the given directory contains any forbidden files or folders.
    Used to stop recursive operations (delete, move) from touching forbidden paths.
    """
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(forbidden.is_relative_to(resolved) for forbidden in FORBIDDEN_PATHS)

def _display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

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
    if _is_forbidden(path):
        message = f"{label} is forbidden: {path}"
    else:
        message = f"{label} is not within the allowed directory: {ALLOWED_DIR}"
    return json.dumps({"success": False, "error": "access_denied", "message": message}, indent=2)

def _get_file_info(path: Path) -> FileMetadata:
    """
    Reads a file and returns its metadata: sha256, encoding, line ending, size and BOM.
    Raises FileNotFoundError if the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "rb") as f:
        raw_data = f.read()

    sha256 = hashlib.sha256(raw_data).hexdigest()
    size_bytes = len(raw_data)

    # Detect BOM
    has_bom = raw_data.startswith(b"\xef\xbb\xbf")
    decode_encoding = "utf-8-sig" if has_bom else "utf-8"

    try:
        raw_data.decode(decode_encoding)
    except UnicodeDecodeError:
        raise Exception(f"Could not decode file {path} using UTF-8.")

    # Detect line endings, defaulting to LF
    line_ending = "CRLF" if b"\r\n" in raw_data else "LF"

    mtime = os.path.getmtime(path)
    modified_at = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).isoformat()

    return FileMetadata(
        path=str(path),
        sha256=sha256,
        size_bytes=size_bytes,
        line_ending=line_ending,
        encoding="utf-8",
        has_bom=has_bom,
        modified_at=modified_at
    )

def _write_logic(path: Path, content: str, expected_sha256: str) -> str:
    """
    Validates the file hash, then writes the content atomically while keeping the
    original line endings and BOM. Returns a JSON response with the new metadata.
    """
    if not path.exists():
        return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {path}"}, indent=2)

    # 1. Read metadata to validate
    try:
        info = _get_file_info(path)
    except Exception as e:
        return json.dumps({"success": False, "error": "read_error", "message": str(e)}, indent=2)

    # 2. Hash validation
    if info.sha256 != expected_sha256:
        return json.dumps({
            "success": False,
            "error": "hash_mismatch",
            "message": "File changed since it was read.",
            "current_sha256": info.sha256
        }, indent=2)

    # 3. Normalize all line endings to LF, then restore the original line endings
    normalized_content = _normalize_newlines(content)
    if info.line_ending == "CRLF":
        normalized_content = normalized_content.replace("\n", "\r\n")

    # 4. Atomic write
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        # Using 'utf-8-sig' if BOM is present handles writing the BOM automatically
        write_encoding = "utf-8-sig" if info.has_bom else "utf-8"

        with open(temp_path, "w", encoding=write_encoding, newline="") as f:
            f.write(normalized_content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)

        new_info = _get_file_info(path)
        return json.dumps({"success": True, **asdict(new_info)}, indent=2)
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        return json.dumps({"success": False, "error": "write_error", "message": str(e)}, indent=2)

def _normalize_newlines(text: str) -> str:
    """
    Converts all line endings to LF.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")

def _line_number(content: str, index: int) -> int:
    """
    Returns the 1-based line number of the character at index.
    """
    return content.count("\n", 0, index) + 1

def _find_whitespace_insensitive_match(content: str, old_text: str) -> int | None:
    """
    Returns the line number where old_text appears when the indentation and trailing whitespace
    of each line are ignored, or None if it does not appear.
    """
    stripped_content = "\n".join(line.strip() for line in content.split("\n"))
    stripped_old = "\n".join(line.strip() for line in old_text.split("\n")).strip("\n")
    if not stripped_old:
        return None
    index = stripped_content.find(stripped_old)
    return None if index == -1 else _line_number(stripped_content, index)

def _edit_logic(path: Path, old_text: str, new_text: str, expected_sha256: str, replace_all: bool) -> str:
    """
    Validates the file hash, replaces old_text with new_text and writes the file with _write_logic.
    Line endings are ignored when matching. old_text must match exactly once unless replace_all is set.
    """
    if not path.exists():
        return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {path}"}, indent=2)

    try:
        info = _get_file_info(path)
    except Exception as e:
        return json.dumps({"success": False, "error": "read_error", "message": str(e)}, indent=2)

    if info.sha256 != expected_sha256:
        return json.dumps({
            "success": False,
            "error": "hash_mismatch",
            "message": "File changed since it was read.",
            "current_sha256": info.sha256
        }, indent=2)

    old = _normalize_newlines(old_text)
    new = _normalize_newlines(new_text)
    if not old:
        return json.dumps({"success": False, "error": "empty_old_text", "message": "old_text must not be empty. Use write_file to replace the whole file."}, indent=2)
    if old == new:
        return json.dumps({"success": False, "error": "no_change", "message": "old_text and new_text are identical."}, indent=2)

    with open(path, "r", encoding="utf-8-sig" if info.has_bom else "utf-8", newline="") as f:
        content = _normalize_newlines(f.read())

    count = content.count(old)
    if count == 0:
        message = "old_text was not found in the file."
        line = _find_whitespace_insensitive_match(content, old)
        if line:
            message += f" A match with different indentation or trailing whitespace starts at line {line}. Copy the text exactly as it appears in the file."
        else:
            message += " Read the file again and copy the text exactly as it appears in the file."
        return json.dumps({"success": False, "error": "no_match", "message": message}, indent=2)

    if count > 1 and not replace_all:
        lines = []
        index = content.find(old)
        while index != -1:
            lines.append(_line_number(content, index))
            index = content.find(old, index + len(old))
        return json.dumps({
            "success": False,
            "error": "multiple_matches",
            "message": f"old_text matches {count} times, at lines {lines}. Include more surrounding lines in old_text to make it unique, or set replace_all to replace every match.",
            "match_lines": lines
        }, indent=2)

    first_line = _line_number(content, content.find(old))
    new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)

    result = json.loads(_write_logic(path, new_content, expected_sha256))
    if result.get("success"):
        result["replacements"] = count if replace_all else 1
        result["first_changed_line"] = first_line
    return json.dumps(result, indent=2)

def _detect_neighbor_line_ending(directory: Path) -> tuple[str, str]:
    """
    Picks the majority line ending of the allowed files in the directory, defaulting to LF.
    Returns the newline string and a note to add to the response message.
    """
    counts = {"CRLF": 0, "LF": 0}
    if directory.exists():
        for neighbor in directory.iterdir():
            if neighbor.is_file() and _is_path_allowed(neighbor):
                try:
                    with open(neighbor, "rb") as f:
                        chunk = f.read(4096)
                        if b"\r\n" in chunk:
                            counts["CRLF"] += 1
                        elif b"\n" in chunk:
                            counts["LF"] += 1
                except Exception:
                    continue

    if counts["CRLF"] > counts["LF"]:
        return "\r\n", ""
    if counts["LF"] > counts["CRLF"]:
        return "\n", ""
    if counts["CRLF"] > 0:
        return "\n", " (Note: Line ending tie detected among neighboring files. Defaulted to LF.)"
    return "\n", " (Note: No neighboring files or no clear majority, defaulting to LF)"

# --- Public MCP Tools ---

@mcp.tool()
def write_file(path: str, content: str, expected_sha256: str) -> str:
    """
    Safely updates an existing file by replacing its whole content.
    It checks if the file's current SHA-256 hash matches the expected_sha256
    to ensure no one else has modified it since you last read it.
    To change only part of a file, use 'edit_file' instead.

    IMPORTANT: If you receive a 'hash_mismatch' error, it means the file has
    changed on disk. You MUST call 'read_file_with_metadata' to get the
    new content and the new SHA-256 before attempting to write again.

    Args:
        path (str): Path to the file to update.
        content (str): The new content to write.
        expected_sha256 (str): The SHA-256 hash of the file as it was when last read.

    Returns:
        str: JSON response indicating success or error (e.g., hash_mismatch).
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        return _write_logic(p, content, expected_sha256)
    except Exception as e:
        return json.dumps({"success": False, "error": "write_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def edit_file(path: str, old_text: str, new_text: str, expected_sha256: str, replace_all: bool = False) -> str:
    """
    Changes part of an existing file by replacing old_text with new_text.
    Prefer this over write_file when changing only part of a file.
    It checks if the file's current SHA-256 hash matches the expected_sha256
    to ensure no one else has modified it since you last read it.

    Copy old_text exactly from the file content returned by 'read_file_with_metadata',
    including indentation. Line endings do not need to match. old_text must match exactly
    once: include enough surrounding lines to make it unique, or set replace_all to True.
    To delete text, use an empty new_text.

    The response contains the file's new SHA-256. Use it as expected_sha256 for the next
    edit to the same file, without reading the file again.

    IMPORTANT: If you receive a 'hash_mismatch' error, it means the file has
    changed on disk. You MUST call 'read_file_with_metadata' to get the
    new content and the new SHA-256 before attempting to edit again.

    Args:
        path (str): Path to the file to edit.
        old_text (str): The exact text to replace.
        new_text (str): The text to replace it with.
        expected_sha256 (str): The SHA-256 hash of the file as it was when last read or written.
        replace_all (bool): If True, replaces every match of old_text. Defaults to False.

    Returns:
        str: JSON response with the new file metadata, the number of replacements and the first
            changed line, or an error (e.g., hash_mismatch, no_match, multiple_matches).
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        return _edit_logic(p, old_text, new_text, expected_sha256, replace_all)
    except Exception as e:
        return json.dumps({"success": False, "error": "edit_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def create_file(path: str, content: str) -> str:
    """
    Creates a new file with the provided content.
    Fails if the file already exists.

    Args:
        path (str): Path to the new file.
        content (str): Content to write to the file.

    Returns:
        str: JSON response indicating success or error.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        if p.exists():
            return json.dumps({"success": False, "error": "file_exists", "message": "File already exists."}, indent=2)

        target_newline, note = _detect_neighbor_line_ending(p.parent)

        normalized_content = _normalize_newlines(content)
        if target_newline == "\r\n":
            normalized_content = normalized_content.replace("\n", "\r\n")

        p.parent.mkdir(parents=True, exist_ok=True)

        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(normalized_content)

        message = f"File created successfully.{note}"
        try:
            new_info = _get_file_info(p)
            return json.dumps({"success": True, **asdict(new_info), "message": message}, indent=2)
        except Exception:
            # Fallback if metadata retrieval fails
            return json.dumps({"success": True, "message": message}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "create_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def read_file_with_metadata(path: str) -> str:
    """
    Reads a file and returns its content along with SHA-256 hash, encoding, line ending, and size.

    MANDATORY WORKFLOW:
    1. Read the file with this tool.
    2. Decide on the change.
    3. Use the returned SHA-256 when calling edit_file (to change part of the file)
       or write_file (to replace the whole file).

    Never guess SHA-256 values.
    Never edit files without reading them first.

    Args:
        path (str): Path to the file.

    Returns:
        str: JSON string containing file content and metadata, or error message.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        info = _get_file_info(p)
        with open(p, "r", encoding="utf-8-sig" if info.has_bom else "utf-8") as f:
            content = f.read()

        return json.dumps({"success": True, **asdict(info), "content": content}, indent=2, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"success": False, "error": "file_not_found", "message": str(e)}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "read_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def read_image_as_base64(path: str) -> str:
    """
    Reads an image file and returns its content as a base64 encoded string.
    Useful for visual analysis of UI components or assets.

    Args:
        path (str): Path to the image file.

    Returns:
        str: JSON string containing the base64 string, file metadata, or error message.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        if not p.exists():
            return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {p}"}, indent=2)

        with open(p, "rb") as f:
            binary_data = f.read()

        base64_data = base64.b64encode(binary_data).decode("utf-8")

        # Simplified metadata, since the content is not decoded as text
        stats = p.stat()
        info = {
            "path": str(p),
            "size_bytes": stats.st_size,
            "modified_at": datetime.datetime.fromtimestamp(stats.st_mtime, tz=datetime.timezone.utc).isoformat(),
            "mime_type": "image/unknown"
        }

        return json.dumps({"success": True, "metadata": info, "base64": base64_data}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "read_image_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def get_file_stats(path: str) -> str:
    """
    Retrieves metadata about a file without reading its content.
    This is useful for checking if a file has changed before deciding to read it.

    Args:
        path (str): Path to the file.

    Returns:
        str: JSON string containing file metadata, or error message.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)
        if not p.exists():
            return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {p}"}, indent=2)

        info = _get_file_info(p)
        return json.dumps({"success": True, **asdict(info)}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "stats_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def list_directory(path: str) -> str:
    """
    Lists all files and directories within the specified path.

    Args:
        path (str): The directory to list.

    Returns:
        str: JSON string containing a list of entries, distinguishing between [FILE] and [DIR].
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)
        if not p.is_dir():
            return json.dumps({"success": False, "error": "not_a_directory", "message": f"{p} is not a directory."}, indent=2)

        entries = []
        for entry in p.iterdir():
            if not _is_path_allowed(entry):
                continue
            try:
                info = entry.stat()
                entries.append({
                    "name": entry.name,
                    "type": "DIR" if entry.is_dir() else "FILE",
                    "size_bytes": info.st_size,
                    "modified_at": datetime.datetime.fromtimestamp(info.st_mtime, tz=datetime.timezone.utc).isoformat()
                })
            except Exception as e:
                entries.append({
                    "name": entry.name,
                    "type": "UNKNOWN",
                    "error": str(e)
                })

        return json.dumps({"success": True, "entries": entries}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "list_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def create_directory(path: str) -> str:
    """
    Creates a new directory at the specified path. Supports creating parent directories.

    Args:
        path (str): The path of the directory to create.

    Returns:
        str: JSON success/error message.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)

        if p.exists() and not p.is_dir():
            return json.dumps({"success": False, "error": "file_exists", "message": "A file already exists at this path."}, indent=2)

        p.mkdir(parents=True, exist_ok=True)
        return json.dumps({"success": True, "message": f"Directory created: {p}"}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "create_dir_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """
    Moves or renames a file or directory from the source to the destination.

    Args:
        source (str): Current path of the file/directory.
        destination (str): New path of the file/directory.

    Returns:
        str: JSON success/error message.
    """
    try:
        src = Path(source).resolve()
        dst = Path(destination).resolve()

        if not _is_path_allowed(src):
            return _access_denied(src, "Source path")
        if not _is_path_allowed(dst):
            return _access_denied(dst, "Destination path")
        if _contains_forbidden(src):
            return json.dumps({"success": False, "error": "access_denied", "message": f"Source path contains forbidden files or folders: {src}"}, indent=2)

        if not src.exists():
            return json.dumps({"success": False, "error": "source_not_found", "message": f"Source not found: {src}"}, indent=2)
        if dst.exists():
            return json.dumps({"success": False, "error": "destination_exists", "message": f"Destination already exists: {dst}"}, indent=2)

        shutil.move(str(src), str(dst))
        return json.dumps({"success": True, "message": f"Moved {src} to {dst}"}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "move_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def delete_file(path: str) -> str:
    """
    Deletes a file or a directory (and its contents if it's a directory).

    Args:
        path (str): Path to the file/directory to be deleted.

    Returns:
        str: JSON success/error message.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return _access_denied(p)
        if _contains_forbidden(p):
            return json.dumps({"success": False, "error": "access_denied", "message": f"Path contains forbidden files or folders: {p}"}, indent=2)
        if not p.exists():
            return json.dumps({"success": False, "error": "not_found", "message": f"Path not found: {p}"}, indent=2)

        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

        return json.dumps({"success": True, "message": f"Deleted: {p}"}, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": "delete_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def get_filesystem_config() -> str:
    """
    Returns the configuration that decides which paths the filesystem tools can access:
    the allowed directory, forbidden paths and the working directory that relative paths
    are resolved against.
    Use this tool to find out why a path is denied or not found by the filesystem tools.

    Returns:
        str: A JSON-formatted string containing the configuration or an error message.
    """
    try:
        return json.dumps({
            "success": True,
            "allowed_dir": ALLOWED_DIR.as_posix(),
            "allowed_dir_exists": ALLOWED_DIR.exists(),
            "working_directory": Path.cwd().resolve().as_posix(),
            "ignored_dirs": [],
            "forbidden_paths": [_display_path(p, ALLOWED_DIR) for p in FORBIDDEN_PATHS],
            "gitignore": {
                "applied": False
            },
            "notes": [
                "Only paths within allowed_dir can be accessed.",
                "Relative paths given to the tools are resolved against working_directory, not allowed_dir.",
                "Forbidden paths and everything inside forbidden folders are denied and hidden from list_directory.",
                "Deleting or moving a folder that contains a forbidden path is denied.",
                "There are no ignored directories and .gitignore is not applied, so folders such as .git are accessible unless forbidden."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

if __name__ == "__main__":
    mcp.run()
