# --- test_protection.py ---
"""
Tests for the access rules shared by all servers: allowed_dir, forbidden paths and protected files
(.mcp.json and the tools config file).
"""
import json

import pytest

from conftest import requires_git, windows_only

SECRET = "SECRET-CONTENT"

@pytest.fixture
def protected_project(project):
    project.write(".mcp.json", f'{{"servers": "{SECRET}"}}')
    project.write("sub/.MCP.json", f'{{"servers": "{SECRET}"}}')
    project.write("src/app.py", 'print("hello")\n')
    project.write("src/data.json", '{"a": 1}\n')
    project.write("secret/keys.txt", SECRET + "\n")
    project.configure(forbidden_paths=["secret"])
    return project

def denied(response: str) -> bool:
    text = response if response.startswith("Error") else json.loads(response).get("error", "")
    return "access_denied" in text

# --- Filesystem ---

@pytest.mark.parametrize("path", [".mcp.json", ".MCP.JSON", "sub/.MCP.json", "tools-config.json", "secret/keys.txt"])
def test_protected_and_forbidden_files_cannot_be_read(protected_project, path):
    assert denied(protected_project.load("filesystem").read_file_with_metadata(path))

def test_protected_files_cannot_be_changed(protected_project):
    fs = protected_project.load("filesystem")
    assert denied(fs.write_file(".mcp.json", "{}", "x"))
    assert denied(fs.edit_file(".mcp.json", "a", "b", "x"))
    assert denied(fs.delete_file(".mcp.json"))
    assert denied(fs.delete_file("tools-config.json"))
    assert denied(fs.create_file("src/.mcp.json", "{}"))
    assert denied(fs.move_file("src/data.json", "src/.mcp.json"))
    assert SECRET in (protected_project.root / ".mcp.json").read_text()

def test_folders_containing_protected_or_forbidden_files_cannot_be_moved_or_deleted(protected_project):
    fs = protected_project.load("filesystem")
    assert denied(fs.delete_file("sub"))
    assert denied(fs.move_file("sub", "sub2"))
    assert denied(fs.delete_file("."))

def test_protected_and_forbidden_paths_are_hidden_from_listings(protected_project):
    names = [e["name"] for e in json.loads(protected_project.load("filesystem").list_directory("."))["entries"]]
    assert ".mcp.json" not in names and "tools-config.json" not in names and "secret" not in names
    assert "src" in names

@windows_only
@pytest.mark.parametrize("path", [".mcp.json.", ".mcp.json ", ".mcp.json::$DATA"])
def test_windows_aliases_of_protected_files_are_denied(protected_project, path):
    fs = protected_project.load("filesystem")
    assert denied(fs.read_file_with_metadata(path))
    assert denied(fs.create_file(f"src/{path}", "{}"))
    assert not (protected_project.root / "src" / ".mcp.json").exists()

def test_paths_outside_allowed_dir_are_denied(protected_project, tmp_path):
    (tmp_path / "outside.txt").write_text("x")
    fs = protected_project.load("filesystem")
    assert denied(fs.read_file_with_metadata("../outside.txt"))
    assert denied(fs.read_file_with_metadata(str(tmp_path / "outside.txt")))

def test_relative_paths_resolve_against_allowed_dir_not_the_working_directory(protected_project, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cwd_only.py").write_text("x")  # exists only in the working directory
    fs = protected_project.load("filesystem")
    assert "Lines: 1-1 of 1" in fs.read_file_with_metadata("src/app.py")
    assert fs.read_file_with_metadata("src/cwd_only.py").startswith("Error (file_not_found)")

# --- Map and search ---

def test_protected_and_forbidden_files_are_hidden_from_maps_and_searches(protected_project):
    tree = protected_project.load("map").get_outline()
    assert ".mcp.json" not in tree.lower() and "tools-config.json" not in tree and "secret" not in tree
    search = protected_project.load("search")
    assert search.search_text_in_files(SECRET).startswith("No matches")
    matches = search.search_files_by_pattern("*.json", recursive=True)
    assert "src/data.json" in matches and ".mcp.json" not in matches.lower() and "tools-config.json" not in matches

def test_forbidden_path_cannot_be_searched_or_outlined(protected_project):
    assert "Path is forbidden" in protected_project.load("search").search_text_in_files("x", path="secret")
    assert "Path is forbidden" in protected_project.load("map").get_outline("secret")

# --- Git ---

@requires_git
def test_protected_and_forbidden_files_are_left_out_of_git_output(protected_project):
    protected_project.init_git()
    for rel in [".mcp.json", "sub/.MCP.json", "secret/keys.txt", "src/app.py"]:
        with open(protected_project.root / rel, "a") as f:
            f.write("changed\n")
    protected_project.configure(forbidden_paths=["secret"], max_output_chars=40000)  # a valid change to the config file
    git = protected_project.load("git")
    changes = json.loads(git.get_diff("head"))
    assert changes["changed_files"] == ["M\tsrc/app.py"]
    assert SECRET not in changes["diff"]
    status = json.loads(git.get_git_status())["stdout"]
    assert "src/app.py" in status and ".mcp.json" not in status.lower() and "secret" not in status and "tools-config" not in status
    assert denied(git.get_diff("head", ".mcp.json"))
    assert denied(git.get_file_history("secret/keys.txt"))

# --- Context ---

@pytest.mark.parametrize("filename", ["../.mcp.json", "..\\x.md", ".mcp.json", "x:y.md", "sub/notes.md", "notes.txt"])
def test_context_files_must_be_plain_markdown_names(project, filename):
    project.write("_context/notes.md", "# notes\n")
    project.configure(context_folder="_context")
    context = project.load("context")
    assert context.read_context_file(filename).startswith("Error")
    assert context.write_context_file(filename, "x").startswith("Error")
