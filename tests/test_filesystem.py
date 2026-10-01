# --- test_filesystem.py ---
"""
Tests for safe-filesystem-mcp: reading, editing, writing and managing files.
"""
import base64
import json
from pathlib import Path

import pytest

from conftest import content_of, sha_of, windows_only

NL = "\n"

# A file with CRLF line endings, a BOM, quotes, backslashes, a tab and trailing whitespace
TRICKY = ('import os\r\n'
          '\r\n'
          'def greet(name):\r\n'
          '    """Says "hello" to C:\\Users\\name."""\r\n'
          '    msg = f"Hello, {name}!"   \r\n'
          '\tprint(msg)\r\n'
          '    return msg\r\n'
          '\r\n'
          'def other():\r\n'
          '    return 1\r\n')
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

@pytest.fixture
def fs(project):
    project.write("app.py", b"\xef\xbb\xbf" + TRICKY.encode("utf-8"))
    return project.load("filesystem")

def file_bytes(project, rel="app.py") -> bytes:
    return (project.root / rel).read_bytes()

# --- Reading ---

def test_read_returns_the_exact_content_as_plain_text(fs):
    response = fs.read_file_with_metadata("app.py")
    assert content_of(response) + "\n" == TRICKY.replace("\r\n", "\n")
    assert "Line ending: CRLF | Encoding: utf-8 with BOM" in response
    assert "Lines: 1-10 of 10\n" in response

def test_read_a_range_with_line_numbers(fs):
    response = fs.read_file_with_metadata("app.py", start_line=3, end_line=4, line_numbers=True)
    assert content_of(response).split("\n") == ["3| def greet(name):", '4|     """Says "hello" to C:\\Users\\name."""']
    assert "Lines: 3-4 of 10 (partial)" in response
    assert "only part of the file" in response

@pytest.mark.parametrize("args, error", [
    (("app.py", 50), "invalid_range"),
    (("app.py", 5, 2), "invalid_range"),
    (("missing.py",), "file_not_found"),
    ((".",), "is_a_directory"),
])
def test_read_errors(fs, args, error):
    assert fs.read_file_with_metadata(*args).startswith(f"Error ({error})")

def test_read_binary_file_points_to_read_image(project):
    project.write("data.bin", b"\x00\xff\xfe\x00")
    assert "read_image" in project.load("filesystem").read_file_with_metadata("data.bin")

def test_read_empty_file(project):
    project.write("empty.txt", "")
    assert "Lines: 0 (empty file)" in project.load("filesystem").read_file_with_metadata("empty.txt")

def test_long_file_is_read_in_parts(project):
    project.write("big.txt", "".join(f"line {i}\n" for i in range(1, 2501)))
    fs = project.load("filesystem")
    response = fs.read_file_with_metadata("big.txt")
    assert "Output limited: lines 1-1000 of 2500" in response
    assert "start_line=1001" in response
    assert content_of(response).split("\n")[-1] == "line 1000"
    assert "Lines: 1001-2000 of 2500" in fs.read_file_with_metadata("big.txt", start_line=1001)

def test_read_respects_max_output_chars(project):
    project.write("big.txt", "".join(f"value_{i} = {i}\n" for i in range(3000)))
    project.configure(max_output_chars=4000)
    response = project.load("filesystem").read_file_with_metadata("big.txt")
    assert len(response) <= 4000
    assert "Output limited" in response

def test_long_lines_are_shortened_with_an_instruction(project):
    project.write("bundle.min.js", "var x=1;" * 2000 + "\n")
    response = project.load("filesystem").read_file_with_metadata("bundle.min.js")
    assert "Output limited: line 1 is longer than 2000 characters" in response
    assert content_of(response).endswith("[...]")

# --- Editing ---

