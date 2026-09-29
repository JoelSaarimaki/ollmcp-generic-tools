# --- generate-map-mcp.py ---
import ast
import fnmatch
import json
import os
import re
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Generate-Map-Server")

INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd())).resolve()
GITIGNORE_PATH = os.getenv("GITIGNORE_PATH")
IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next",
    ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode",
    "target", "out", ".mypy_cache", ".ruff_cache"
}
PYTHON_SUFFIXES = {".py"}
JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
OTHER_SUFFIXES = {".md", ".json", ".css", ".scss", ".html"}
ALL_ALLOWED_SUFFIXES = PYTHON_SUFFIXES | JS_TS_SUFFIXES | OTHER_SUFFIXES

# --- Internal Helpers ---

def _load_forbidden_paths(base_dir: Path) -> list[Path]:
    """
    Loads the forbidden folders and files from the FORBIDDEN_PATHS env var.
    Paths are separated by commas, e.g. "folder/sub_folder/code-file.py, another_folder".
    Relative paths are resolved against base_dir. An empty or unset value means no restrictions.
    """
    raw = os.getenv("FORBIDDEN_PATHS", "").strip()
    if not raw:
        return []

    forbidden = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        p = Path(entry)
        if not p.is_absolute():
            p = base_dir / p
        forbidden.append(p.resolve())
    return forbidden

FORBIDDEN_PATHS = _load_forbidden_paths(INPUT_DIR)

def _is_forbidden(path: Path) -> bool:
    """
    Checks if the given path is a forbidden file or is located within a forbidden folder.
    """
    if not FORBIDDEN_PATHS:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return any(resolved.is_relative_to(forbidden) for forbidden in FORBIDDEN_PATHS)

def _display_path(path: Path, base_dir: Path) -> str:
    """
    Returns the path relative to base_dir if it is inside it, otherwise the absolute path.
    """
    if path.is_relative_to(base_dir):
        return path.relative_to(base_dir).as_posix()
    return path.as_posix()

def _get_gitignore_path(input_dir: Path) -> Path:
    """
    Returns GITIGNORE_PATH if set, otherwise input_dir/.gitignore.
    """
    return Path(GITIGNORE_PATH).resolve() if GITIGNORE_PATH else input_dir / ".gitignore"

def _load_gitignore_patterns(input_dir: Path) -> list[str]:
    """
    Loads the patterns from the .gitignore file returned by _get_gitignore_path.
    """
    gitignore_path = _get_gitignore_path(input_dir)

    if not gitignore_path.exists():
        return []

    patterns = []
    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except Exception:
        pass
    return patterns

def _is_ignored(path: Path, input_dir: Path, ignored_dirs: set[str], gitignore_patterns: list[str]) -> bool:
    """
    Checks if a path is excluded by FORBIDDEN_PATHS, ignored_dirs or .gitignore patterns.
    """
    if _is_forbidden(path):
        return True

    if any(ignored in path.parts for ignored in ignored_dirs):
        return True

    if not gitignore_patterns:
        return False

    try:
        rel_path = path.relative_to(input_dir).as_posix()
    except ValueError:
        return False

    for pattern in gitignore_patterns:
        if fnmatch.fnmatch(rel_path, pattern) or \
           fnmatch.fnmatch(f"{rel_path}/", pattern) or \
           any(fnmatch.fnmatch(part, pattern) for part in path.relative_to(input_dir).parts):
            return True

    return False

def _format_docstring(docstring: str) -> str:
    """
    Returns the first line of a docstring as a single-line markdown description.
    """
    if not docstring:
        return ""

    lines = docstring.splitlines()
    summary = lines[0].strip() if lines else ""

    return f": *{summary}*" if summary else ""

