# --- filesystem.py ---
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
from .common import (
    PROJECT_DIR,
    BACKUP_DIR,
    COMMAND_SUGGESTIONS,
    REGISTRY_PATH,
    CONFIG,
    ALLOWED_PATHS,
    CONFIG_PATH,
    DISABLED_TOOLS,
    ENABLED_TOOLSETS,
    FORBIDDEN_PATHS,
    GITIGNORE_PATH,
    IGNORED_DIRS,
    MAX_OUTPUT_CHARS,
    OLLAMA_API_KEY,
    USER_CONFIG_PATH,
    UNAVAILABLE_TOOLSETS,
    PROTECTED_FILE_NAMES,
    PROTECTED_PATHS,
    READ_ONLY_FILES,
    access_denied_message,
    code_block,
    code_language,
    contains_forbidden,
    contains_read_only,
    display_path,
    get_repo_root,
    is_path_allowed,
    is_path_readable,
    is_read_only,
    load_gitignore_patterns,
    resolve_path,
    text_response
)
from .parsing import (
    OUTLINE_SUFFIXES,
    locate_section,
    outline_after_write,
    outline_text
)

# --- Constants & Config ---
mcp = MCPServer("filesystem")  # Holds the tools of this toolset; __main__.py serves the enabled toolsets in one server

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB limit
MAX_READ_LINES = 1000  # Longer files are read in parts
MAX_LINE_CHARS = 2000  # Longer lines (e.g. minified code) are shown shortened, ending with SHORTENED_LINE_MARK
SHORTENED_LINE_MARK = " [...]"
MAX_TEXT_FILE_BYTES = 50 * 1024 * 1024  # Larger text files are not read
MAX_BACKUPS = 200  # Changes kept for restore_file; the backups of older ones are removed
BACKUP_JOURNAL = "journal.json"
EDIT_SNIPPET_CONTEXT_LINES = 3  # Lines shown around a change after an edit
MAX_EDIT_SNIPPET_LINES = 40
SHRINK_WARNING_RATIO = 0.5  # write_file warns if a file of 20+ lines shrinks below this ratio
OUTLINE_AFTER_WRITE_CHARS = min(2500, MAX_OUTPUT_CHARS // 8)  # Room for the outline shown after a write
# Fence lines of a code block copied into edit_file with the text, e.g. '```python', removed if the text does not match with them
FENCE_LINE = re.compile(r"^ {0,3}`{3,}[\w+-]*[ \t]*$")
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

def _rel(path: Path) -> str:
    """
    Returns a path as shown in responses: relative to PROJECT_DIR, like the paths given to and shown by the other tools.
    """
    return display_path(path, PROJECT_DIR)

def _is_writable(path: Path) -> bool:
    """
    Checks if the file tools may change the given path: an allowed path that is not a read-only file.
    """
    return is_path_allowed(path) and not is_read_only(path)

def _access_denied(path: Path, label: str = "Path") -> str:
    """
    Returns the JSON access_denied response, stating why access to the given path was denied.
    """
    return json.dumps({"success": False, "error": "access_denied", "message": access_denied_message(path, label)}, indent=2, ensure_ascii=False)

def _text_error(error: str, message: str) -> str:
    """
    Returns a plain-text error response, used by the tools whose responses contain file content.
    """
    return f"Error ({error}): {message}"

def _edit_error(error: str, message: str, sha256_valid: bool = False) -> str:
    """
    Returns the plain-text error of an edit that was not made, saying that the file was not changed and,
    once the hash has been checked, that the same expected_sha256 can be used for the next try.
    """
    unchanged = "Nothing was changed, and expected_sha256 is still valid." if sha256_valid else "Nothing was changed."
    return _text_error(error, f"{unchanged} {message}")

def _not_changed(error: str, message: str, **fields) -> str:
    """
    Returns the JSON error of a change that was not made, starting the message with 'Nothing was changed.'.
    """
    return json.dumps({"success": False, "error": error, "message": f"Nothing was changed. {message}", **fields}, indent=2, ensure_ascii=False)

def _maybe_changed(error: str, e: Exception, check: str) -> str:
    """
    Returns the JSON error of an unexpected failure during a change, which may have been partly made, with the call that checks it.
    """
    return json.dumps({"success": False, "error": error, "message": f"{e}. The change may have been partly made: check it with {check} before trying again.",
                       "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)

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
        raise FileNotFoundError(f"File not found: {_rel(path)}")

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
        raise Exception(f"{_rel(path)} is not a UTF-8 text file. For images, use 'read_image'.")

    # Detect line endings, defaulting to LF
    line_ending = "CRLF" if b"\r\n" in raw_data else "LF"

    mtime = os.path.getmtime(path)
    modified_at = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).isoformat()

    return FileMetadata(
        path=_rel(path),
        sha256=sha256,
        size_bytes=size_bytes,
        line_ending=line_ending,
        encoding="utf-8",
        has_bom=has_bom,
        modified_at=modified_at
    )

def _write_logic(path: Path, content: str, expected_sha256: str, tool: str) -> str:
    """
    Validates the file hash, keeps a backup of the file for restore_file, then writes the content atomically
    while keeping the original line endings and BOM. Returns a JSON response with the new metadata.
    """
    if not path.exists():
        return _not_changed("file_not_found", f"File not found: {_rel(path)}.")

    # 1. Read metadata to validate
    try:
        info = _get_file_info(path)
    except Exception as e:
        return _not_changed("read_error", str(e))

    # 2. Hash validation
    if info.sha256 != expected_sha256:
        return _not_changed("hash_mismatch", "The file has changed since it was read. Read it again with read_file_with_metadata before writing it.",
                            current_sha256=info.sha256)

    # 3. Normalize all line endings to LF, then restore the original line endings
    normalized_content = _normalize_newlines(content)
    if info.line_ending == "CRLF":
        normalized_content = normalized_content.replace("\n", "\r\n")

    # 4. Backup, then atomic write
    try:
        _record_change(tool, path, "copy")
    except Exception as e:
        return _backup_error(e)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        # Using 'utf-8-sig' if BOM is present handles writing the BOM automatically
        write_encoding = "utf-8-sig" if info.has_bom else "utf-8"

        with open(temp_path, "w", encoding=write_encoding, newline="") as f:
            f.write(normalized_content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)
    except Exception as e:
        if temp_path.exists():
            os.remove(temp_path)
        return _not_changed("write_error", f"The file could not be written: {e}")

    new_info = _get_file_info(path)
    return json.dumps({"success": True, **asdict(new_info)}, indent=2, ensure_ascii=False)

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

def _fit_lines(lines: list[str], max_chars: int) -> list[str]:
    """
    Returns the first lines that fit in max_chars characters, at least one line.
    """
    total = 0
    for i, line in enumerate(lines):
        total += len(line) + 1
        if total > max_chars and i > 0:
            return lines[:i]
    return lines

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

def _content_block(lines: list[str], first_line: int, language: str = "text", line_numbers: bool = False) -> str:
    """
    Returns the lines as a fenced code block, optionally prefixed with 'N| ' line numbers.
    """
    if line_numbers:
        width = len(str(first_line + len(lines) - 1))
        lines = [f"{first_line + i:>{width}}| {line}" for i, line in enumerate(lines)]
    return code_block("\n".join(lines), language)

def _strip_fence_lines(text: str) -> str | None:
    """
    Removes the fence lines of a code block copied with the text, e.g. '```python' before it and '```'
    after it, or returns None if the text does not start or end with one.
    """
    lines = text.split("\n")
    start = 1 if lines and FENCE_LINE.match(lines[0]) else 0
    end = len(lines) - 1 if len(lines) > start and FENCE_LINE.match(lines[-1]) else len(lines)
    if start == 0 and end == len(lines):
        return None
    return "\n".join(lines[start:end])

def _find_placeholder(new_text: str, original: str) -> str | None:
    """
    Returns a placeholder comment such as '// ... existing code ...' found in new_text,
    unless the original content already contains one.
    """
    match = PLACEHOLDER_COMMENT.search(new_text)
    if match and not PLACEHOLDER_COMMENT.search(original):
        return match.group(0).strip()
    return None

def _write_report(path: Path, before: str | None, after: str) -> dict | None:
    """
    Returns the outline of a file after a write and the sections the write removed or added
    (see outline_after_write), or None if the file type has no outline.
    """
    return outline_after_write(before, _normalize_newlines(after), path.suffix, OUTLINE_AFTER_WRITE_CHARS)

def _add_report(result: dict, report: dict | None) -> dict:
    """
    Adds the outline, the removed and added sections and their warnings to a JSON write response.
    """
    if not report or not result.get("success"):
        return result
    if report["outline"]:
        result["outline"] = report["outline"]
    if report["removed"]:
        result["removed_sections"] = report["removed"]
    if report["added"]:
        result["added_sections"] = report["added"]
    warnings = [w for w in [result.get("warning")] + report["warnings"] if w]
    if warnings:
        result["warning"] = " ".join(warnings)
    return result

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
        stripped = _strip_fence_lines(old)
        if stripped and stripped in content:
            old, new = stripped, _strip_fence_lines(new) or new
            note = " Code block fence lines were removed from old_text."
    if old not in content:
        stripped = _strip_line_numbers(old)
        if stripped and stripped in content:
            old, new = stripped, _strip_line_numbers(new) or new
            note += " Line number prefixes were removed from old_text."

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
        actual = _fit_lines(actual, MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4))
        message += (f" Lines {start + 1}-{start + len(old_lines)} match when indentation is ignored."
                    f" Copy them exactly as they appear in the file, including indentation:\n\n"
                    f"{_content_block(actual, start + 1)}")
        if shortened or len(actual) < len(old_lines):
            message += f"\n\nOutput limited: only part of these lines is shown. Read them with read_file_with_metadata(start_line={start + 1})."
    else:
        message += " Read the file again with 'read_file_with_metadata' and copy the text exactly as it appears in the file."
    raise _EditError("no_match", message)

def _edit_logic(path: Path, old_text: str, new_text: str, expected_sha256: str, replace_all: bool) -> str:
    """
    Validates the file hash, replaces old_text with new_text and writes the file with _write_logic.
    Returns a plain-text response with the new SHA-256 and the changed lines, or an error.
    """
    if not path.is_file():
        return _edit_error("file_not_found", f"File not found: {_rel(path)}. To create a new file, use write_file.")

    try:
        info = _get_file_info(path)
    except Exception as e:
        return _edit_error("read_error", str(e))

    if info.sha256 != expected_sha256:
        return _edit_error("hash_mismatch", f"The file has changed since it was read. Current SHA-256: {info.sha256}. Read the file again with 'read_file_with_metadata' before editing it.")

    old = _normalize_newlines(old_text)
    new = _normalize_newlines(new_text)
    if not old:
        return _edit_error("empty_old_text", "old_text must not be empty. Use write_file to replace the whole file.", sha256_valid=True)
    if old == new:
        return _edit_error("no_change", "old_text and new_text are identical.", sha256_valid=True)

    content = _read_text(path, info.has_bom)
    placeholder = _find_placeholder(new, content)
    if placeholder:
        return _edit_error("placeholder_in_new_text", f"new_text contains the placeholder comment '{placeholder}'. Text is written literally, so the placeholder would replace real code. Write out the full code in new_text, or make old_text cover only the lines you change.", sha256_valid=True)

    try:
        new_content, first_line, last_line, replacements, note = _apply_edit(content, old, new, replace_all)
    except _EditError as e:
        return _edit_error(e.error, e.message, sha256_valid=True)

    result = json.loads(_write_logic(path, new_content, expected_sha256, "edit_file"))
    if not result.get("success"):
        # The message of _write_logic already says that nothing was changed
        return _text_error(result.get("error", "write_error"), result.get("message", ""))

    report = _write_report(path, content, new_content)
    warnings = report["warnings"] if report else []
    outline = ["Outline after the edit:", code_block("\n".join(report["outline"]))] if report and report["outline"] else []
    if report and report["added"]:
        outline.append(f"Added sections: {', '.join(report['added'][:20])}")

    # Show the changed lines within MAX_EDIT_SNIPPET_LINES and what is left of MAX_OUTPUT_CHARS after the rest of the response
    new_lines = _split_lines(new_content)
    snippet_start = max(1, first_line - EDIT_SNIPPET_CONTEXT_LINES)
    snippet_end = min(len(new_lines), last_line + EDIT_SNIPPET_CONTEXT_LINES, snippet_start + MAX_EDIT_SNIPPET_LINES - 1)
    plural = "replacement" if replacements == 1 else "replacements"
    snippet, shortened = _shorten_lines(new_lines[snippet_start - 1:snippet_end], snippet_start)
    snippet = _fit_lines(snippet, MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4) - sum(len(line) + 1 for line in warnings + outline))
    snippet_end = snippet_start + len(snippet) - 1 if new_lines else snippet_end
    shortened = [n for n in shortened if n <= snippet_end]
    limited = []
    if last_line + EDIT_SNIPPET_CONTEXT_LINES > snippet_end and snippet_end < len(new_lines):
        limited.append(f"Output limited: the change continues after line {snippet_end}. Check the rest with read_file_with_metadata(path='{_rel(path)}', start_line={snippet_end + 1}) if needed.")
    if shortened:
        limited.append(_shortened_lines_note(shortened))
    header = [f"Edited `{_rel(path)}`: {replacements} {plural}, starting at line {first_line}.{note}",
              f"New SHA-256: {result['sha256']} (use it as expected_sha256 for the next edit to this file)",
              *warnings, *limited]
    changed = (f"Lines {snippet_start}-{snippet_end} of {len(new_lines)} after the edit:\n\n{_content_block(snippet, snippet_start, code_language(path))}"
               if new_lines else "The file is now empty.")
    return text_response(header, changed, *outline)

