# --- test_map.py ---
"""
Tests for generate-map-mcp: get_outline for folders and files, its detail levels and limits.
The parsers themselves are tested in test_outline.py.
"""
import pytest

APP_TSX = '''/**
 * Root application component.
 */
import React, { useState } from "react";
import { formatDate } from "../utils/format";

/** Props for the App component. */
export interface AppProps {
  name: string;
}

/** Renders the main layout. */
export default function App({ name }: AppProps) {
  return <div>{formatDate(new Date())}</div>;
}

/** Tracks window width. */
export const useWindowWidth = () => window.innerWidth;

export const Header = React.memo(function Header() {
  return <h1>Title</h1>;
});

// export function commentedOut() {}
/* export class AlsoCommented {} */
const url = "http://example.com"; // not a comment start inside the string

function internalHelper(x: number): number {
  return x * 2;
}
'''

FORMAT_TS = '''/** Formats a date as YYYY-MM-DD. */
export function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export enum Status { Active, Inactive }

/** Caches values in memory. */
export abstract class Cache<T> extends Base {
  /** Gets a cached value. */
  get(key: string): T | undefined {
    if (key) { return undefined; }
    return undefined;
  }

  static create(): void {}
}

const parse = function (s: string) { return JSON.parse(s); };

export { parse };
export * from "./dates";
'''

INDEX_JS = '''const { formatDate } = require("./utils/format");
module.exports = { run };
function run() { return formatDate(new Date()); }
'''

@pytest.fixture
def js_outline(project):
    project.write("src/components/App.tsx", APP_TSX)
    project.write("src/utils/format.ts", FORMAT_TS)
    project.write("src/index.js", INDEX_JS)
    return project.load("map").get_outline()

def test_folder_outline_lists_js_ts_sections_with_line_spans(js_outline):
    for line in [
        "src/components/App.tsx (30 lines): Root application component.",
        "  7-10 interface AppProps (export): Props for the App component.",
        "  12-15 component App (default export): Renders the main layout.",
        "  17-18 hook useWindowWidth() (export): Tracks window width.",
        "  20-22 component Header (export)",
        "  28-30 internalHelper()",
        "  1-4 formatDate() (export): Formats a date as YYYY-MM-DD.",
        "  6-6 enum Status (export)",
        "  8-17 class Cache extends Base (export): Caches values in memory.",
        "    10-14 get(): Gets a cached value.",
        "    16-16 create()",
        "  19-19 parse() (export)",
        "  3-3 run() (export)",
    ]:
        assert line in js_outline.split("\n"), line
    assert "Output limited" not in js_outline

def test_commented_out_code_is_not_listed(js_outline):
    assert "commentedOut" not in js_outline and "AlsoCommented" not in js_outline

