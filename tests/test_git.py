# --- test_git.py ---
"""
Tests for git-diff-mcp: diffs, history and status.
"""
import json

import pytest

from conftest import requires_git

pytestmark = requires_git

@pytest.fixture
def repo(project):
    project.write("src/app.py", "print(1)\n")
    project.write("src/ääkköset.py", "x = 1\n")
    project.write("docs/readme.md", "# Docs\n")
    project.configure()
    project.init_git()
    project.write("src/app.py", "print(1)\nprint(2)\n")
    project.write("src/ääkköset.py", "x = 1\ny = 2\n")
    project.write("src/new.py", "new = True\n")
    return project

def test_file_diff(repo):
    result = json.loads(repo.load("git").get_file_diff("src/app.py", "unstaged"))
    assert result["success"] and "+print(2)" in result["stdout"]

def test_file_diff_without_changes_and_of_a_missing_file(repo):
    git = repo.load("git")
    assert json.loads(git.get_file_diff("docs/readme.md", "head"))["message"] == "No differences detected."
    assert json.loads(git.get_file_diff("missing.py", "head"))["error"] == "file_not_found"
    assert json.loads(git.get_file_diff("src/app.py", "wrong"))["error"] == "invalid_mode"

def test_all_changes_lists_changed_and_untracked_files(repo):
    result = json.loads(repo.load("git").get_all_changes_diff("head"))
    assert result["changed_files"] == ["M\tsrc/app.py", "M\tsrc/ääkköset.py"]
    assert result["untracked_files"] == ["src/new.py"]
    assert result["output_limited"] is None

def test_all_changes_in_staged_mode(repo):
    repo.git("add", "src/app.py")
    result = json.loads(repo.load("git").get_all_changes_diff("staged"))
    assert result["changed_files"] == ["M\tsrc/app.py"] and result["untracked_files"] == []

def test_all_changes_limited_to_a_folder(repo):
    result = json.loads(repo.load("git").get_all_changes_diff("head", "docs"))
    assert result["changed_files"] == [] and result["message"] == "No differences detected."

def test_file_history_and_limit_below_one(repo):
    git = repo.load("git")
    history = json.loads(git.get_file_history("src/app.py"))
    assert history["success"] and "init" in history["stdout"]
    assert json.loads(git.get_file_history("src/app.py", 0))["stdout"].count("commit ") == 1
    assert "No commit history" in json.loads(git.get_file_history("src/new.py"))["message"]

def test_status_keeps_non_ascii_names_readable(repo):
    response = repo.load("git").get_git_status()
    assert "ääkköset.py" in response and "\\u00e4" not in response

def test_folder_outside_a_repository(project):
    project.write("src/app.py", "x\n")
    assert json.loads(project.load("git").get_git_status())["error"] == "not_a_repository"

def test_allowed_dir_as_a_repository_subfolder_limits_the_output(make_project):
    repo = make_project("repo")
    repo.write("inside/app.py", "x\n")
    repo.write("outside/other.py", "y\n")
    repo.init_git()
    repo.write("inside/app.py", "x\nx2\n")
    repo.write("outside/other.py", "y\ny2\n")
    repo.config_path = repo.root / "inside" / "tools-config.json"
    repo.config_path.write_text('{"allowed_dir": "."}', encoding="utf-8")
    result = json.loads(repo.load("git").get_all_changes_diff("head"))
    assert result["changed_files"] == ["M\tinside/app.py"]

def test_long_diff_is_limited_with_an_instruction(repo):
    repo.write("src/app.py", "".join(f"line_{i} = {i}\n" for i in range(3000)))
    repo.configure(max_output_chars=4000)
    git = repo.load("git")
    diff = json.loads(git.get_file_diff("src/app.py", "unstaged"))
    assert "read_file_with_metadata" in diff["output_limited"]
    changes = git.get_all_changes_diff("unstaged")
    assert len(changes) <= 4000 and "get_file_diff" in json.loads(changes)["output_limited"]