def test_edit_with_text_copied_from_the_read_response(fs, project):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    response = fs.edit_file("app.py", '    """Says "hello" to C:\\Users\\name."""', '    """Greets the user."""', sha)
    assert response.startswith("- Edited")
    assert '"""Greets the user."""' in content_of(response)
    raw = file_bytes(project)
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\n") == raw.count(b"\r\n")  # BOM and CRLF kept

def test_edit_removes_copied_line_number_prefixes(fs):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    response = fs.edit_file("app.py", "9| def other():\n10|     return 1", "9| def other():\n10|     return 2", sha)
    assert "Line number prefixes were removed" in response
    assert "    return 2" in content_of(response)

def test_edit_ignores_trailing_whitespace(fs):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    response = fs.edit_file("app.py", '    msg = f"Hello, {name}!"\n\tprint(msg)', '    msg = f"Hi, {name}!"\n\tprint(msg)', sha)
    assert "Matched ignoring trailing whitespace" in response

def test_edit_with_wrong_indentation_shows_the_lines_to_copy(fs):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    response = fs.edit_file("app.py", "print(msg)\nreturn msg", "x", sha)
    assert response.startswith("Error (no_match)")
    assert "Lines 6-7 match when indentation is ignored" in response
    assert content_of(response) == "\tprint(msg)\n    return msg"

def test_edit_reports_multiple_matches_with_line_numbers(fs):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    response = fs.edit_file("app.py", "return", "yield", sha)
    assert response.startswith("Error (multiple_matches)") and "[7, 10]" in response

def test_edit_replace_all_and_chained_hash(fs):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    first = fs.edit_file("app.py", "return", "yield", sha, replace_all=True)
    assert "2 replacements" in first
    second = fs.edit_file("app.py", "import os", "import sys", sha_of(first))
    assert second.startswith("- Edited")

@pytest.mark.parametrize("old, new, error", [
    ("", "x", "empty_old_text"),
    ("def other", "def other", "no_change"),
    ("def other():", "# ... rest of the code ...\ndef other():", "placeholder_in_new_text"),
])
def test_edit_rejects_invalid_edits(fs, old, new, error):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    assert fs.edit_file("app.py", old, new, sha).startswith(f"Error ({error})")

def test_edit_with_a_stale_hash_is_refused(fs):
    response = fs.edit_file("app.py", "import os", "import sys", "0" * 64)
    assert response.startswith("Error (hash_mismatch)") and "Current SHA-256" in response

def test_edit_can_delete_lines(fs, project):
    sha = sha_of(fs.read_file_with_metadata("app.py"))
    fs.edit_file("app.py", "import os\n\n", "", sha)
    assert file_bytes(project).decode("utf-8-sig").startswith("def greet")

# --- Writing and creating ---

def test_write_rejects_placeholder_comments(project):
    project.write("long.py", "".join(f"x_{i} = {i}\n" for i in range(40)))
    fs = project.load("filesystem")
    sha = sha_of(fs.read_file_with_metadata("long.py"))
    assert json.loads(fs.write_file("long.py", "x_0 = 0\n// ... existing code ...\n", sha))["error"] == "placeholder_in_content"

def test_write_warns_when_a_file_shrinks_a_lot(project):
    project.write("long.py", "".join(f"x_{i} = {i}\n" for i in range(40)))
    fs = project.load("filesystem")
    sha = sha_of(fs.read_file_with_metadata("long.py"))
    result = json.loads(fs.write_file("long.py", "x_0 = 0\n", sha))
    assert result["success"] and "shrank from 40 to 1 lines" in result["warning"]

def test_write_keeps_line_endings(project):
    project.write("crlf.txt", "a\r\nb\r\n", newline="")
    fs = project.load("filesystem")
    sha = sha_of(fs.read_file_with_metadata("crlf.txt"))
    assert json.loads(fs.write_file("crlf.txt", "x\ny\n", sha))["success"]
    assert file_bytes(project, "crlf.txt") == b"x\r\ny\r\n"