def test_folder_outline_lists_every_file(project):
    project.write("src/app.py", "def main():\n    pass\n")
    project.write("pyproject.toml", "[project]\nname = 'x'\n")
    project.write("Dockerfile", "FROM python\n")
    project.write("logo.png", b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
    lines = project.load("map").get_outline().split("\n")
    assert "pyproject.toml (2 lines)" in lines and "Dockerfile (1 line)" in lines
    assert "logo.png (binary, 11 B)" in lines
    assert "src/app.py (2 lines)" in lines and "  1-2 main()" in lines

def test_file_outline_shows_calls_into_the_rest_of_the_project(project):
    project.write("src/app.py", 'def own():\n    """Own function."""\n    helper()\n\nclass Service:\n    """A service."""\n    def run(self):\n        """Runs it."""\n        return Service()\n')
    project.write("lib/util.py", "def helper():\n    pass\n")
    project.write(".venv/lib/ext.py", "def external_helper():\n    pass\n")
    project.write("src/uses_external.py", "def caller():\n    external_helper()\n")
    outline = project.load("map")
    lines = outline.get_outline("src/app.py").split("\n")
    assert lines[0] == "- src/app.py (9 lines)"
    assert lines[lines.index("1-3 own(): Own function.") + 1] == "  Calls: helper"
    assert "5-9 class Service: A service." in lines and "  7-9 run(): Runs it." in lines
    assert "    Instantiates: Service" in lines
    assert "Calls" not in outline.get_outline("src/uses_external.py")  # .venv is not scanned

def test_folder_outline_leaves_out_calls(project):
    project.write("src/app.py", "def own():\n    helper()\n\ndef helper():\n    pass\n")
    assert "Calls" not in project.load("map").get_outline("src")

def test_file_outline_of_an_unsupported_type(project):
    project.write("config.toml", "a = 1\n")
    response = project.load("map").get_outline("config.toml")
    assert response.startswith("- config.toml (1 line)") and "read_file_with_metadata" in response

def test_file_outline_reports_a_syntax_error(project):
    project.write("broken.py", "def broken(:\n    pass\n")
    response = project.load("map").get_outline("broken.py")
    assert "not outlined: Python syntax error at line 1" in response

@pytest.mark.parametrize("path, message", [
    ("node_modules", "is ignored: it is an ignored folder"),
    ("missing", "does not exist"),
    ("../elsewhere", "not within the allowed directory"),
])
def test_invalid_outline_paths(project, path, message):
    project.write("src/app.py", "x = 1\n")
    project.write("node_modules/dep/index.js", "")
    assert message in project.load("map").get_outline(path)

def test_ignored_folders_and_gitignored_files_are_left_out(project):
    project.write("src/app.py", "x = 1\n")
    project.write("node_modules/dep/index.js", "export function dep() {}\n")
    project.write("generated/gen.py", "def gen(): pass\n")
    project.write(".gitignore", "generated\n")
    outline = project.load("map").get_outline()
    assert "app.py" in outline and "node_modules" not in outline and "generated" not in outline

def test_project_inside_a_folder_named_like_an_ignored_folder(make_project):
    project = make_project("out/myproject")
    project.write("src/app.py", "x = 1\n")
    assert "app.py" in project.load("map").get_outline()

def test_empty_folder(project):
    (project.root / "empty").mkdir()
    assert "No files found" in project.load("map").get_outline("empty")

def make_large_project(project, handlers=40, widgets=25):
    for i in range(handlers):
        project.write(f"src/api/handler_{i}.py", f'class Handler{i}:\n    """Handles request {i}."""\n    def get(self):\n        pass\n\n    def post(self):\n        pass\n\ndef route_{i}():\n    pass\n')
    for i in range(widgets):
        project.write(f"src/ui/Widget{i}.tsx", f"export default function Widget{i}() {{ return null; }}\n")

@pytest.mark.parametrize("max_chars, note", [
    (8200, "summaries are left out"),
    (7000, "only top-level sections are shown"),
    (5000, "only the files are listed, without their sections"),
    (2500, "only the files directly in the folder and the subfolders with their file counts are listed"),
])
def test_large_folders_are_shown_with_less_detail(project, max_chars, note):
    make_large_project(project)
    project.configure(max_output_chars=max_chars)
    outline = project.load("map").get_outline()
    assert len(outline) <= max_chars
    assert f"Output limited: {note}" in outline
    assert "path='src/api'" in outline and "path='src/ui'" in outline  # descends past 'src', which holds everything

def test_files_level_lists_section_counts(project):
    make_large_project(project)
    project.configure(max_output_chars=5000)
    assert "src/api/handler_0.py (10 lines, 4 sections)" in project.load("map").get_outline()

def test_folders_level_lists_subfolders_with_file_counts(project):
    make_large_project(project)
    project.configure(max_output_chars=2500)
    lines = project.load("map").get_outline().split("\n")
    assert "src/api/ (40 files)" in lines and "src/ui/ (25 files)" in lines

def test_long_file_outline_is_limited(project):
    project.write("big.py", "".join(f'def function_{i}():\n    """Does thing number {i} in a rather long summary line."""\n\n' for i in range(400)))
    project.configure(max_output_chars=4000)
    outline = project.load("map").get_outline("big.py")
    assert len(outline) <= 4000
    assert "Output limited: summaries are left out" in outline or "more sections not shown" in outline

def test_file_outline_points_to_the_right_tool(project):
    project.write("logo.png", b"\x89PNG\r\n\x1a\n\x00")
    project.write("src/app.py", "def main():\n    pass\n")
    outline = project.load("map")
    assert "read_image" in outline.get_outline("logo.png")
    assert "read_file_with_metadata(path='src/app.py', section=...)" in outline.get_outline("src/app.py")

def test_file_outline_shows_a_js_syntax_error(project):
    project.write("src/app.ts", "export function a() {\n  return 1;\n}\nexport function b( {\n")
    assert project.load("map").get_outline("src/app.ts").startswith("- src/app.ts (4 lines, JS/TS syntax error near line 4)")
