# --- safe-filesystem-mcp.py ---
import datetime
import hashlib
import json
import os
import re
import shutil
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

# --- Constants & Config ---
mcp = MCPServer("Safe-Filesystem-Server")

ALLOWED_DIR = Path(os.getenv("ALLOWED_DIR", os.getcwd())).resolve()
PROTECTED_FILE_NAMES = {".mcp.json"}  # MCP server configuration, never accessible regardless of FORBIDDEN_PATHS
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB limit
# Largest response size in characters (about 4 characters per token). Lower it for models with small context windows.
MAX_OUTPUT_CHARS = max(2000, int(os.getenv("MAX_OUTPUT_CHARS") or 40000))
MAX_READ_LINES = 1000  # Longer files are read in parts
MAX_LINE_CHARS = 2000  # Longer lines (e.g. minified code) are shown shortened, ending with SHORTENED_LINE_MARK
SHORTENED_LINE_MARK = " [...]"
MAX_TEXT_FILE_BYTES = 50 * 1024 * 1024  # Larger text files are not read
MAX_LIST_ENTRIES = 250
EDIT_SNIPPET_CONTEXT_LINES = 3  # Lines shown around a change after an edit
MAX_EDIT_SNIPPET_LINES = 40
SHRINK_WARNING_RATIO = 0.5  # write_file warns if a file of 20+ lines shrinks below this ratio
CONTENT_START = "----- BEGIN CONTENT -----"
CONTENT_END = "----- END CONTENT -----"
# 'N| ' prefixes of read_file_with_metadata(line_numbers=True), removed if copied into edit_file
LINE_NUMBER_PREFIX = re.compile(r"^ *\d+\| ?")
# Comments that stand in for code, e.g. '// ... existing code ...', which would replace real code if written
PLACEHOLDER_COMMENT = re.compile(
    r"(?:#|//|/\*|<!--|--)[ \t]*(?:\.\.\.|…)?[ \t]*\(?[ \t]*"
    r"(?:rest of (?:the )?(?:code|file|implementation|function|class|component|module|content|methods)"
    r"|existing (?:code|content|implementation|methods|imports)"
    r"|(?:code|content|everything else|the rest|other methods) (?:remains?|stays?|is) (?:the same|unchanged)"
    r"|unchanged (?:code|content|methods)|same as (?:before|above)|previous (?:code|content|implementation))",
    re.IGNORECASE
)

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

def _contains_forbidden(path: Path) -> bool:
    """
    Checks if the given directory contains any forbidden or protected files or folders.
    Used to stop recursive operations (delete, move) from touching forbidden or protected paths.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if any(forbidden.is_relative_to(resolved) for forbidden in FORBIDDEN_PATHS):
        return True
    if resolved.is_dir():
        for dirpath, _, filenames in os.walk(resolved):
            if any(_is_protected(Path(dirpath) / name) for name in filenames):
                return True
    return False

def _display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

def _resolve_path(path: str) -> Path:
    """
    Resolves a path given to a tool. Relative paths are resolved against ALLOWED_DIR, as in all servers.
    """
    p = Path(path)
    return (p if p.is_absolute() else ALLOWED_DIR / p).resolve()

def _is_path_allowed(path: Path) -> bool:
    """
    Checks if the given path is within ALLOWED_DIR and not forbidden.
    """
    try:
        return path.resolve().is_relative_to(ALLOWED_DIR) and not _is_forbidden(path)
    except (ValueError, OSError):
        return False

def _access_denied_message(path: Path, label: str = "Path") -> str:
    """
    Returns the reason why access to the given path was denied.
    """
    if _is_protected(path):
        return f"{label} is protected: '{path.name}' files contain the MCP server configuration and cannot be accessed."
    if _is_forbidden(path):
        return f"{label} is forbidden: {path}"
    return f"{label} is not within the allowed directory: {ALLOWED_DIR}"

def _access_denied(path: Path, label: str = "Path") -> str:
    """
    Returns the JSON access_denied response, stating why access to the given path was denied.
    """
    return json.dumps({"success": False, "error": "access_denied", "message": _access_denied_message(path, label)}, indent=2)

def _text_error(error: str, message: str) -> str:
    """
    Returns a plain-text error response, used by the tools whose responses contain file content.
    """
    return f"Error ({error}): {message}"

class _EditError(Exception):
    """
    Raised when an edit cannot be applied, with an error code and a message for the AI.
    """
    def __init__(self, error: str, message: str):
        super().__init__(message)
        self.error = error
        self.message = message

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
        raise Exception(f"{path} is not a UTF-8 text file. For images, use 'read_image'.")

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

def _detect_image_format(data: bytes) -> str | None:
    """
    Returns the image format ('png', 'jpeg', 'gif' or 'webp') based on the file's first bytes,
    or None if the data is not a supported image.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None

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