def _detect_neighbor_line_ending(directory: Path) -> tuple[str, str]:
    """
    Picks the majority line ending of the allowed files in the directory, defaulting to LF.
    Returns the newline string and a note to add to the response message.
    """
    counts = {"CRLF": 0, "LF": 0}
    if directory.exists():
        for neighbor in directory.iterdir():
            if neighbor.is_file() and is_path_allowed(neighbor):
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

# --- Backups for restore_file ---
# Every change the file tools make is recorded in a journal in BACKUP_DIR before it is made, with a copy of
# a changed file. A deleted file or folder is moved into the backup folder instead of being removed.
# restore_file undoes the latest change to a path and moves what it replaces into the backup folder too,
# so that nothing the tools touch is lost. Only the last MAX_BACKUPS changes are kept.

def _load_journal() -> list[dict]:
    """
    Returns the recorded changes, oldest first.
    """
    path = BACKUP_DIR / BACKUP_JOURNAL
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_journal(entries: list[dict]) -> None:
    """
    Writes the journal atomically, removing the backups of the changes beyond the last MAX_BACKUPS.
    """
    for old in entries[:-MAX_BACKUPS]:
        shutil.rmtree(BACKUP_DIR / old["id"], ignore_errors=True)
    path = BACKUP_DIR / BACKUP_JOURNAL
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(entries[-MAX_BACKUPS:], f, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)

