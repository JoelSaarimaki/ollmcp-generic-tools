# --- test_map.py ---
"""
Tests for generate-map-mcp: the file tree, the codebase map and the Python and JS/TS parsers.
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
def js_map(project):
    project.write("src/components/App.tsx", APP_TSX)
    project.write("src/utils/format.ts", FORMAT_TS)
    project.write("src/index.js", INDEX_JS)
    return project.load("map").generate_codebase_map()

def test_js_ts_declarations_are_listed(js_map):
    for line in [
        "> Root application component.",
        "- Imports: `../utils/format`",
        "- **Interface** `AppProps` (export): *Props for the App component.*",
        "- **Component** `App` (default export): *Renders the main layout.*",
        "- **Hook** `useWindowWidth` (export): *Tracks window width.*",
        "- **Component** `Header` (export)",
        "- `internalHelper()`",
        "- `formatDate()` (export): *Formats a date as YYYY-MM-DD.*",
        "- **Enum** `Status` (export)",
        "- **Class** `Cache` extends `Base` (export): *Caches values in memory.*",
        "  - `get()`: *Gets a cached value.*",
        "  - `create()`",
        "- `parse()` (export)",
        "- Re-exports all from `./dates`",
        "- `run()` (export)",
    ]:
        assert line in js_map, line

def test_commented_out_code_is_not_listed(js_map):
    assert "commentedOut" not in js_map and "AlsoCommented" not in js_map

def test_python_functions_classes_and_calls(project):
    project.write("src/app.py", 'def own():\n    """Own function."""\n    helper()\n\nclass Service:\n    """A service."""\n    def run(self):\n        """Runs it."""\n        return Service()\n')
    project.write("src/pkg/util.py", "def helper():\n    pass\n")
    project.write(".venv/lib/ext.py", "def external_helper():\n    pass\n")
    project.write("src/uses_external.py", "def caller():\n    external_helper()\n")
    project.configure()
    map_text = project.load("map").generate_codebase_map()
    assert "- `own()`: *Own function.*\n  - Calls: helper" in map_text
    assert "- **Class** `Service`: *A service.*" in map_text and "  - `run()`: *Runs it.*" in map_text
    assert "Instantiates: Service" in map_text
    assert "external_helper" not in map_text.split("uses_external.py")[1].split("###")[0]  # .venv is not parsed

def test_map_limited_to_a_subfolder_still_finds_calls_into_other_folders(project):
    project.write("src/app.py", "def own():\n    helper()\n")
    project.write("lib/util.py", "def helper():\n    pass\n")
    map_text = project.load("map").generate_codebase_map("src")
    assert "Calls: helper" in map_text and "lib/util.py" not in map_text

@pytest.mark.parametrize("path, message", [
    ("node_modules", "ignored or forbidden"),
    ("missing", "not a directory"),
    ("src/app.py", "not a directory"),
    ("../elsewhere", "not within the allowed directory"),
])
def test_invalid_map_paths(project, path, message):
    project.write("src/app.py", "x = 1\n")
    project.write("node_modules/dep/index.js", "")
    assert message in project.load("map").generate_codebase_map(path)

def test_ignored_folders_and_gitignored_files_are_left_out(project):
    project.write("src/app.py", "x = 1\n")
    project.write("node_modules/dep/index.js", "export function dep() {}\n")
    project.write("generated/gen.py", "def gen(): pass\n")
    project.write(".gitignore", "generated\n")
    tree = project.load("map").generate_file_map()
    assert "app.py" in tree and "node_modules" not in tree and "generated" not in tree

def test_project_inside_a_folder_named_like_an_ignored_folder(make_project):
    project = make_project("out/myproject")
    project.write("src/app.py", "x = 1\n")
    assert "app.py" in project.load("map").generate_file_map()

def test_large_map_is_limited_with_folder_suggestions(project):
    for i in range(40):
        project.write(f"src/api/handler_{i}.py", f'def handle_{i}():\n    """Handles request {i}."""\n')
    for i in range(25):
        project.write(f"src/ui/Widget{i}.tsx", f"export default function Widget{i}() {{ return null; }}\n")
    project.configure(max_output_chars=4000)
    map_text = project.load("map").generate_codebase_map()
    assert len(map_text) <= 4000
    assert "Output limited: the map reached its limit" in map_text
    assert "path='src/api'" in map_text and "path='src/ui'" in map_text  # descends past 'src', which holds everything

def test_long_file_tree_is_cut_short(project):
    for i in range(450):
        project.write(f"src/file_{i:03}.py", "")
    tree = project.load("map").generate_file_map()
    assert "Output limited: the file tree has more than 400 lines" in tree
    assert "(file tree cut short)" in tree