def _read_text(path: Path, has_bom: bool) -> str:
    """
    Reads a UTF-8 text file with its line endings converted to LF.
    """
    with open(path, "r", encoding="utf-8-sig" if has_bom else "utf-8", newline="") as f:
        return _normalize_newlines(f.read())

def _split_lines(content: str) -> list[str]:
    """
    Splits content into lines, without an empty last line for a final line break.
    """
    lines = content.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines

def _shorten_lines(lines: list[str], first_line: int) -> tuple[list[str], list[int]]:
    """
    Shortens lines longer than MAX_LINE_CHARS, ending them with SHORTENED_LINE_MARK.
    Returns the lines and the line numbers of the shortened lines.
    """
    shortened = []
    result = []
    for i, line in enumerate(lines):
        if len(line) > MAX_LINE_CHARS:
            shortened.append(first_line + i)
            line = line[:MAX_LINE_CHARS] + SHORTENED_LINE_MARK
        result.append(line)
    return result, shortened

def _shortened_lines_note(line_numbers: list[int]) -> str:
    """
    Returns the instruction shown when lines were shortened, or an empty string.
    """
    if not line_numbers:
        return ""
    listed = ", ".join(str(n) for n in line_numbers[:10]) + (", ..." if len(line_numbers) > 10 else "")
    subject = f"line {listed} is" if len(line_numbers) == 1 else f"lines {listed} are"
    return (f"Output limited: {subject} longer than {MAX_LINE_CHARS} characters and shown shortened, ending with '{SHORTENED_LINE_MARK.strip()}'."
            f" To edit such a line, use a unique part of the shown text as old_text in edit_file; the hidden rest of the line stays unchanged.")

def _content_block(lines: list[str], first_line: int, line_numbers: bool = False) -> str:
    """
    Returns the lines between the content markers, optionally prefixed with 'N| ' line numbers.
    """
    if line_numbers:
        width = len(str(first_line + len(lines) - 1))
        lines = [f"{first_line + i:>{width}}| {line}" for i, line in enumerate(lines)]
    return "\n".join([CONTENT_START, *lines, CONTENT_END])

def _find_placeholder(new_text: str, original: str) -> str | None:
    """
    Returns a placeholder comment such as '// ... existing code ...' found in new_text,
    unless the original content already contains one.
    """
    match = PLACEHOLDER_COMMENT.search(new_text)
    if match and not PLACEHOLDER_COMMENT.search(original):
        return match.group(0).strip()
    return None

def _strip_line_numbers(text: str) -> str | None:
    """
    Removes 'N| ' line number prefixes if every non-empty line has one, otherwise returns None.
    """
    lines = text.split("\n")
    numbered = [line for line in lines if line.strip()]
    if not numbered or not all(LINE_NUMBER_PREFIX.match(line) for line in numbered):
        return None
    return "\n".join(LINE_NUMBER_PREFIX.sub("", line, count=1) for line in lines)

def _find_line_matches(lines: list[str], old_lines: list[str], strip) -> list[int]:
    """
    Returns the 0-based indexes where old_lines match consecutive lines, comparing lines with strip.
    """
    n = len(old_lines)
    targets = [strip(line) for line in old_lines]
    return [i for i in range(len(lines) - n + 1) if all(strip(lines[i + k]) == targets[k] for k in range(n))]