def _parse_python(filepath: Path, input_dir: Path, metadata: dict) -> str:
    """
    Parses a Python file using AST and returns a markdown summary of its classes, functions and calls.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(filepath))
    except (SyntaxError, OSError):
        return ""

    rel_path = filepath.relative_to(input_dir)
    out = [f"### `{rel_path.as_posix()}`\n"]

    module_doc = ast.get_docstring(tree)
    if module_doc:
        out.append(f"> {module_doc.splitlines()[0]}\n")

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls_doc = ast.get_docstring(node)
            cls_desc = _format_docstring(cls_doc)
            out.append(f"- **Class** `{node.name}`{cls_desc}\n")

            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_doc = ast.get_docstring(sub)
                    doc_str = _format_docstring(method_doc)
                    out.append(f"  - `{sub.name}()`{doc_str}\n")

                    calls = _get_function_calls_python(sub, metadata)
                    if calls:
                        out.append(f"    - Calls: {', '.join(calls)}\n")

                    instantiations = _get_class_instantiations_python(sub, metadata)
                    if instantiations:
                        out.append(f"    - Instantiates: {', '.join(instantiations)}\n")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_doc = ast.get_docstring(node)
            fn_desc = _format_docstring(fn_doc)
            out.append(f"- `{node.name}()`{fn_desc}\n")

            calls = _get_function_calls_python(node, metadata)
            if calls:
                out.append(f"  - Calls: {', '.join(calls)}\n")

            instantiations = _get_class_instantiations_python(node, metadata)
            if instantiations:
                out.append(f"  - Instantiates: {', '.join(instantiations)}\n")

    return "".join(out)

def _get_function_calls_python(node, metadata: dict) -> list[str]:
    """
    Finds calls to the project's own functions within an AST node.
    """
    symbols = metadata["symbols"]
    classes = metadata["classes"]
    modules = metadata["modules"]
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            try:
                call_name = ast.unparse(child.func)
                if call_name in symbols:
                    calls.add(call_name)
                    continue
                if "." in call_name:
                    parts = call_name.split(".", 1)
                    prefix = parts[0]
                    remainder = parts[1]
                    if prefix == "self":
                        continue
                    is_ours_prefix = (prefix in classes or prefix in modules)
                    if is_ours_prefix:
                        if remainder in symbols or f"{prefix}.{remainder}" in symbols:
                            calls.add(call_name)
            except Exception:
                pass
    return sorted(calls)

def _get_class_instantiations_python(node, metadata: dict) -> list[str]:
    """
    Finds instantiations of the project's own classes within an AST node.
    """
    classes = metadata["classes"]
    modules = metadata["modules"]
    instantiations = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            try:
                call_name = ast.unparse(child.func)
                if call_name in classes:
                    instantiations.add(call_name)
                    continue
                if "." in call_name:
                    parts = call_name.split(".", 1)
                    prefix = parts[0]
                    remainder = parts[1]
                    if prefix == "self":
                        continue
                    is_ours_prefix = (prefix in classes or prefix in modules)
                    if is_ours_prefix:
                        class_name = remainder.split(".")[-1]
                        if class_name in classes:
                            instantiations.add(class_name)
            except Exception:
                pass
    return sorted(instantiations)

def _parse_js_ts(filepath: Path, input_dir: Path) -> str:
    """
    Parses a JS/TS/JSX/TSX file with regex and returns a markdown summary of its exports,
    components, hooks and interfaces.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return ""

    rel_path = filepath.relative_to(input_dir)
    out = [f"### `{rel_path.as_posix()}`\n"]

    jsdoc_pattern = re.compile(r"/\*\* (.*?) \*/", re.DOTALL)
    jsdocs = jsdoc_pattern.findall(content)
    if jsdocs:
        out.append(f"> {jsdocs[0].strip().splitlines()[0]}\n")

    symbols = []
    patterns = [
        (r"export\s+function\s+(\w+)\s*\((.*?)\)", "Function"),
        (r"export\s+const\s+(\w+)\s*=\s*(?:\([^)]*\)|[^=]+)\s*=>", "Component/ArrowFn"),
        (r"export\s+class\s+(\w+)", "Class"),
        (r"export\s+interface\s+(\w+)", "Interface"),
        (r"export\s+type\s+(\w+)", "Type"),
        (r"export\s+const\s+(\w+)\s*=\s*use\w+", "Hook")
    ]

    for pattern, label in patterns:
        matches = re.finditer(pattern, content, re.MULTILINE)
        for match in matches:
            name = match.group(1)
            if label == "Function":
                symbols.append(f"- `{name}()`")
            else:
                symbols.append(f"- **{label}** `{name}`")

    if symbols:
        out.append("\n".join(symbols) + "\n")
    else:
        out.append("- (No significant exports detected)\n")

    return "".join(out)

def _get_project_metadata(root_dir: Path) -> dict:
    """
    Scans the project's Python files for the names of its modules, classes and functions.
    """
    metadata = {
        "symbols": set(),
        "classes": set(),
        "modules": set()
    }

    for path in root_dir.rglob("*"):
        if path.is_file() and path.suffix in PYTHON_SUFFIXES and not _is_forbidden(path):
            module_name = path.stem
            metadata["modules"].add(module_name)

            try:
                content = path.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(path))
            except (SyntaxError, OSError):
                continue

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    metadata["symbols"].add(node.name)
                    metadata["symbols"].add(f"{module_name}.{node.name}")
                elif isinstance(node, ast.ClassDef):
                    metadata["classes"].add(node.name)
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            metadata["symbols"].add(f"{node.name}.{sub.name}")
                            metadata["symbols"].add(sub.name)
    return metadata

