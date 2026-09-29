# --- test_context.py ---
"""
Tests for context-record-mcp: context files and read-only files.
"""
import pytest

from conftest import start_server

@pytest.fixture
def context(project):
    project.write("_context/plan.md", "# Plan\n- step 1\n")
    project.write("_context/App-Instructions.md", "SHADOW COPY\n")  # same name as the read-only file, other case
    project.write("_instructions/app-instructions.md", "# Instructions\nFollow the practices.\n")
    project.configure(context_folder="_context", read_only_files=["_instructions/app-instructions.md"])
    return project.load("context")

def test_read_only_files_are_listed_first_and_shadow_copies_are_hidden(context):
    assert context.list_context_files().split("\n") == ["app-instructions.md (read-only)", "plan.md"]

def test_read_all_starts_with_the_read_only_files(context):
    response = context.read_all_context_files()
    headers = [line[6:] for line in response.splitlines() if line.startswith("FILE: ")]
    assert headers == ["app-instructions.md (read-only)", "plan.md"]
    assert "SHADOW COPY" not in response

def test_read_only_files_are_read_by_name_in_any_case(context):
    assert context.read_context_file("APP-INSTRUCTIONS.MD").startswith("# Instructions")

def test_read_only_files_cannot_be_changed(context, project):
    for result in [context.write_context_file("app-instructions.md", "x"),
                   context.append_to_context_file("App-Instructions.md", "x"),
                   context.remove_context_file("app-instructions.md")]:
        assert "is read-only" in result
    assert (project.root / "_instructions/app-instructions.md").read_text() == "# Instructions\nFollow the practices.\n"

def test_write_append_read_and_remove(context):
    assert context.write_context_file("notes.md", "# Notes").startswith("Successfully")
    assert context.append_to_context_file("notes.md", "- more").startswith("Successfully")
    assert context.read_context_file("notes.md") == "# Notes\n- more"
    assert context.remove_context_file("notes.md").startswith("Successfully")
    assert context.read_context_file("notes.md").startswith("Error: File 'notes.md' not found")

def test_read_all_is_limited_but_keeps_read_only_files(project):
    for i in range(4):
        project.write(f"_context/note_{i}.md", f"# Note {i}\n" + "Some planning text.\n" * 120)
    project.write("instructions.md", "# Instructions\n")
    project.configure(context_folder="_context", read_only_files=["instructions.md"], max_output_chars=4000)
    response = project.load("context").read_all_context_files()
    assert len(response) <= 4000
    assert "FILE: instructions.md (read-only)" in response
    assert "Output limited:" in response and "read_context_file" in response

def test_missing_context_folder_setting(project):
    assert "'context_folder' is not set" in project.load("context").list_context_files()

@pytest.mark.parametrize("files, message", [
    (["missing.md"], "file not found"),
    (["a/plan.md", "b/plan.md"], "two files are named 'plan.md'"),
])
def test_invalid_read_only_files_stop_the_server(project, files, message):
    project.write("a/plan.md", "x")
    project.write("b/plan.md", "y")
    result = start_server("context", project.configure(read_only_files=files))
    assert result.returncode != 0 and message in result.stderr