def _record_change(tool: str, path: Path, keep: str = "", source: Path | None = None) -> None:
    """
    Records a change to path before the tool makes it. keep is 'copy' to keep a copy of the file, or 'move'
    to move the file or folder into the backup folder, which deletes it. For a move, path is the destination
    and source the current path. Raises an error if the backup fails, so that the change is not made.
    """
    entries = _load_journal()
    now = datetime.datetime.now()
    entry_id = base_id = now.strftime("%Y%m%d-%H%M%S-%f")
    count = 1
    while (BACKUP_DIR / entry_id).exists():
        count += 1
        entry_id = f"{base_id}-{count}"
    entry_dir = BACKUP_DIR / entry_id
    entry_dir.mkdir(parents=True)
    if keep == "copy":
        shutil.copy2(path, entry_dir / "before")
    elif keep == "move":
        shutil.move(str(path), str(entry_dir / "before"))
    entries.append({"id": entry_id, "time": now.isoformat(timespec="seconds"), "tool": tool,
                    "path": _rel(path), "source": _rel(source) if source else None, "restored": False})
    try:
        _save_journal(entries)
    except Exception:
        if keep == "move":
            shutil.move(str(entry_dir / "before"), str(path))
        raise

def _backup_error(e: Exception) -> str:
    """
    Returns the JSON error for a change that was not made because its backup failed.
    """
    return json.dumps({"success": False, "error": "backup_error", "message": f"Nothing was changed, as the backup for restore_file failed: {e}"}, indent=2, ensure_ascii=False)

