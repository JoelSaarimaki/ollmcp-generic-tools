# --- test_context.py ---
"""
Tests for context-record-mcp: context files and read-only files, their outlines and section reads.
"""
import pytest

from conftest import content_of, start_server

PLAN = "# Plan\n\n## Goals\n- one\n- two\n\n## Next steps\n- step 1\n"

@pytest.fixture
def context(project):
    project.write("_context/plan.md", PLAN)
    project.write("_context/App-Instructions.md", "SHADOW COPY\n")  # same name as the read-only file, other case
    project.write("_instructions/app-instructions.md", "# Instructions\nFollow the practices.\n")
    project.configure(context_folder="_context", read_only_files=["_instructions/app-instructions.md"])
    return project.load("context")

def test_files_are_listed_with_their_outlines_and_shadow_copies_are_hidden(context):
    assert context.list_context_files().split("\n") == [
        "app-instructions.md (read-only, 2 lines)",
        "1-2 # Instructions",
        "plan.md (8 lines)",
        "1-8 # Plan",
        "  3-5 ## Goals",
        "  7-8 ## Next steps",
    ]

def test_read_all_starts_with_the_read_only_files(context):
    response = context.read_context_file()
    headers = [line[12:-6] for line in response.splitlines() if line.startswith("===== FILE: ")]
    assert headers == ["app-instructions.md (read-only)", "plan.md"]
    assert "SHADOW COPY" not in response

def test_read_only_files_are_read_by_name_in_any_case(context):
    response = context.read_context_file("APP-INSTRUCTIONS.MD")
    assert response.startswith("File: app-instructions.md (read-only)")
    assert content_of(response) == "# Instructions\nFollow the practices."

def test_read_a_section_by_heading(context):
    response = context.read_context_file("plan.md", section="Next steps")
    assert "Section: ## Next steps (lines 7-8)" in response and "Lines: 7-8 of 8 (partial)" in response
    assert content_of(response) == "## Next steps\n- step 1"

def test_read_a_line_range(context):
    assert content_of(context.read_context_file("plan.md", start_line=3, end_line=4)) == "## Goals\n- one"

@pytest.mark.parametrize("kwargs, message", [
    ({"section": "Missing"}, "No section is named 'Missing'. Sections in the file: 'Plan', 'Plan > Goals', 'Plan > Next steps'."),
    ({"section": "Goals", "start_line": 1}, "either section, or start_line and end_line"),
    ({"start_line": 20}, "start_line must be between 1 and 8"),
])
def test_read_errors(context, kwargs, message):
    response = context.read_context_file("plan.md", **kwargs)
    assert response.startswith("Error:") and message in response

def test_read_only_files_cannot_be_changed(context, project):
    for result in [context.write_context_file("app-instructions.md", "x"),
                   context.write_context_file("App-Instructions.md", "x", append=True),
                   context.remove_context_file("app-instructions.md")]:
        assert "is read-only" in result
    assert (project.root / "_instructions/app-instructions.md").read_text() == "# Instructions\nFollow the practices.\n"

def test_write_append_read_and_remove(context):
    assert context.write_context_file("notes.md", "# Notes").startswith("Successfully")
    assert context.write_context_file("notes.md", "- more", append=True).startswith("Successfully")
    assert content_of(context.read_context_file("notes.md")) == "# Notes\n- more"
    assert context.remove_context_file("notes.md").startswith("Successfully")
    assert context.read_context_file("notes.md").startswith("Error: File 'notes.md' not found")

def test_writes_show_the_outline_and_warn_about_removed_headings(context):
    response = context.write_context_file("plan.md", "# Plan\n\n## Goals\n- one\n")
    assert "Warning: these sections are no longer in the file: # Plan > ## Next steps." in response
    assert response.endswith("Outline after the write:\n1-4 # Plan\n  3-4 ## Goals")
    response = context.write_context_file("plan.md", "## Done\n- all", append=True)
    assert "Warning" not in response and response.endswith("  6-7 ## Done")

def test_long_file_is_read_in_parts(project):
    project.write("_context/long.md", "# Long\n" + "".join(f"Line {i} of the notes.\n" for i in range(400)))
    project.configure(context_folder="_context", max_output_chars=3000)
    context = project.load("context")
    response = context.read_context_file("long.md")
    assert len(response) <= 3000
    next_line = int(response.split("start_line=")[1].split(")")[0])
    assert "Output limited" in response and "Keep context files short" in response
    assert f"Lines: {next_line}-" in context.read_context_file("long.md", start_line=next_line)

def test_read_all_shows_long_files_as_outlines_and_keeps_read_only_files(project):
    for i in range(4):
        project.write(f"_context/note_{i}.md", f"# Note {i}\n## Part A\n" + "Some planning text.\n" * 60 + "## Part B\n" + "More text.\n" * 60)
    project.write("instructions.md", "# Instructions\n")
    project.configure(context_folder="_context", read_only_files=["instructions.md"], max_output_chars=4000)
    response = project.load("context").read_context_file()
    assert len(response) <= 4000
    assert "===== FILE: instructions.md (read-only) =====" in response
    assert "Output limited:" in response and "read_context_file(filename, section=...)" in response
    assert "===== FILE: note_1.md (outline only, 123 lines) =====" in response and "  63-123 ## Part B" in response

def test_long_list_is_limited(project):
    for i in range(30):
        project.write(f"_context/note_{i}.md", f"# Note {i}\n" + "".join(f"## Heading {j}\ntext\n" for j in range(10)))
    project.configure(context_folder="_context", max_output_chars=3000)
    response = project.load("context").list_context_files()
    assert len(response) <= 3000
    assert response.startswith("Output limited:")

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

def test_writes_do_not_double_line_breaks(project):
    project.configure(context_folder="_context")
    context = project.load("context")
    context.write_context_file("crlf.md", "# A\r\nx\r\n# B\r\n")
    context.write_context_file("crlf.md", "more\r\n", append=True)
    assert (project.root / "_context/crlf.md").read_bytes() == b"# A\nx\n# B\n\nmore\n"
    assert "1-2 # A" in context.list_context_files()

def test_read_all_stays_within_the_limit_with_long_read_only_files(project):
    project.write("a.md", "".join(f"Instruction line {i}.\n" for i in range(300)))
    project.write("b.md", "".join(f"Another line {i}.\n" for i in range(300)))
    project.configure(context_folder="_context", read_only_files=["a.md", "b.md"], max_output_chars=2000)
    (project.root / "_context").mkdir()
    response = project.load("context").read_context_file()
    assert len(response) <= 2000 and "===== FILE: b.md (read-only) =====" in response

def test_append_creates_a_missing_file(project):
    project.configure(context_folder="_context")
    context = project.load("context")
    assert context.write_context_file("new.md", "# New", append=True).startswith("Successfully appended to")
    assert (project.root / "_context/new.md").read_text(encoding="utf-8") == "# New"