def test_write_to_a_missing_file_points_to_create_file(project):
    assert "create_file" in json.loads(project.load("filesystem").write_file("new.py", "x", "0"))["message"]

def test_create_file_uses_the_line_endings_of_neighbouring_files(project):
    project.write("src/a.py", "a\r\nb\r\n", newline="")
    project.write("src/b.py", "a\r\nb\r\n", newline="")
    fs = project.load("filesystem")
    assert json.loads(fs.create_file("src/new.py", "x\ny\n"))["line_ending"] == "CRLF"
    assert json.loads(fs.create_file("src/new.py", "x"))["error"] == "file_exists"

def test_create_move_and_delete(project):
    fs = project.load("filesystem")
    assert json.loads(fs.create_file("a/b/c.txt", "x"))["success"]
    assert json.loads(fs.move_file("a/b/c.txt", "a/d.txt"))["success"]
    assert json.loads(fs.delete_file("a"))["success"]
    assert not (project.root / "a").exists()

# --- Listing and images ---

def test_list_directory_lists_folders_first(project):
    project.write("b.txt")
    project.write("a/x.txt")
    entries = json.loads(project.load("filesystem").list_directory("."))["entries"]
    assert [e["name"] for e in entries if e["name"] != "tools-config.json"][:2] == ["a", "b.txt"]

def test_large_directory_listing_is_limited(project):
    for i in range(300):
        project.write(f"many/file_{i:03}.txt")
    result = json.loads(project.load("filesystem").list_directory("many"))
    assert result["total_entries"] == 300 and len(result["entries"]) == 250
    assert "pattern='many/*.txt'" in result["output_limited"]

def test_read_image_returns_an_image(project):
    project.write("logo.png", PNG)
    project.write("misnamed.jpg", PNG)
    fs = project.load("filesystem")
    metadata, image = fs.read_image("logo.png")
    assert json.loads(metadata)["mime_type"] == "image/png"
    assert image.data == PNG
    assert json.loads(fs.read_image("misnamed.jpg")[0])["mime_type"] == "image/png"  # detected from the bytes

def test_read_image_rejects_other_files(project):
    project.write("icon.svg", "<svg/>")
    project.write("fake.png", "not an image")
    fs = project.load("filesystem")
    assert "read_file_with_metadata" in json.loads(fs.read_image("icon.svg"))["message"]
    assert json.loads(fs.read_image("fake.png"))["error"] == "unsupported_format"

def test_json_responses_keep_non_ascii_characters_readable(project):
    project.write("ääkköset.txt")
    response = project.load("filesystem").list_directory(".")
    assert "ääkköset.txt" in response and "\\u00e4" not in response

# --- Sections and outlines ---

SERVICE = ('class Service:\n'
           '    """A service."""\n'
           '\n'
           '    def start(self):\n'
           '        return 1\n'
           '\n'
           '    def stop(self):\n'
           '        return 2\n'
           '\n'
           '\n'
           'def main():\n'
           '    Service().start()\n')

@pytest.fixture
def service(project):
    project.write("service.py", SERVICE)
    return project.load("filesystem")

@pytest.mark.parametrize("section", ["Service.stop", "stop", "stop()"])
def test_read_a_section_by_name(service, section):
    response = service.read_file_with_metadata("service.py", section=section)
    assert "Section: stop() (lines 7-8)" in response and "Lines: 7-8 of 12 (partial)" in response
    assert content_of(response) == "    def stop(self):\n        return 2"

def test_read_a_markdown_section_by_heading(project):
    project.write("guide.md", "# Guide\n\n## Install\npip install x\n\n## Use\nRun it.\n")
    response = project.load("filesystem").read_file_with_metadata("guide.md", section="Install")
    assert content_of(response) == "## Install\npip install x"