def _latest_change(entries: list[dict], path: Path) -> dict | None:
    """
    Returns the latest change that is not restored yet to path or to a folder containing it, also as the source of a move.
    """
    for entry in reversed(entries):
        paths = [PROJECT_DIR / entry["path"]] + ([PROJECT_DIR / entry["source"]] if entry["source"] else [])
        if not entry["restored"] and any(path.is_relative_to(p) for p in paths):
            return entry
    return None

def _set_aside(path: Path, entry_dir: Path) -> None:
    """
    Moves a file or folder that restore_file replaces into the backup folder of the change.
    """
    if path.exists() or path.is_symlink():
        shutil.move(str(path), str(entry_dir / "replaced"))

def _text_or_none(path: Path) -> str | None:
    """
    Returns the content of a UTF-8 text file, or None for folders, missing and other files.
    """
    try:
        return _read_text(path, _get_file_info(path).has_bom) if path.is_file() else None
    except Exception:
        return None

def _create_logic(path: Path, content: str) -> str:
    """
    Creates a new file and any missing folders, with the line endings of the neighbouring files,
    after recording the creation for restore_file. Returns a JSON response with the metadata and outline.
    """
    target_newline, note = _detect_neighbor_line_ending(path.parent)

    normalized_content = _normalize_newlines(content)
    if target_newline == "\r\n":
        normalized_content = normalized_content.replace("\n", "\r\n")

    try:
        _record_change("write_file", path)
    except Exception as e:
        return _backup_error(e)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(normalized_content)

    message = f"File created successfully.{note}"
    try:
        new_info = _get_file_info(path)
        result = {"success": True, **asdict(new_info), "message": message}
        return json.dumps(_add_report(result, _write_report(path, None, content)), indent=2, ensure_ascii=False)
    except Exception:
        # Fallback if metadata retrieval fails
        return json.dumps({"success": True, "message": message}, indent=2, ensure_ascii=False)

# --- Public MCP Tools ---