def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> tuple[str, int, int, int, str]:
    """
    Replaces old with new in content and returns (new content, first changed line, last changed line,
    number of replacements, note). If old does not match exactly, it is retried without copied
    line number prefixes, and then line by line ignoring trailing whitespace.
    Raises _EditError if old matches nowhere, or more than once without replace_all.
    """
    note = ""
    if old not in content:
        stripped = _strip_line_numbers(old)
        if stripped and stripped in content:
            old, new = stripped, _strip_line_numbers(new) or new
            note = " Line number prefixes were removed from old_text."

    count = content.count(old)
    if count:
        starts = []
        index = content.find(old)
        while index != -1:
            starts.append(_line_number(content, index))
            index = content.find(old, index + len(old))
        if count > 1 and not replace_all:
            raise _EditError("multiple_matches", f"old_text matches {count} times, at lines {starts}. Include more surrounding lines in old_text to make it unique, or set replace_all to replace every match.")
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        first = starts[0]
        return new_content, first, first + new.count("\n"), count if replace_all else 1, note

    # Retry line by line, ignoring trailing whitespace, which models often drop or add
    lines = content.split("\n")
    old_lines = _split_lines(old)
    new_lines = [] if new == "" else (_split_lines(new) if old.endswith("\n") else new.split("\n"))
    matches = _find_line_matches(lines, old_lines, str.rstrip) if old_lines else []
    if len(matches) > 1 and not replace_all:
        raise _EditError("multiple_matches", f"old_text matches {len(matches)} times when trailing whitespace is ignored, at lines {[m + 1 for m in matches]}. Include more surrounding lines in old_text to make it unique, or set replace_all to replace every match.")
    if matches:
        for i in reversed(matches):
            lines[i:i + len(old_lines)] = new_lines
        first = matches[0] + 1
        note += " Matched ignoring trailing whitespace."
        return "\n".join(lines), first, first + max(len(new_lines), 1) - 1, len(matches), note

    # No match: show the lines that match when indentation is also ignored, so they can be copied exactly
    message = "old_text was not found in the file."
    near = _find_line_matches(lines, old_lines, str.strip) if old_lines else []
    if near:
        start = near[0]
        actual, shortened = _shorten_lines(lines[start:start + len(old_lines)][:MAX_EDIT_SNIPPET_LINES], start + 1)
        message += (f" Lines {start + 1}-{start + len(old_lines)} match when indentation is ignored."
                    f" Copy them exactly as they appear in the file, including indentation:\n"
                    f"{_content_block(actual, start + 1)}")
        if shortened or len(old_lines) > MAX_EDIT_SNIPPET_LINES:
            message += f"\nOutput limited: only part of these lines is shown. Read them with read_file_with_metadata(start_line={start + 1})."
    else:
        message += " Read the file again with 'read_file_with_metadata' and copy the text exactly as it appears in the file."
    raise _EditError("no_match", message)

