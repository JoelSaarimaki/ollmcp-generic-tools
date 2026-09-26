# --- safe-filesystem-mcp.py ---
import hashlib
import os
import traceback
import json
import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("SafeWrite-Server")
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

def _is_path_allowed(path: Path) -> bool:
    """
    Checks if the given path is within the ALLOWED_DIR.
    """
    try:
        return path.resolve().is_relative_to(ALLOWED_DIR)
    except ValueError:
        return False

def _get_file_info(path: Path) -> FileMetadata:
    """
    Reads file content and returns metadata: sha256, encoding, line_ending, size_bytes, and has_bom.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, 'rb') as f:
        raw_data = f.read()
        
    sha256 = hashlib.sha256(raw_data).hexdigest()
    size_bytes = len(raw_data)
    
    # Detect BOM
    has_bom = raw_data.startswith(b'\xef\xbb\xbf')
    decode_encoding = "utf-8-sig" if has_bom else "utf-8"
    
    try:
        content = raw_data.decode(decode_encoding)
    except UnicodeDecodeError:
        raise Exception(f"Could not decode file {path} using UTF-8.")

    # Detect line endings
    if b"\r\n" in raw_data:
        line_ending = "CRLF"
    elif b"\n" in raw_data:
        line_ending = "LF"
    else:
        line_ending = "LF"  # Default
        
    # Get modification time
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

def _safe_write_logic(path: Path, content: str, expected_sha256: str) -> str:
    """
    Performs the safe write operation with hash validation and atomic replacement.
    Returns metadata about the written file.
    """
    if not path.exists():
        return json.dumps({"success": False, "error": "file_not_found"})

    # 1. Read metadata to validate
    try:
        info = _get_file_info(path)
        current_sha256 = info.sha256
        original_line_ending = info.line_ending
        has_bom = info.has_bom
    except Exception as e:
        return json.dumps({"success": False, "error": "read_error", "message": str(e)})

    # 2. Hash Validation
    if current_sha256 != expected_sha256:
        return json.dumps({
            "success": False, 
            "error": "hash_mismatch", 
            "message": "File changed since it was read.",
            "current_sha256": current_sha256
        })

    # 3. Prepare content (Restore original line endings and BOM)
    # First, normalize all line endings to LF
    normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
    
    if original_line_ending == "CRLF":
        normalized_content = normalized_content.replace("\n", "\r\n")

    # 4. Atomic Write
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        # Using 'utf-8-sig' if BOM is present handles writing the BOM automatically
        write_encoding = "utf-8-sig" if has_bom else "utf-8"
        
        with open(temp_path, 'w', encoding=write_encoding, newline='') as f:
            f.write(normalized_content)
            f.flush()
            os.fsync(f.fileno())
        
        # Replace original with temp
        os.replace(temp_path, path)
        
        # Get new metadata for response
        new_info = _get_file_info(path)
        
        return json.dumps({
            "success": True,
            "path": new_info.path,
            "sha256": new_info.sha256,
            "size_bytes": new_info.size_bytes,
            "line_ending": new_info.line_ending,
            "encoding": new_info.encoding,
            "has_bom": new_info.has_bom,
            "modified_at": new_info.modified_at
        })
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        return json.dumps({"success": False, "error": "write_error", "message": str(e)})

# --- Public MCP Tools ---

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
            return json.dumps({"success": False, "error": "access_denied", "message": f"Path is not within the allowed directory: {ALLOWED_DIR}"})
        
        if p.exists():
            return json.dumps({"success": False, "error": "file_exists", "message": "File already exists."})

        # Determine target line ending
        target_newline = "\n"
        warning = ""
        
        # Check Neighbors
        neighbor_le_counts = {"CRLF": 0, "LF": 0}
        if p.parent.exists():
            for neighbor in p.parent.iterdir():
                if neighbor.is_file():
                    try:
                        with open(neighbor, 'rb') as f:
                            chunk = f.read(4096) # Read a bit more to be sure
                            if b"\r\n" in chunk:
                                neighbor_le_counts["CRLF"] += 1
                            elif b"\n" in chunk:
                                neighbor_le_counts["LF"] += 1
                    except:
                        continue
        
        if neighbor_le_counts["CRLF"] > neighbor_le_counts["LF"]:
            target_newline = "\r\n"
        elif neighbor_le_counts["LF"] > neighbor_le_counts["CRLF"]:
            target_newline = "\n"
        elif neighbor_le_counts["CRLF"] == neighbor_le_counts["LF"] and neighbor_le_counts["CRLF"] > 0:
            # Tied, use LF as per spec
            target_newline = "\n"
            warning = " (Note: Line ending tie detected among neighboring files. Defaulted to LF.)"
        else:
            # Default to LF
            target_newline = "\n"
            warning = " (Note: No neighboring files or no clear majority, defaulting to LF)"

        # Normalize content to the target newline
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        if target_newline == "\r\n":
            normalized_content = normalized_content.replace("\n", "\r\n")

        # Ensure parent directory exists
        p.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write(normalized_content)

        # Get metadata for response
        try:
            new_info = _get_file_info(p)
            return json.dumps({
                "success": True,
                "path": new_info.path,
                "sha256": new_info.sha256,
                "size_bytes": new_info.size_bytes,
                "line_ending": new_info.line_ending,
                "encoding": new_info.encoding,
                "has_bom": new_info.has_bom,
                "modified_at": new_info.modified_at,
                "message": f"File created successfully.{warning}"
            })
        except Exception as e:
            # Fallback if metadata retrieval fails
            return json.dumps({
                "success": True, 
                "message": f"File created successfully.{warning}"
            })

    except Exception as e:
        return json.dumps({"success": False, "error": "create_error", "message": str(e), "traceback": traceback.format_exc()})

@mcp.tool()
def read_file_with_metadata(path: str) -> str:
    """
    Reads a file and returns its content along with SHA-256 hash, encoding, line ending, and size.
    
    MANDATORY WORKFLOW:
    1. Read the file with this tool.
    2. Modify the content locally.
    3. Use the returned SHA-256 when calling safe_write_file.

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
            return json.dumps({"error": "access_denied", "message": f"Path is not within the allowed directory: {ALLOWED_DIR}"})
        info = _get_file_info(p)
        with open(p, 'r', encoding='utf-8-sig' if info.has_bom else 'utf-8') as f:
            content = f.read()
        
        response = asdict(info)
        response['content'] = content
        return json.dumps(response, ensure_ascii=False)
    except FileNotFoundError as e:
        return json.dumps({"error": "file_not_found", "message": str(e)})
    except Exception as e:
        return json.dumps({"error": "read_error", "message": str(e), "traceback": traceback.format_exc()})

@mcp.tool()
def safe_write_file(path: str, content: str, expected_sha256: str) -> str:
    """
    Safely writes content to a file after validating the SHA-256 hash to prevent accidental overwrites.
    Uses an atomic write strategy.

    When hash_mismatch occurs:
    1. Read the file again.
    2. Reapply changes.
    3. Retry the write.
    Never attempt to force an overwrite.

    Args:
        path (str): Path to the file being modified.
        content (str): The complete new contents of the file.
        expected_sha256 (str): The SHA-256 hash returned from the most recent read operation.

    Returns:
        str: JSON response indicating success or error.
    """
    try:
        p = Path(path).resolve()
        if not _is_path_allowed(p):
            return json.dumps({"success": False, "error": "access_denied", "message": f"Path is not within the allowed directory: {ALLOWED_DIR}"})
        return _safe_write_logic(p, content, expected_sha256)
    except Exception as e:
        return json.dumps({"success": False, "error": "unexpected_error", "message": str(e), "traceback": traceback.format_exc()})

if __name__ == "__main__":
    mcp.run()