@mcp.tool()
def write_file(path: str, content: str, expected_sha256: str = "") -> str:
    """
    Writes a whole file: creates a new file and any missing folders, or replaces an existing file.
    To change only part of a file, use edit_file.
    content is written literally: never use placeholders such as '// ... existing code ...'.

    Args:
        path: The file.
        content: The complete content.
        expected_sha256: To replace a file, the SHA-256 from its last read or edit. Empty for a new file.

    Returns:
        JSON with the new SHA-256 and, for code and Markdown files, the new outline and removed sections.
    """
    try:
        p = resolve_path(path)
        if not _is_writable(p):
            return _not_changed("access_denied", access_denied_message(p))
        if p.is_dir():
            return _not_changed("is_a_directory", f"{_rel(p)} is a folder, not a file.")

        if not p.exists():
            if expected_sha256:
                return _not_changed("file_not_found", f"File not found: {_rel(p)}. Check the path, or leave expected_sha256 empty to create a new file.")
            return _create_logic(p, content)
        if not expected_sha256:
            return _not_changed("file_exists", f"{_rel(p)} already exists. To replace it, give the SHA-256 from read_file_with_metadata as expected_sha256, or change only part of it with edit_file.")

        try:
            info = _get_file_info(p)
            original = _read_text(p, info.has_bom)
        except Exception as e:
            return _not_changed("read_error", str(e))

        placeholder = _find_placeholder(_normalize_newlines(content), original)
        if placeholder:
            return _not_changed("placeholder_in_content", f"content contains the placeholder comment '{placeholder}'. The content is written literally, so the placeholder would replace real code. Write out the complete file, or use edit_file to change only part of it.")

        result = json.loads(_write_logic(p, content, expected_sha256, "write_file"))
        old_count, new_count = len(_split_lines(original)), len(_split_lines(_normalize_newlines(content)))
        if result.get("success") and old_count >= 20 and new_count < old_count * SHRINK_WARNING_RATIO:
            result["warning"] = f"The file shrank from {old_count} to {new_count} lines. If you meant to change only part of it, restore the missing lines: write_file replaces the whole file, while edit_file changes only part of it."
        return json.dumps(_add_report(result, _write_report(p, original, content)), indent=2, ensure_ascii=False)
    except Exception as e:
        return _maybe_changed("write_error", e, f"read_file_with_metadata(path='{path}')")

@mcp.tool()
def edit_file(path: str, old_text: str, new_text: str, expected_sha256: str, replace_all: bool = False) -> str:
    """
    Replaces old_text with new_text in an existing file. Prefer this over write_file.
    Copy old_text exactly from read_file_with_metadata, including indentation, without line number
    prefixes. It must match once: add surrounding lines, or set replace_all. An empty new_text deletes.
    new_text is written literally: never use placeholders such as '// ... existing code ...'.

    Args:
        path: The file to edit.
        old_text: The exact text to replace.
        new_text: The replacement text.
        expected_sha256: The SHA-256 from the last read or edit of the file.
        replace_all: Replace every match. Defaults to False.

    Returns:
        The changed lines, the new SHA-256 (use it for the next edit without reading again) and the
        file's outline, or an error such as no_match with the closest matching lines.
    """
    try:
        p = resolve_path(path)
        if not _is_writable(p):
            return _edit_error("access_denied", access_denied_message(p))

        return _edit_logic(p, old_text, new_text, expected_sha256, replace_all)
    except Exception as e:
        return _text_error("edit_error", f"The edit may have been partly made: read the file again with read_file_with_metadata(path='{path}') before editing it. {e}\n{traceback.format_exc()}")