def _edit_logic(path: Path, old_text: str, new_text: str, expected_sha256: str, replace_all: bool) -> str:
    """
    Validates the file hash, replaces old_text with new_text and writes the file with _write_logic.
    Returns a plain-text response with the new SHA-256 and the changed lines, or an error.
    """
    if not path.is_file():
        return _text_error("file_not_found", f"File not found: {path}")

    try:
        info = _get_file_info(path)
    except Exception as e:
        return _text_error("read_error", str(e))

    if info.sha256 != expected_sha256:
        return _text_error("hash_mismatch", f"File changed since it was read. Current SHA-256: {info.sha256}. Read the file again with 'read_file_with_metadata' before editing it.")

    old = _normalize_newlines(old_text)
    new = _normalize_newlines(new_text)
    if not old:
        return _text_error("empty_old_text", "old_text must not be empty. Use write_file to replace the whole file.")
    if old == new:
        return _text_error("no_change", "old_text and new_text are identical.")

    content = _read_text(path, info.has_bom)
    placeholder = _find_placeholder(new, content)
    if placeholder:
        return _text_error("placeholder_in_new_text", f"new_text contains the placeholder comment '{placeholder}'. Text is written literally, so the placeholder would replace real code. Write out the full code in new_text, or make old_text cover only the lines you change.")

    try:
        new_content, first_line, last_line, replacements, note = _apply_edit(content, old, new, replace_all)
    except _EditError as e:
        return _text_error(e.error, e.message)

    result = json.loads(_write_logic(path, new_content, expected_sha256))
    if not result.get("success"):
        return _text_error(result.get("error", "write_error"), result.get("message", ""))

    new_lines = _split_lines(new_content)
    snippet_start = max(1, first_line - EDIT_SNIPPET_CONTEXT_LINES)
    snippet_end = min(len(new_lines), last_line + EDIT_SNIPPET_CONTEXT_LINES, snippet_start + MAX_EDIT_SNIPPET_LINES - 1)
    plural = "replacement" if replacements == 1 else "replacements"
    snippet, shortened = _shorten_lines(new_lines[snippet_start - 1:snippet_end], snippet_start)
    limited = []
    if last_line + EDIT_SNIPPET_CONTEXT_LINES > snippet_end and snippet_end < len(new_lines):
        limited.append(f"Output limited: the change continues after line {snippet_end}. Check the rest with read_file_with_metadata(start_line={snippet_end + 1}) if needed.")
    if shortened:
        limited.append(_shortened_lines_note(shortened))
    return "\n".join([
        f"Edited {path}: {replacements} {plural}, starting at line {first_line}.{note}",
        f"New SHA-256: {result['sha256']} (use it as expected_sha256 for the next edit to this file)",
        *limited,
        f"Lines {snippet_start}-{snippet_end} of {len(new_lines)} after the edit:" if new_lines else "The file is now empty.",
        _content_block(snippet, snippet_start) if new_lines else ""
    ]).rstrip()

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

    The content must be the complete new file. Never use placeholder comments such as
    '// ... existing code ...': the content is written literally, so they are rejected.

    IMPORTANT: If you receive a 'hash_mismatch' error, it means the file has
    changed on disk. You MUST call 'read_file_with_metadata' to get the
    new content and the new SHA-256 before attempting to write again.

    Args:
        path (str): Path to the file to update.
        content (str): The complete new content of the file.
        expected_sha256 (str): The SHA-256 hash of the file as it was when last read.

    Returns:
        str: JSON response indicating success or error (e.g., hash_mismatch), with a warning
            if the file became much shorter.
    """
    try:
        p = _resolve_path(path)
        if not _is_path_allowed(p):
            return _access_denied(p)

        if p.is_file():
            try:
                info = _get_file_info(p)
                original = _read_text(p, info.has_bom)
            except Exception as e:
                return json.dumps({"success": False, "error": "read_error", "message": str(e)}, indent=2)

            placeholder = _find_placeholder(_normalize_newlines(content), original)
            if placeholder:
                return json.dumps({
                    "success": False,
                    "error": "placeholder_in_content",
                    "message": f"content contains the placeholder comment '{placeholder}'. The content is written literally, so the placeholder would replace real code. Write out the complete file, or use edit_file to change only part of it."
                }, indent=2)

            result = json.loads(_write_logic(p, content, expected_sha256))
            old_count, new_count = len(_split_lines(original)), len(_split_lines(_normalize_newlines(content)))
            if result.get("success") and old_count >= 20 and new_count < old_count * SHRINK_WARNING_RATIO:
                result["warning"] = f"The file shrank from {old_count} to {new_count} lines. If you meant to change only part of it, restore the missing lines: write_file replaces the whole file, while edit_file changes only part of it."
            return json.dumps(result, indent=2)

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
    including indentation, and without line number prefixes. Line endings and trailing
    whitespace do not need to match. old_text must match exactly once: include a few
    surrounding lines to make it unique, or set replace_all to True.
    To delete lines, use an empty new_text.

    new_text is written literally: never use placeholder comments such as
    '// ... existing code ...' in it. Keep old_text short, covering only the lines you change.

    The response shows the changed lines after the edit and the file's new SHA-256.
    Use the new SHA-256 as expected_sha256 for the next edit to the same file,
    without reading the file again.

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
        str: Plain text with the number of replacements, the new SHA-256 and the changed lines
            after the edit, or an error (e.g., hash_mismatch, no_match with the closest matching
            lines, multiple_matches with their line numbers).
    """
    try:
        p = _resolve_path(path)
        if not _is_path_allowed(p):
            return _text_error("access_denied", _access_denied_message(p))

        return _edit_logic(p, old_text, new_text, expected_sha256, replace_all)
    except Exception as e:
        return _text_error("edit_error", f"{e}\n{traceback.format_exc()}")

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
        p = _resolve_path(path)
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
def read_file_with_metadata(path: str, start_line: int | None = None, end_line: int | None = None, line_numbers: bool = False) -> str:
    """
    Reads a text file and returns its exact content along with its SHA-256 hash, line count,
    line ending, encoding and size. The content is shown as-is between the lines
    '----- BEGIN CONTENT -----' and '----- END CONTENT -----'.

    Long files are returned in parts of up to 1000 lines: the response then says which
    start_line to use to read the next part. Use start_line and end_line to read only the
    lines you need, e.g. around a line number found with a search tool.

    MANDATORY WORKFLOW:
    1. Read the file with this tool.
    2. Decide on the change.
    3. Use the returned SHA-256 when calling edit_file (to change part of the file)
       or write_file (to replace the whole file, only after reading the whole file).

    Never guess SHA-256 values.
    Never edit files without reading them first.

    Args:
        path (str): Path to the file.
        start_line (int, optional): The first line to read, starting from 1. Defaults to 1.
        end_line (int, optional): The last line to read. Defaults to the end of the file.
        line_numbers (bool): If True, prefixes each line with its number as 'N| '. The prefixes
            are not part of the content. Defaults to False.

    Returns:
        str: The file's metadata followed by its content as plain text, or an error message.
    """
    try:
        p = _resolve_path(path)
        if not _is_path_allowed(p):
            return _text_error("access_denied", _access_denied_message(p))
        if p.is_dir():
            return _text_error("is_a_directory", f"{p} is a directory. Use 'list_directory' to see its contents.")
        if not p.exists():
            return _text_error("file_not_found", f"File not found: {p}")

        size = p.stat().st_size
        if size > MAX_TEXT_FILE_BYTES:
            return _text_error("too_large", f"File is {size} bytes, larger than the {MAX_TEXT_FILE_BYTES} byte limit for reading text files. Find the relevant lines with search_text_in_files instead.")

        info = _get_file_info(p)
        lines = _split_lines(_read_text(p, info.has_bom))
        total = len(lines)

        start = 1 if start_line is None else int(start_line)
        end = total if end_line is None else min(int(end_line), total)
        if start < 1 or (total and start > total):
            return _text_error("invalid_range", f"start_line must be between 1 and {total}, the number of lines in the file.")
        if end < start and total:
            return _text_error("invalid_range", f"end_line must not be smaller than start_line ({start}).")

        # Keep the read within MAX_READ_LINES and MAX_OUTPUT_CHARS, leaving room for the header
        shown, shortened = _shorten_lines(lines[start - 1:min(end, start + MAX_READ_LINES - 1)], start)
        budget = MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4)
        chars = 0
        for i, line in enumerate(shown):
            chars += len(line) + (len(str(end)) + 3 if line_numbers else 1)
            if chars > budget and i > 0:
                shown = shown[:i]
                break
        shortened = [n for n in shortened if n < start + len(shown)]
        truncated = start + len(shown) - 1 < end
        end = start + len(shown) - 1 if total else end

        encoding = "utf-8 with BOM" if info.has_bom else "utf-8"
        header = [f"File: {info.path}", f"SHA-256: {info.sha256}"]
        if not total:
            header.append("Lines: 0 (empty file)")
        else:
            partial = start > 1 or end < total
            header.append(f"Lines: {start}-{end} of {total}" + (" (partial)" if partial else ""))
        header.append(f"Line ending: {info.line_ending} | Encoding: {encoding} | Size: {info.size_bytes} bytes | Modified: {info.modified_at}")
        if truncated:
            header.append(f"Output limited: lines {start}-{end} of {total} are shown, as one read is limited to {MAX_READ_LINES} lines or {MAX_OUTPUT_CHARS} characters."
                          f" Read the next part with start_line={end + 1}, or read only the lines you need with start_line and end_line (e.g. around a line number found with search_text_in_files).")
        if shortened:
            header.append(_shortened_lines_note(shortened))
        if total and (start > 1 or end < total):
            header.append("Note: This is only part of the file. Change it with edit_file: write_file would replace the whole file with only this part.")
        if line_numbers:
            header.append("Note: The 'N| ' line number prefixes are not part of the content. Do not copy them into edit_file.")

        return "\n".join(header) + "\n" + _content_block(shown, start, line_numbers)
    except Exception as e:
        return _text_error("read_error", str(e))

