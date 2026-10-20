# --- test_protection.py ---
"""
Tests for the access rules shared by all toolsets: project_dir, forbidden, allowed and read-only paths and protected
files (.mcp.json, the tools config file, the user config file and the command registry).
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
    assert denied(fs.write_file("src/.mcp.json", "{}"))
    assert denied(fs.move_file("src/data.json", "src/.mcp.json"))
    assert SECRET in (protected_project.root / ".mcp.json").read_text()

def test_folders_containing_protected_or_forbidden_files_cannot_be_moved_or_deleted(protected_project):
    fs = protected_project.load("filesystem")
    assert denied(fs.delete_file("sub"))
    assert denied(fs.move_file("sub", "sub2"))
    assert denied(fs.delete_file("."))

def test_a_backup_folder_inside_the_project_is_hidden_and_protected(project):
    project.write("src/a.py", "x = 1\n")
    project.configure(backup_folder="backups")
    fs = project.load("filesystem")
    fs.delete_file("src/a.py")
    assert (project.root / "backups" / "journal.json").is_file()
    assert "backup folder of restore_file" in fs.read_file_with_metadata("backups/journal.json")
    assert denied(fs.delete_file(".")) and denied(fs.move_file("backups", "elsewhere"))
    assert "backups" not in project.load("outline").get_outline()
    assert project.load("search").search_relevant_files("journal restored").startswith("- No files contain")
    assert json.loads(fs.restore_file("src/a.py"))["success"] and (project.root / "src/a.py").exists()

@windows_only
@pytest.mark.parametrize("path", [".mcp.json.", ".mcp.json ", ".mcp.json::$DATA"])
def test_windows_aliases_of_protected_files_are_denied(protected_project, path):
    fs = protected_project.load("filesystem")
    assert denied(fs.read_file_with_metadata(path))
    assert denied(fs.write_file(f"src/{path}", "{}"))
    assert not (protected_project.root / "src" / ".mcp.json").exists()

def test_paths_outside_project_dir_are_denied(protected_project, tmp_path):
    (tmp_path / "outside.txt").write_text("x")
    fs = protected_project.load("filesystem")
    assert denied(fs.read_file_with_metadata("../outside.txt"))
    assert denied(fs.read_file_with_metadata(str(tmp_path / "outside.txt")))

def test_relative_paths_resolve_against_project_dir_not_the_working_directory(protected_project, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cwd_only.py").write_text("x")  # exists only in the working directory
    fs = protected_project.load("filesystem")
    assert "Lines: 1-1 of 1" in fs.read_file_with_metadata("src/app.py")
    assert fs.read_file_with_metadata("src/cwd_only.py").startswith("Error (file_not_found)")

@pytest.fixture
def registry_project(project):
    project.write("_commands/registry.json", json.dumps({"x": {"template": "python", "description": SECRET}}))
    project.write("src/app.py", 'print("hello")\n')
    project.configure(command_registry="_commands/registry.json")
    return project

def test_the_command_registry_is_protected(registry_project):
    fs = registry_project.load("filesystem")
    assert denied(fs.read_file_with_metadata("_commands/registry.json"))
    assert denied(fs.read_file_with_metadata("_COMMANDS/Registry.JSON"))
    assert denied(fs.write_file("_commands/registry.json", "{}", "x"))
    assert denied(fs.move_file("src/app.py", "_commands/registry.json"))
    assert denied(fs.delete_file("_commands"))
    assert SECRET in (registry_project.root / "_commands/registry.json").read_text()
    assert any(p.endswith("_commands/registry.json") for p in json.loads(fs.get_tools_config())["shared"]["protected_files"])
    assert "registry.json" not in registry_project.load("outline").get_outline()
    assert registry_project.load("search").search_text_in_files(SECRET).startswith("- No matches")

def test_the_user_config_is_protected_also_inside_project_dir(make_project):
    """The user config file is normally outside the project, but a project can be the home folder itself."""
    project = make_project("home")
    project.user_config(ollama_api_key=SECRET)
    project.configure()
    fs = project.load("filesystem")
    assert denied(fs.read_file_with_metadata(".ollmcp-tools/user-config.json"))
    assert denied(fs.write_file(".ollmcp-tools/user-config.json", "{}", "x"))
    assert project.load("search").search_text_in_files(SECRET).startswith("- No matches")

def test_a_missing_command_registry_cannot_be_created(project):
    project.configure(command_registry="_commands/registry.json")
    fs = project.load("filesystem")
    assert denied(fs.write_file("_commands/registry.json", "{}"))
    assert not (project.root / "_commands/registry.json").exists()

# --- Map and search ---

def test_protected_and_forbidden_files_are_hidden_from_maps_and_searches(protected_project):
    tree = protected_project.load("outline").get_outline()
    assert ".mcp.json" not in tree.lower() and "tools-config.json" not in tree and "secret" not in tree
    search = protected_project.load("search")
    assert search.search_text_in_files(SECRET).startswith("- No matches")
    assert search.search_relevant_files(SECRET).startswith("- No files contain")
    matches = search.search_files_by_pattern("*.json", recursive=True)
    assert "src/data.json" in matches and ".mcp.json" not in matches.lower() and "tools-config.json" not in matches

def test_forbidden_path_cannot_be_searched_or_outlined(protected_project):
    assert "Path is forbidden" in protected_project.load("search").search_text_in_files("x", path="secret")
    assert "Path is forbidden" in protected_project.load("search").search_relevant_files("key", path="secret")
    assert "Path is forbidden" in protected_project.load("outline").get_outline("secret")

# --- Git ---

@requires_git
def test_protected_and_forbidden_files_are_left_out_of_git_output(protected_project):
    protected_project.init_git()
    for rel in [".mcp.json", "sub/.MCP.json", "secret/keys.txt", "src/app.py"]:
        with open(protected_project.root / rel, "a") as f:
            f.write("changed\n")
    protected_project.write("new/.mcp.json", SECRET)  # an untracked protected file
    protected_project.write("new/app.py", "x\n")
    protected_project.configure(forbidden_paths=["secret"], max_output_chars=40000)  # a valid change to the config file
    git = protected_project.load("git")
    changes = json.loads(git.get_git_diff("head"))
    assert changes["changed_files"] == ["M\tsrc/app.py"] and changes["untracked_files"] == ["new/app.py"]
    assert SECRET not in changes["diff"]
    assert denied(git.get_git_diff("head", ".mcp.json"))
    assert denied(git.get_git_history("secret/keys.txt"))

@requires_git
def test_the_command_registry_is_left_out_of_git_output(registry_project):
    registry_project.init_git()
    with open(registry_project.root / "_commands/registry.json", "a") as f:
        f.write("\n")
    git = registry_project.load("git")
    assert json.loads(git.get_git_diff("head"))["changed_files"] == []
    assert "registry" not in json.loads(git.get_git_diff("unstaged"))["diff"]
    assert denied(git.get_git_history("_commands/registry.json"))

# --- Allowed paths ---

@pytest.fixture
def allowed_project(project):
    """A forbidden, gitignored tools folder with an allowed plans folder in it, as init creates it."""
    project.write(".ollmcp-tools/context/plan.md", "# Plan\n## Steps\n")
    project.write(".ollmcp-tools/notes.md", SECRET + "\n")
    project.write(".ollmcp-tools/context/private/key.md", SECRET + "\n")
    project.write(".gitignore", ".ollmcp-tools\n")
    project.write("src/app.py", "x = 1\n")
    project.configure(forbidden_paths=[".ollmcp-tools", ".ollmcp-tools/context/private"], allowed_paths=[".ollmcp-tools/context"])
    return project

def test_allowed_paths_inside_a_forbidden_folder_can_be_used(allowed_project):
    fs = allowed_project.load("filesystem")
    assert "# Plan" in fs.read_file_with_metadata(".ollmcp-tools/context/plan.md")
    assert json.loads(fs.write_file(".ollmcp-tools/context/new.md", "# New\n"))["success"]
    assert json.loads(fs.delete_file(".ollmcp-tools/context/new.md"))["success"]
    assert denied(fs.read_file_with_metadata(".ollmcp-tools/notes.md"))
    assert denied(fs.write_file(".ollmcp-tools/other.md", "x"))
    assert denied(fs.delete_file(".ollmcp-tools"))

def test_a_forbidden_path_inside_an_allowed_one_stays_forbidden(allowed_project):
    fs = allowed_project.load("filesystem")
    assert denied(fs.read_file_with_metadata(".ollmcp-tools/context/private/key.md"))
    assert denied(fs.delete_file(".ollmcp-tools/context"))  # it contains the forbidden folder
    assert allowed_project.load("search").search_text_in_files(SECRET).startswith("- No matches")

def test_outline_and_search_show_allowed_paths_despite_forbidden_and_gitignored_folders(allowed_project):
    outline = allowed_project.load("outline")
    tree = outline.get_outline()
    assert ".ollmcp-tools/context/plan.md" in tree and "## Steps" in tree and "src/app.py" in tree
    assert "notes.md" not in tree and "private" not in tree
    folder = outline.get_outline(".ollmcp-tools")  # the forbidden folder shows only what is allowed in it
    assert ".ollmcp-tools/context/plan.md" in folder and "notes.md" not in folder
    search = allowed_project.load("search")
    assert ".ollmcp-tools/context/plan.md:2:" in search.search_text_in_files("Steps")
    assert ".ollmcp-tools/context/plan.md" in search.search_files_by_pattern("*.md", recursive=True)
    assert "plan.md" in search.search_relevant_files("plan steps", path=".ollmcp-tools/context")

@requires_git
def test_the_git_tools_say_that_they_leave_out_allowed_paths_in_forbidden_folders(allowed_project):
    allowed_project.init_git()
    result = json.loads(allowed_project.load("git").get_git_diff("head", ".ollmcp-tools/context/plan.md"))
    assert result["error"] == "forbidden_folder" and "read_file_with_metadata" in result["message"]

# --- Read-only files ---

@pytest.fixture
def read_only_project(project, tmp_path):
    """Read-only files in a normal folder, in a forbidden folder and outside the project."""
    project.write("docs/rules.md", "# Rules\n## Naming\n")
    project.write(".ollmcp-tools/instructions.md", "# Instructions\n")
    project.write("src/app.py", "x = 1\n")
    outside = tmp_path / "shared" / "guide.md"
    outside.parent.mkdir()
    outside.write_text("# Guide\n## Style\n", encoding="utf-8")
    project.configure(forbidden_paths=[".ollmcp-tools"], read_only_files=["docs/rules.md", ".ollmcp-tools/instructions.md", str(outside)])
    project.outside = outside
    return project

def test_read_only_files_are_readable_wherever_they_are(read_only_project):
    fs = read_only_project.load("filesystem")
    for path in ["docs/rules.md", ".ollmcp-tools/instructions.md", str(read_only_project.outside)]:
        assert "SHA-256" in fs.read_file_with_metadata(path), path
    assert "## Style" in read_only_project.load("search").search_text_in_files("Style", path=str(read_only_project.outside))

def test_read_only_files_are_never_changed(read_only_project):
    fs = read_only_project.load("filesystem")
    sha = fs.read_file_with_metadata("docs/rules.md").split("SHA-256: ")[1].split()[0]
    for response in [fs.write_file("docs/rules.md", "x", sha), fs.edit_file("docs/rules.md", "Rules", "x", sha),
                     fs.write_file(str(read_only_project.outside), "x", "0"), fs.delete_file("docs/rules.md"),
                     fs.delete_file("docs"), fs.move_file("docs", "docs2"), fs.move_file("src/app.py", "docs/rules.md"),
                     fs.restore_file("docs/rules.md")]:
        assert "read-only" in response and "Nothing was changed" in response, response
    assert (read_only_project.root / "docs/rules.md").read_text() == "# Rules\n## Naming\n"

def test_the_outline_lists_read_only_files_and_those_outside_the_project_last(read_only_project):
    tree = read_only_project.load("outline").get_outline()
    lines = tree.splitlines()
    outside = read_only_project.outside.resolve().as_posix()
    assert any(line.startswith("docs/rules.md (") and "read-only" in line for line in lines)
    assert any(line.startswith(".ollmcp-tools/instructions.md (") and "read-only" in line for line in lines)
    assert any(line.startswith(f"{outside} (") and "read-only" in line for line in lines)
    assert tree.index(outside) > tree.index("src/app.py") and "## Style" in tree
    assert "## Style" in read_only_project.load("outline").get_outline(str(read_only_project.outside))

def test_get_tools_config_shows_allowed_paths_and_read_only_files(read_only_project):
    shared = json.loads(read_only_project.load("filesystem").get_tools_config())["shared"]
    assert shared["read_only_files"][:2] == ["docs/rules.md", ".ollmcp-tools/instructions.md"]
    assert shared["allowed_paths"] == []