@mcp.tool()
def read_file_with_metadata(path: str, start_line: int | None = None, end_line: int | None = None, line_numbers: bool = False, section: str = "") -> str:
    """
    Reads a text file: its SHA-256, needed by edit_file and write_file, and its exact content in a
    code block (the fence lines are not part of it). Read only what you need: a class,
    function or heading with section, or lines with start_line and end_line (e.g. a span from
    get_outline). Long files are read in parts of up to 1000 lines.

    Args:
        path: The file.
        start_line: The first line to read. Defaults to 1.
        end_line: The last line to read. Defaults to the end of the file.
        line_numbers: Prefix lines with 'N| ', which is not part of the content. Defaults to False.
        section: A name from get_outline, e.g. 'App.run' or 'Install', instead of start_line and end_line.
    """
    try:
        p = resolve_path(path)
        if not is_path_readable(p):
            return _text_error("access_denied", access_denied_message(p))
        if p.is_dir():
            return _text_error("is_a_directory", f"{_rel(p)} is a directory. Use get_outline(path='{_rel(p)}') to see its files.")
        if not p.exists():
            return _text_error("file_not_found", f"File not found: {_rel(p)}")

        size = p.stat().st_size
        if size > MAX_TEXT_FILE_BYTES:
            return _text_error("too_large", f"File is {size} bytes, larger than the {MAX_TEXT_FILE_BYTES} byte limit for reading text files. Find the relevant lines with search_text_in_files instead.")

        info = _get_file_info(p)
        text = _read_text(p, info.has_bom)
        lines = _split_lines(text)
        total = len(lines)

        section_line = ""
        if section:
            if start_line is not None or end_line is not None:
                return _text_error("invalid_arguments", "Give either section, or start_line and end_line, not both.")
            found, error, message = locate_section(outline_text(text, p.suffix), section)
            if not found:
                if error == "section_not_found":
                    message += f" Use get_outline(path='{_rel(p)}') to see the sections with their line spans."
                return _text_error(error, message)
            start_line, end_line = found.start, found.end
            section_line = f"Section: {found.label} (lines {found.start}-{found.end})"

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
        requested_end = end
        end = start + len(shown) - 1 if total else end

        encoding = "utf-8 with BOM" if info.has_bom else "utf-8"
        header = [f"File: `{info.path}`", f"SHA-256: {info.sha256}"]
        if not total:
            header.append("Lines: 0 (empty file)")
        else:
            partial = start > 1 or end < total
            header.append(f"Lines: {start}-{end} of {total}" + (" (partial)" if partial else ""))
        if section_line:
            header.append(section_line)
        header.append(f"Line ending: {info.line_ending} | Encoding: {encoding} | Size: {info.size_bytes} bytes | Modified: {info.modified_at}")
        if truncated:
            header.append(f"Output limited: lines {start}-{end} of {total} are shown, as one read is limited to {MAX_READ_LINES} lines or {MAX_OUTPUT_CHARS} characters."
                          f" Read the next part with start_line={end + 1}{f', end_line={requested_end}' if requested_end < total else ''}, or read only the lines you need with start_line and end_line (e.g. around a line number found with search_text_in_files).")
        if shortened:
            header.append(_shortened_lines_note(shortened))
        if total and (start > 1 or end < total):
            header.append("Note: This is only part of the file. Change it with edit_file: write_file would replace the whole file with only this part.")
        if line_numbers:
            header.append("Note: The 'N| ' line number prefixes are not part of the content. Do not copy them into edit_file.")

        return text_response(header, _content_block(shown, start, code_language(p), line_numbers))
    except Exception as e:
        return _text_error("read_error", str(e))