@mcp.tool()
def read_image(path: str) -> list[str | Image] | str:
    """
    Reads an image file and returns it as an image you can see, along with its metadata.
    Useful for visual analysis of UI components, screenshots or assets.
    Supports PNG, JPEG, GIF and WebP images. Viewing the image requires a vision-capable model.
    For SVG images, use 'read_file_with_metadata' instead, as they are text files.

    Args:
        path (str): Path to the image file.

    Returns:
        list | str: The image and a JSON string with its path, format, size and modification time,
            or a JSON error message (e.g., unsupported_format or too_large).
    """
    try:
        p = _resolve_path(path)
        if not _is_path_allowed(p):
            return _access_denied(p)

        if not p.is_file():
            return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {p}"}, indent=2)

        stats = p.stat()
        if stats.st_size > MAX_IMAGE_BYTES:
            return json.dumps({
                "success": False,
                "error": "too_large",
                "message": f"Image is {stats.st_size} bytes, larger than the {MAX_IMAGE_BYTES} byte limit."
            }, indent=2)

        with open(p, "rb") as f:
            data = f.read()

        image_format = _detect_image_format(data)
        if not image_format:
            message = "Not a supported image. Supported formats: PNG, JPEG, GIF and WebP."
            if p.suffix.lower() == ".svg":
                message += " SVG images are text files, so read them with 'read_file_with_metadata'."
            return json.dumps({"success": False, "error": "unsupported_format", "message": message}, indent=2)

        metadata = json.dumps({
            "success": True,
            "path": str(p),
            "mime_type": f"image/{image_format}",
            "size_bytes": stats.st_size,
            "modified_at": datetime.datetime.fromtimestamp(stats.st_mtime, tz=datetime.timezone.utc).isoformat()
        }, indent=2, ensure_ascii=False)
        return [metadata, Image(data=data, format=image_format)]
    except Exception as e:
        return json.dumps({"success": False, "error": "read_image_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

@mcp.tool()
def list_directory(path: str) -> str:
    """
    Lists all files and directories within the specified path, folders first.
    Very large directories are listed partially.

    Args:
        path (str): The directory to list.

    Returns:
        str: JSON string containing a list of entries, distinguishing between [FILE] and [DIR].
    """
    try:
        p = _resolve_path(path)
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

        # Folders first, then files, by name, so that a limited listing is predictable.
        # Each listed entry takes about 150 characters of JSON.
        entries.sort(key=lambda e: (e["type"] != "DIR", e["name"].lower()))
        limit = max(10, min(MAX_LIST_ENTRIES, (MAX_OUTPUT_CHARS - 1000) // 150))
        response = {"success": True, "total_entries": len(entries), "entries": entries[:limit]}
        if len(entries) > limit:
            rel = _display_path(p, ALLOWED_DIR)
            prefix = "" if rel == "." else f"{rel}/"
            suffixes = [Path(e["name"]).suffix for e in entries if e["type"] == "FILE" and Path(e["name"]).suffix]
            example = max(set(suffixes), key=suffixes.count) if suffixes else ".py"
            response["output_limited"] = (f"Output limited: the directory has {len(entries)} entries, but only the first {limit} (folders first, then files, by name) are listed."
                                          f" Find specific files with search_files_by_pattern, e.g. pattern='{prefix}*{example}' or pattern='{prefix}name*'.")
        return json.dumps(response, indent=2)
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
        p = _resolve_path(path)
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
        src = _resolve_path(source)
        dst = _resolve_path(destination)

        if not _is_path_allowed(src):
            return _access_denied(src, "Source path")
        if not _is_path_allowed(dst):
            return _access_denied(dst, "Destination path")
        if _contains_forbidden(src):
            return json.dumps({"success": False, "error": "access_denied", "message": f"Source path contains forbidden or protected files or folders: {src}"}, indent=2)

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
        p = _resolve_path(path)
        if not _is_path_allowed(p):
            return _access_denied(p)
        if _contains_forbidden(p):
            return json.dumps({"success": False, "error": "access_denied", "message": f"Path contains forbidden or protected files or folders: {p}"}, indent=2)
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
    the allowed directory, which relative paths are resolved against, and forbidden paths.
    Use this tool to find out why a path is denied or not found by the filesystem tools.

    Returns:
        str: A JSON-formatted string containing the configuration or an error message.
    """
    try:
        return json.dumps({
            "success": True,
            "allowed_dir": ALLOWED_DIR.as_posix(),
            "allowed_dir_exists": ALLOWED_DIR.exists(),
            "ignored_dirs": [],
            "forbidden_paths": [_display_path(p, ALLOWED_DIR) for p in FORBIDDEN_PATHS],
            "max_output_chars": MAX_OUTPUT_CHARS,
            "max_read_lines": MAX_READ_LINES,
            "max_line_chars": MAX_LINE_CHARS,
            "max_list_entries": MAX_LIST_ENTRIES,
            "protected_file_names": sorted(PROTECTED_FILE_NAMES),
            "gitignore": {
                "applied": False
            },
            "notes": [
                "Files named in protected_file_names (the MCP server configuration) are always denied and hidden at any depth, even if forbidden_paths is empty.",
                "Only paths within allowed_dir can be accessed.",
                "Relative paths given to the tools are resolved against allowed_dir.",
                "Forbidden paths and everything inside forbidden folders are denied and hidden from list_directory.",
                "Deleting or moving a folder that contains a forbidden path is denied.",
                "There are no ignored directories and .gitignore is not applied, so folders such as .git are accessible unless forbidden."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

if __name__ == "__main__":
    mcp.run()
