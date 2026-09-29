# --- test_search.py ---
"""
Tests for search-tool-mcp: text search and file pattern search.
"""
import pytest

@pytest.fixture
def search_project(project):
    project.write("src/app.py", "def formatDate():\n    return formatDate\n")
    project.write("src/components/App.tsx", "import { formatDate } from '../utils';\n")
    project.write("tests/test_a.py", "formatDate\n")
    project.write("mytests/test_b.py", "formatDate\n")
    project.write("src/logo.bin", b"\x00\x01formatDate\x00")
    project.write("src/bundle.min.js", "var a=1;" * 200 + "formatDate(x);" + "var b=2;" * 200)
    project.write("node_modules/dep/index.js", "formatDate\n")
    project.write("generated/out.ts", "formatDate\n")
    project.write(".gitignore", "generated\n")
    return project

def matched_files(response: str) -> list[str]:
    return sorted({line.split(":")[0] for line in response.splitlines() if ":" in line and line.split(":")[1].isdigit()})

def test_search_skips_binary_ignored_and_gitignored_files(search_project):
    response = search_project.load("search").search_text_in_files("formatDate")
    assert matched_files(response) == ["mytests/test_b.py", "src/app.py", "src/bundle.min.js", "src/components/App.tsx", "tests/test_a.py"]

def test_long_lines_are_shortened_around_the_match(search_project):
    line = next(l for l in search_project.load("search").search_text_in_files("formatDate").splitlines() if "bundle" in l)
    assert "formatDate(x);" in line and len(line) < 400 and line.split(": ", 1)[1].startswith("...")

def test_file_pattern_matches_at_a_folder_boundary(search_project):
    response = search_project.load("search").search_text_in_files("formatDate", file_pattern="tests/*.py")
    assert matched_files(response) == ["tests/test_a.py"]  # not mytests/test_b.py

def test_search_can_be_limited_to_a_folder_or_file(search_project):
    search = search_project.load("search")
    assert matched_files(search.search_text_in_files("formatDate", path="src/components")) == ["src/components/App.tsx"]
    assert "2 matches in 1 files" in search.search_text_in_files("formatDate", path="src/app.py")

def test_context_lines_are_shown_grep_style(project):
    project.write("a.py", "one\ntwo\nthree\nfour\n")
    response = project.load("search").search_text_in_files("three", context_lines=1)
    assert "a.py-2- two\na.py:3: three\na.py-4- four" in response

def test_invalid_regex_explains_how_to_search_literally(project):
    assert "Escape special characters" in project.load("search").search_text_in_files("foo(")

def test_no_matches_message(search_project):
    assert search_project.load("search").search_text_in_files("zzz", file_pattern="*.py").startswith("No matches found for 'zzz'")

def test_many_matches_are_limited_with_suggestions(project):
    for i in range(30):
        project.write(f"src/core/mod_{i}.py", "".join(f"value = lookup({j})\n" for j in range(10)))
    project.write("src/other.py", "value = lookup(1)\n")
    project.configure(max_output_chars=4000)
    response = project.load("search").search_text_in_files("lookup")
    assert len(response) <= 4000
    assert "Output limited: only" in response and "of 301 matches are shown" in response
    assert "path='src/core'" in response  # descends past 'src', which holds all matches

@pytest.mark.parametrize("pattern, recursive, expected", [
    ("*.py", False, []),
    ("*.py", True, ["mytests/test_b.py", "src/app.py", "tests/test_a.py"]),
    ("src/**/*.tsx", False, ["src/components/App.tsx"]),
    ("src/*", False, ["src/components/", "src/app.py", "src/bundle.min.js", "src/logo.bin"]),
])
def test_file_pattern_search(search_project, pattern, recursive, expected):
    response = search_project.load("search").search_files_by_pattern(pattern, recursive)
    found = sorted(line for line in response.splitlines() if line and not line.startswith(("Found", "No files")))
    assert found == sorted(expected)