@mcp.tool()
def read_image(path: str) -> list[str | Image] | str:
    """
    Shows a PNG, JPEG, GIF or WebP image, if you can see images. Read SVG files with read_file_with_metadata.

    Args:
        path: The image file.
    """
    try:
        p = resolve_path(path)
        if not is_path_readable(p):
            return _access_denied(p)

        if not p.is_file():
            return json.dumps({"success": False, "error": "file_not_found", "message": f"File not found: {_rel(p)}"}, indent=2, ensure_ascii=False)

        stats = p.stat()
        if stats.st_size > MAX_IMAGE_BYTES:
            return json.dumps({
                "success": False,
                "error": "too_large",
                "message": f"Image is {stats.st_size} bytes, larger than the {MAX_IMAGE_BYTES} byte limit."
            }, indent=2, ensure_ascii=False)

        with open(p, "rb") as f:
            data = f.read()

        image_format = _detect_image_format(data)
        if not image_format:
            message = "Not a supported image. Supported formats: PNG, JPEG, GIF and WebP."
            if p.suffix.lower() == ".svg":
                message += " SVG images are text files, so read them with 'read_file_with_metadata'."
            return json.dumps({"success": False, "error": "unsupported_format", "message": message}, indent=2, ensure_ascii=False)

        metadata = json.dumps({
            "success": True,
            "path": _rel(p),
            "mime_type": f"image/{image_format}",
            "size_bytes": stats.st_size,
            "modified_at": datetime.datetime.fromtimestamp(stats.st_mtime, tz=datetime.timezone.utc).isoformat()
        }, indent=2, ensure_ascii=False)
        return [metadata, Image(data=data, format=image_format)]
    except Exception as e:
        return json.dumps({"success": False, "error": "read_image_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)

@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """
    Moves or renames a file or folder. Missing destination folders are created.

    Args:
        source: The current path.
        destination: The new path.
    """
    try:
        src = resolve_path(source)
        dst = resolve_path(destination)

        if not is_path_allowed(src):
            return _not_changed("access_denied", access_denied_message(src, "Source path"))
        if not _is_writable(dst):
            return _not_changed("access_denied", access_denied_message(dst, "Destination path"))
        if contains_forbidden(src):
            return _not_changed("access_denied", f"Source path contains forbidden or protected files or folders: {_rel(src)}")
        if contains_read_only(src):
            return _not_changed("access_denied", f"Source path is or contains a read-only file, which the tools can read but not change: {_rel(src)}")

        if not src.exists():
            return _not_changed("source_not_found", f"Source not found: {_rel(src)}")
        if dst.exists() and os.path.samefile(src, dst):
            # Only the letter case changes, e.g. 'app.py' to 'App.py' on Windows, where both name the same file
            new_name = Path(destination).name
            if new_name == src.name:
                return _not_changed("same_path", f"Source and destination are the same: {_rel(src)}")
            try:
                _record_change("move_file", src.with_name(new_name), source=src)
            except Exception as e:
                return _backup_error(e)
            os.rename(src, src.with_name(new_name))
            return json.dumps({"success": True, "message": f"Renamed {_rel(src)} to {_rel(src.with_name(new_name))}"}, indent=2, ensure_ascii=False)
        if dst.exists():
            return _not_changed("destination_exists", f"Destination already exists: {_rel(dst)}")
        if src.is_dir() and dst.is_relative_to(src):
            return _not_changed("invalid_destination", f"A folder cannot be moved into itself: {_rel(dst)} is inside {_rel(src)}.")

        try:
            _record_change("move_file", dst, source=src)
        except Exception as e:
            return _backup_error(e)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return json.dumps({"success": True, "message": f"Moved {_rel(src)} to {_rel(dst)}"}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _maybe_changed("move_error", e, f"get_outline(path='{source}') and get_outline(path='{destination}')")

@mcp.tool()
def delete_file(path: str) -> str:
    """
    Deletes a file, or a folder with everything in it.

    Args:
        path: The file or folder.
    """
    try:
        p = resolve_path(path)
        if not is_path_allowed(p):
            return _not_changed("access_denied", access_denied_message(p))
        if contains_forbidden(p):
            return _not_changed("access_denied", f"Path contains forbidden or protected files or folders: {_rel(p)}")
        if contains_read_only(p):
            return _not_changed("access_denied", f"Path is or contains a read-only file, which the tools can read but not change: {_rel(p)}")
        if not p.exists():
            return _not_changed("not_found", f"Path not found: {_rel(p)}")

        # Moved into the backup folder instead of being removed, so that restore_file can bring it back
        try:
            _record_change("delete_file", p, "move")
        except Exception as e:
            return _backup_error(e)
        return json.dumps({"success": True, "message": f"Deleted: {_rel(p)}. Undo with restore_file(path='{_rel(p)}') if needed."}, indent=2, ensure_ascii=False)
    except Exception as e:
        return _maybe_changed("delete_error", e, f"get_outline(path='{path}')")

@mcp.tool()
def restore_file(path: str) -> str:
    """
    Undoes the last change edit_file, write_file, move_file or delete_file made to a path,
    e.g. a broken edit. Call it again to undo earlier changes.

    Args:
        path: The file or folder, before or after a move.
    """
    try:
        p = resolve_path(path)
        if not _is_writable(p):
            return _not_changed("access_denied", access_denied_message(p))

        entries = _load_journal()
        entry = _latest_change(entries, p)
        if not entry:
            recent = list(dict.fromkeys(e["path"] for e in reversed(entries) if not e["restored"]))[:10]
            hint = f" Paths with changes that can be undone: {', '.join(recent)}." if recent else " No changes are recorded."
            return _not_changed("no_backup", f"No change to {_rel(p)} by the file tools is recorded.{hint}")

        target = PROJECT_DIR / entry["path"]
        source = PROJECT_DIR / entry["source"] if entry["source"] else None
        for checked in [target, source]:
            if checked and not _is_writable(checked):
                return _not_changed("access_denied", access_denied_message(checked))
            if checked and contains_read_only(checked):
                return _not_changed("access_denied", f"{_rel(checked)} is or contains a read-only file, which the tools can read but not change.")
        entry_dir = BACKUP_DIR / entry["id"]
        when = f"{entry['tool']} at {entry['time'].replace('T', ' ')}"

        if source:
            if not target.exists():
                return _not_changed("not_found", f"{entry['path']}, which {when} moved from {entry['source']}, no longer exists, so it cannot be moved back.")
            _set_aside(source, entry_dir)
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(source))
            restored, message = source, f"Moved {entry['path']} back to {entry['source']}, undoing {when}."
        else:
            before_text = _text_or_none(target)
            _set_aside(target, entry_dir)
            restored = target
            if (entry_dir / "before").exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry_dir / "before"), str(target))
                message = f"Restored {entry['path']} as it was before {when}."
            else:
                message = f"Removed {entry['path']}, undoing {when}."
        entry["restored"] = True
        _save_journal(entries)

        if (entry_dir / "replaced").exists():
            message += f" The replaced version is kept in {(entry_dir / 'replaced').as_posix()}."
        earlier = sum(1 for e in entries if not e["restored"] and _latest_change([e], restored))
        if earlier:
            message += f" {earlier} earlier {'change' if earlier == 1 else 'changes'} can be undone with restore_file(path='{_rel(restored)}')."
        result = {"success": True, "message": message}
        after_text = _text_or_none(restored)
        if after_text is not None:
            result.update(asdict(_get_file_info(restored)))
            result = _add_report(result, _write_report(restored, None if source else before_text, after_text))
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return _maybe_changed("restore_error", e, f"read_file_with_metadata(path='{path}') or get_outline(path='{path}')")