@pytest.mark.parametrize("kwargs, error, message", [
    ({"section": "missing"}, "section_not_found", "Sections in the file: 'Service', 'Service.start', 'Service.stop', 'main'. Use get_outline(path='service.py')"),
    ({"section": "stop", "start_line": 1}, "invalid_arguments", "not both"),
])
def test_section_read_errors(service, kwargs, error, message):
    response = service.read_file_with_metadata("service.py", **kwargs)
    assert response.startswith(f"Error ({error}):") and message in response

def test_sections_need_an_outlined_file_type(project):
    project.write("config.toml", "a = 1\n")
    response = project.load("filesystem").read_file_with_metadata("config.toml", section="a")
    assert response.startswith("Error (no_outline):") and "start_line and end_line" in response

def test_long_section_is_read_in_parts_within_the_section(project):
    project.write("big.py", "def big():\n" + "".join(f"    x_{i} = {i}\n" for i in range(1500)) + "\ndef after():\n    pass\n")
    response = project.load("filesystem").read_file_with_metadata("big.py", section="big")
    assert "Read the next part with start_line=1001, end_line=1501" in response

def test_edit_shows_the_outline_and_warns_about_removed_sections(service):
    sha = sha_of(service.read_file_with_metadata("service.py"))
    response = service.edit_file("service.py", "    def stop(self):\n        return 2\n", "", sha)
    assert "Warning: these sections are no longer in the file: class Service > stop()." in response
    assert response.endswith("Outline after the edit:\n\n```text\n1-5 class Service\n  4-5 start()\n9-10 main()\n```")

def test_edit_reports_added_sections_without_a_warning(service):
    sha = sha_of(service.read_file_with_metadata("service.py"))
    response = service.edit_file("service.py", "def main():", "def helper():\n    pass\n\n\ndef main():", sha)
    assert "Warning" not in response and response.endswith("Added sections: helper()")

def test_edit_warns_when_the_file_no_longer_parses(service):
    sha = sha_of(service.read_file_with_metadata("service.py"))
    response = service.edit_file("service.py", "def main():", "def main(:", sha)
    assert "Warning: the file could be outlined before this change, but not anymore: Python syntax error at line 11" in response

def test_edit_of_a_file_without_an_outline_has_no_outline(project):
    project.write("notes.txt", "a\nb\n")
    fs = project.load("filesystem")
    response = fs.edit_file("notes.txt", "a", "c", sha_of(fs.read_file_with_metadata("notes.txt")))
    assert "Outline" not in response

def test_write_file_reports_removed_sections(service):
    sha = sha_of(service.read_file_with_metadata("service.py"))
    result = json.loads(service.write_file("service.py", "def main():\n    pass\n", sha))
    assert result["success"] and result["outline"] == ["1-2 main()"]
    assert result["removed_sections"] == ["class Service", "class Service > start()", "class Service > stop()"]
    assert "no longer in the file" in result["warning"]

def test_create_file_shows_the_outline(project):
    result = json.loads(project.load("filesystem").create_file("new.md", "# Title\n## Part\n"))
    assert result["outline"] == ["1-2 # Title", "  2-2 ## Part"] and "warning" not in result

def test_responses_show_paths_relative_to_the_allowed_directory(service):
    response = service.read_file_with_metadata("service.py")
    assert response.startswith("- File: `service.py`\n")
    edit = service.edit_file("service.py", "return 1", "return 3", sha_of(response))
    assert edit.startswith("- Edited `service.py`:")
    assert json.loads(service.write_file("service.py", SERVICE, sha_of(edit)))["path"] == "service.py"

def test_edit_response_stays_within_the_limit(project):
    project.write("wide.py", "".join(f"x_{i} = '{'a' * 150}'\n" for i in range(60)))
    project.configure(max_output_chars=2000)
    fs = project.load("filesystem")
    sha = sha_of(fs.read_file_with_metadata("wide.py", end_line=1))
    old = "".join(f"x_{i} = '{'a' * 150}'\n" for i in range(40))
    response = fs.edit_file("wide.py", old, old.replace("'a", "'b"), sha)
    assert len(response) <= 2000 and "Output limited: the change continues after line" in response