def _generate_simple_map(input_dir: Path, gitignore_patterns: list[str]) -> str:
    """
    Generates a directory tree string of the allowed files.
    """
    lines = [f"Root: `{input_dir.as_posix()}`\n"]

    def _build_tree(current_dir: Path, prefix: str = ""):
        try:
            entries = []
            for entry in current_dir.iterdir():
                if _is_ignored(entry, input_dir, IGNORED_DIRS, gitignore_patterns):
                    continue

                if entry.is_dir():
                    entries.append(entry)
                else:
                    if entry.suffix in ALL_ALLOWED_SUFFIXES:
                        entries.append(entry)

            if not entries:
                return
            entries.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
        except (PermissionError, OSError):
            return

        for i, entry in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/\n")
                new_prefix = prefix + ("    " if is_last else "│   ")
                _build_tree(entry, new_prefix)
            else:
                lines.append(f"{prefix}{connector}{entry.name}\n")

    _build_tree(input_dir)
    return "".join(lines)

def _create_map_content(input_dir: Path) -> str:
    """
    Creates the full codebase map content: the file tree followed by per-file summaries.
    """
    gitignore_patterns = _load_gitignore_patterns(input_dir)
    metadata = _get_project_metadata(input_dir)

    content = ["# Codebase Structure & Summaries\n\n"]
    content.append("## File Map\n")
    map_tree = _generate_simple_map(input_dir, gitignore_patterns)
    content.append(f"```\n{map_tree}```\n\n")
    content.append("## Detailed Descriptions\n\n")

    for path in input_dir.rglob("*"):
        if path.is_dir():
            continue

        if _is_ignored(path, input_dir, IGNORED_DIRS, gitignore_patterns):
            continue

        file_content = ""
        if path.suffix in PYTHON_SUFFIXES:
            file_content = _parse_python(path, input_dir, metadata)
        elif path.suffix in JS_TS_SUFFIXES:
            file_content = _parse_js_ts(path, input_dir)
        elif path.suffix in OTHER_SUFFIXES:
            file_content = f"### `{path.relative_to(input_dir).as_posix()}`\n"

        if file_content:
            if len(content) > 0 and not content[-1].endswith("\n\n"):
                content.append("\n" + file_content)
            else:
                content.append(file_content)

    return "".join(content)

# --- Public MCP Tools ---

@mcp.tool()
def generate_codebase_map() -> str:
    """
    Generate a filemap and structuremap of the codebase.
    Supports Python (via AST) and React/JS/TS (via regex).
    Use this tool to gain starting information about all the code in the codebase.

    Returns:
        str: The generated codebase structure map as a string, or an error message.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR.as_posix()} does not exist."

    try:
        content = _create_map_content(INPUT_DIR)
        return f"Codebase structure map of {INPUT_DIR.as_posix()}\n\n{content}"
    except Exception as e:
        return f"Error: Generating codebase map failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def generate_file_map() -> str:
    """
    Generate the file tree map of the codebase.
    Useful for a quick overview of the directory structure without detailed summaries.

    Returns:
        str: The generated file tree map as a string, or an error message.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR.as_posix()} does not exist."

    try:
        gitignore_patterns = _load_gitignore_patterns(INPUT_DIR)
        map_tree = _generate_simple_map(INPUT_DIR, gitignore_patterns)
        return f"File map of {INPUT_DIR.as_posix()}\n\n```\n{map_tree}```"
    except Exception as e:
        return f"Error: Generating file map failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def get_codebase_map_config() -> str:
    """
    Returns the configuration that decides which files the codebase map tools can see:
    the input directory, ignored directories, forbidden paths, .gitignore patterns and
    the file types that are included in the maps.
    Use this tool to find out why a file is missing from generate_codebase_map or generate_file_map.

    Returns:
        str: A JSON-formatted string containing the configuration or an error message.
    """
    try:
        gitignore_path = _get_gitignore_path(INPUT_DIR)
        return json.dumps({
            "success": True,
            "input_dir": INPUT_DIR.as_posix(),
            "input_dir_exists": INPUT_DIR.exists(),
            "ignored_dirs": sorted(IGNORED_DIRS),
            "forbidden_paths": [_display_path(p, INPUT_DIR) for p in FORBIDDEN_PATHS],
            "gitignore": {
                "applied": True,
                "path": gitignore_path.as_posix(),
                "found": gitignore_path.exists(),
                "patterns": _load_gitignore_patterns(INPUT_DIR)
            },
            "included_file_types": sorted(ALL_ALLOWED_SUFFIXES),
            "notes": [
                "Only files with an included file type are listed in the maps.",
                "Folders named in ignored_dirs are skipped at any depth within input_dir.",
                "Forbidden paths and everything inside forbidden folders are skipped.",
                "Files and folders matching the .gitignore patterns are skipped."
            ]
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": "config_error", "message": str(e), "traceback": traceback.format_exc()}, indent=2)

if __name__ == "__main__":
    mcp.run()