@mcp.tool()
def get_tools_config() -> str:
    """
    Shows the tools config and what each toolset sees: the project folder, forbidden, allowed and
    read-only paths, ignored folders, the git repository and commands. Use it when a file is not found or a path is denied.
    """
    try:
        gitignore_patterns = load_gitignore_patterns()
        repo_root = get_repo_root(PROJECT_DIR) if PROJECT_DIR.exists() else None

        commands = {"registry": REGISTRY_PATH.as_posix() if REGISTRY_PATH else None,
                    "registry_found": bool(REGISTRY_PATH and REGISTRY_PATH.is_file()),
                    "suggestions_file": COMMAND_SUGGESTIONS.as_posix() if COMMAND_SUGGESTIONS else None}
        if commands["registry_found"]:
            try:
                with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                    commands["command_count"] = len(json.load(f))
            except Exception as e:
                commands["error"] = f"The registry could not be read: {e}"

        return json.dumps({
            "success": True,
            "config_file": CONFIG_PATH.as_posix(),
            "toolsets": {
                "enabled": ENABLED_TOOLSETS,
                "not_served": UNAVAILABLE_TOOLSETS,
                "disabled_tools": sorted(DISABLED_TOOLS)
            },
            "shared": {
                "project_dir": PROJECT_DIR.as_posix(),
                "project_dir_exists": PROJECT_DIR.exists(),
                "forbidden_paths": [display_path(p, PROJECT_DIR) for p in FORBIDDEN_PATHS],
                "allowed_paths": [display_path(p, PROJECT_DIR) for p in ALLOWED_PATHS],
                "read_only_files": [display_path(p, PROJECT_DIR) for p in READ_ONLY_FILES],
                "protected_file_names": sorted(PROTECTED_FILE_NAMES),
                "protected_files": [p.as_posix() for p in PROTECTED_PATHS],
                "max_output_chars": MAX_OUTPUT_CHARS
            },
            "filesystem": {
                "max_read_lines": MAX_READ_LINES,
                "max_line_chars": MAX_LINE_CHARS,
                "backup_folder": BACKUP_DIR.as_posix(),
                "max_backups": MAX_BACKUPS
            },
            "outline_and_search": {
                "ignored_dirs": sorted(IGNORED_DIRS),
                "ignored_dirs_source": "default" if CONFIG.get("ignored_dirs") is None else "tools config",
                "gitignore": {
                    "path": GITIGNORE_PATH.as_posix(),
                    "found": GITIGNORE_PATH.exists(),
                    "patterns": gitignore_patterns
                },
                "outline_file_types": sorted(OUTLINE_SUFFIXES)
            },
            "git": {
                "repository_root": repo_root.as_posix() if repo_root else None
            },
            "commands": commands,
            "web_search": {
                "ollama_api_key_set": OLLAMA_API_KEY is not None,
                "user_config_file": USER_CONFIG_PATH.as_posix()
            },
            "notes": [
                "Only the tools of the enabled toolsets are available, except the disabled_tools. The user chooses them in the config_file. Toolsets in not_served are enabled but not available, for the reason given.",
                "Only paths within project_dir can be accessed. Relative paths given to the tools are resolved against project_dir.",
                "Forbidden paths and everything inside forbidden folders are denied and hidden, and so are protected files: files named in protected_file_names and the protected_files (the config_file, the user_config_file and the command registry).",
                "allowed_paths inside forbidden folders can be used again, unless a forbidden path inside them is more specific. The git tools still leave out the whole forbidden folders.",
                "read_only_files can be read and outlined wherever they are, also in a forbidden folder or outside project_dir, but never changed, moved or deleted. get_outline of the whole project lists the ones outside project_dir last, with their full paths.",
                "Commands are added to the registry only by the user.",
                "restore_file undoes the last max_backups changes of the file tools. Their backups, including deleted files, are kept in backup_folder, which the tools cannot access directly.",
                "The outline and search tools also skip folders named in ignored_dirs at any depth, files matching the .gitignore patterns and, for search, binary files. They do not skip allowed_paths and read_only_files for these reasons.",
                "get_outline lists every file that is not ignored, with the sections of the outline_file_types.",
                "Git's own .gitignore rules decide which untracked files the git tools list. If repository_root is null, project_dir is not in a git repository.",
                "The web search tools are only available if ollama_api_key_set is true: the user sets the key in the user_config_file. The key itself is never shown.",
                "Responses longer than max_output_chars are cut short with an 'Output limited:' message that explains how to see the rest."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2, ensure_ascii=False)