def test_edit_warns_when_a_ts_file_gets_a_syntax_error(project):
    project.write("app.ts", "export function a() {\n  return 1;\n}\n")
    fs = project.load("filesystem")
    response = fs.edit_file("app.ts", "  return 1;\n}", "  return 1;", sha_of(fs.read_file_with_metadata("app.ts")))
    assert "Warning: JS/TS syntax error near line" in response

def test_move_creates_missing_destination_folders(project):
    project.write("a.txt", "x")
    fs = project.load("filesystem")
    assert json.loads(fs.move_file("a.txt", "new/deep/a.txt"))["success"]
    assert (project.root / "new/deep/a.txt").read_text() == "x"

def test_folder_cannot_be_moved_into_itself(project):
    project.write("mv/a.txt", "x")
    fs = project.load("filesystem")
    assert json.loads(fs.move_file("mv", "mv/deep/er/mv"))["error"] == "invalid_destination"
    assert not (project.root / "mv/deep").exists()

@windows_only
def test_rename_that_only_changes_letter_case(project):
    project.write("app.py", "x")
    fs = project.load("filesystem")
    assert json.loads(fs.move_file("app.py", "App.py"))["success"]
    assert [p.name for p in project.root.iterdir() if p.suffix == ".py"] == ["App.py"]


# --- Formatting for the console ---

def test_content_is_a_code_block_that_contains_code_blocks(project):
    text = "# Guide" + NL + NL + "```python" + NL + "print(1)" + NL + "```" + NL
    project.write("guide.md", text)
    response = project.load("filesystem").read_file_with_metadata("guide.md")
    assert "````markdown" + NL in response  # a longer fence than the one inside
    assert content_of(response) + NL == text

def test_response_parts_are_separated_by_blank_lines(fs):
    response = fs.read_file_with_metadata("app.py")
    header, block = response.split(NL + NL, 1)
    assert all(line.startswith("- ") for line in header.split(NL))
    assert block.startswith("```python" + NL)

def test_edit_ignores_copied_code_block_fences(project):
    project.write("a.py", "x = 1" + NL + "y = 2" + NL)
    fs = project.load("filesystem")
    sha = sha_of(fs.read_file_with_metadata("a.py"))
    response = fs.edit_file("a.py", "```python" + NL + "x = 1" + NL + "```", "```python" + NL + "x = 3" + NL + "```", sha)
    assert "Code block fence lines were removed" in response
    assert (project.root / "a.py").read_text() == "x = 3" + NL + "y = 2" + NL

# --- Restoring changes ---

def restore(fs, path: str) -> dict:
    return json.loads(fs.restore_file(path))

def test_restore_undoes_edits_one_at_a_time(project):
    project.write("a.py", "def one():\n    return 1\n")
    fs = project.load("filesystem")
    sha = sha_of(fs.edit_file("a.py", "return 1", "return 2", sha_of(fs.read_file_with_metadata("a.py"))))
    fs.edit_file("a.py", "def one():\n    return 2\n", "", sha)
    result = restore(fs, "a.py")
    assert result["success"] and "before edit_file" in result["message"] and "1 earlier change can be undone" in result["message"]
    assert (project.root / "a.py").read_text() == "def one():\n    return 2\n"
    assert result["sha256"] == sha and result["outline"] == ["1-2 one()"]
    restore(fs, "a.py")
    assert (project.root / "a.py").read_text() == "def one():\n    return 1\n"
    assert restore(fs, "a.py")["error"] == "no_backup"

def test_restore_keeps_line_endings_and_bom_exactly(fs, project):
    original = file_bytes(project)
    assert json.loads(fs.write_file("app.py", "x = 1\n", sha_of(fs.read_file_with_metadata("app.py"))))["success"]
    restore(fs, "app.py")
    assert file_bytes(project) == original

def test_restore_removes_a_created_file(project):
    fs = project.load("filesystem")
    fs.create_file("src/new.py", "x = 1\n")
    assert "Removed src/new.py, undoing create_file" in restore(fs, "src/new.py")["message"]
    assert not (project.root / "src/new.py").exists()

def test_restore_brings_back_a_deleted_folder(project):
    project.write("pkg/a.py", "a\n")
    project.write("pkg/sub/b.py", "b\n")
    fs = project.load("filesystem")
    assert "restore_file(path='pkg')" in json.loads(fs.delete_file("pkg"))["message"]
    assert not (project.root / "pkg").exists()
    assert restore(fs, "pkg/sub/b.py")["success"]  # a path inside the deleted folder restores the folder
    assert (project.root / "pkg/a.py").read_text() == "a\n" and (project.root / "pkg/sub/b.py").read_text() == "b\n"

@pytest.mark.parametrize("path", ["old/a.py", "new/a.py"])
def test_restore_moves_a_file_back_by_either_path(project, path):
    project.write("old/a.py", "a\n")
    fs = project.load("filesystem")
    fs.move_file("old/a.py", "new/a.py")
    assert "Moved new/a.py back to old/a.py" in restore(fs, path)["message"]
    assert (project.root / "old/a.py").exists() and not (project.root / "new/a.py").exists()

def test_restore_undoes_changes_in_reverse_order_across_moves(project):
    project.write("a.py", "one\n")
    fs = project.load("filesystem")
    fs.write_file("a.py", "two\n", sha_of(fs.read_file_with_metadata("a.py")))
    fs.move_file("a.py", "b.py")
    restore(fs, "b.py")
    restore(fs, "a.py")
    assert (project.root / "a.py").read_text() == "one\n" and not (project.root / "b.py").exists()

def test_restore_keeps_the_version_it_replaces(project):
    project.write("a.txt", "one\n")
    fs = project.load("filesystem")
    fs.write_file("a.txt", "two\n", sha_of(fs.read_file_with_metadata("a.txt")))
    (project.root / "a.txt").write_text("changed by the user\n")
    message = restore(fs, "a.txt")["message"]
    kept = message.split("is kept in ")[1][:-1]
    assert Path(kept).read_text() == "changed by the user\n"

def test_restore_without_backup_lists_the_paths_that_have_one(project):
    fs = project.load("filesystem")
    fs.create_file("x.txt", "x")
    result = restore(fs, "y.txt")
    assert result["error"] == "no_backup" and "can be undone: x.txt" in result["message"]

def test_backups_are_kept_outside_the_project_by_default(project):
    fs = project.load("filesystem")
    fs.create_file("x.txt", "x")
    folder = Path(json.loads(fs.get_config())["filesystem"]["backup_folder"])
    assert folder.parent == project.root.parent / "home" / ".ollmcp-tools-backups" and folder.name.startswith("project-")
    assert (folder / "journal.json").is_file()

def test_old_backups_are_removed(project, monkeypatch):
    fs = project.load("filesystem")
    monkeypatch.setattr(fs, "MAX_BACKUPS", 3)
    for i in range(5):
        fs.create_file(f"f{i}.txt", "x")
    assert len(fs._load_journal()) == 3 and len([p for p in fs.BACKUP_DIR.iterdir() if p.is_dir()]) == 3
    assert restore(fs, "f0.txt")["error"] == "no_backup" and restore(fs, "f4.txt")["success"]

def test_failed_backup_leaves_the_file_unchanged(project, monkeypatch):
    project.write("a.txt", "one\n")
    fs = project.load("filesystem")
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(fs, "_record_change", fail)
    assert json.loads(fs.delete_file("a.txt"))["error"] == "backup_error"
    assert fs.edit_file("a.txt", "one", "two", sha_of(fs.read_file_with_metadata("a.txt"))).startswith("Error (backup_error)")
    assert (project.root / "a.txt").read_text() == "one\n"
