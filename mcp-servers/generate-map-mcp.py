from mcp.server.mcpserver import MCPServer
import ast
import os
import re
import traceback
from pathlib import Path

mcp = MCPServer("GenerateMap")

# --- Constants & Config ---
INPUT_DIR = Path(os.getenv("INPUT_DIR", os.getcwd()))
IGNORED_DIRS = {"node_modules", ".git", "__pycache__", "dist", "build", ".next"}
PYTHON_SUFFIXES = {".py"}
JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
OTHER_SUFFIXES = {".md", ".json", ".css", ".scss", ".html"}
ALL_ALLOWED_SUFFIXES = PYTHON_SUFFIXES | JS_TS_SUFFIXES | OTHER_SUFFIXES

# --- Internal Helpers ---

def format_docstring(docstring: str) -> str:
    """
    Parses a docstring to extract the summary and returns it
    as a single-line markdown description.
    """
    if not docstring:
        return ""
    
    lines = docstring.splitlines()
    summary = lines[0].strip() if lines else ""
    
    return f": *{summary}*" if summary else ""

def parse_python(filepath: Path, input_dir: Path, metadata: dict) -> str:
    """Python parser using AST."""
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
            cls_desc = format_docstring(cls_doc)
            out.append(f"- **Class** `{node.name}`{cls_desc}\n")
            
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_doc = ast.get_docstring(sub)
                    doc_str = format_docstring(method_doc)
                    out.append(f"  - `{sub.name}()`{doc_str}\n")
                    
                    calls = _get_function_calls_python(sub, metadata)
                    if calls:
                        out.append(f"    - Calls: {', '.join(calls)}\n")
                    
                    instantiations = _get_class_instantiations_python(sub, metadata)
                    if instantiations:
                        out.append(f"    - Instantiates: {', '.join(instantiations)}\n")
            
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_doc = ast.get_docstring(node)
            fn_desc = format_docstring(fn_doc)
            out.append(f"- `{node.name}()`{fn_desc}\n")
            
            calls = _get_function_calls_python(node, metadata)
            if calls:
                out.append(f"  - Calls: {', '.join(calls)}\n")
            
            instantiations = _get_class_instantiations_python(node, metadata)
            if instantiations:
                out.append(f"  - Instantiates: {', '.join(instantiations)}\n")

    return "".join(out)

def _get_function_calls_python(node, metadata: dict) -> list[str]:
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
                    parts = call_name.split('.', 1)
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
    return sorted(list(calls))

def _get_class_instantiations_python(node, metadata: dict) -> list[str]:
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
                    parts = call_name.split('.', 1)
                    prefix = parts[0]
                    remainder = parts[1]
                    if prefix == "self":
                        continue
                    is_ours_prefix = (prefix in classes or prefix in modules)
                    if is_ours_prefix:
                        class_name = remainder.split('.')[-1]
                        if class_name in classes:
                            instantiations.add(class_name)
            except Exception:
                pass
    return sorted(list(instantiations))

def parse_js_ts(filepath: Path, input_dir: Path) -> str:
    """
    A lightweight regex-based parser for JS/TS/JSX/TSX.
    Targets exports, components, hooks, and interfaces.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return ""

    rel_path = filepath.relative_to(input_dir)
    out = [f"### `{rel_path.as_posix()}`\n"]
    
    # 1. Extract JSDoc if present
    jsdoc_pattern = re.compile(r'/\*\*(.*?)\*/', re.DOTALL)
    jsdocs = jsdoc_pattern.findall(content)
    if jsdocs:
        out.append(f"> {jsdocs[0].strip().splitlines()[0]}\n")

    symbols = []
    
    # Patterns for components, functions, hooks, classes, interfaces, types
    patterns = [
        (r'export\s+function\s+(\w+)\s*\((.*?)\)', "Function"),
        (r'export\s+const\s+(\w+)\s*=\s*(?:\([^)]*\)|[^=]+)\s*=>', "Component/ArrowFn"),
        (r'export\s+class\s+(\w+)', "Class"),
        (r'export\s+interface\s+(\w+)', "Interface"),
        (r'export\s+type\s+(\w+)', "Type"),
        (r'export\s+const\s+(\w+)\s*=\s*use\w+', "Hook")
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
    metadata = {
        "symbols": set(),
        "classes": set(),
        "modules": set()
    }
    
    for path in root_dir.rglob("*"):
        if path.is_file() and path.suffix in PYTHON_SUFFIXES:
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

def _generate_simple_map(input_dir: Path) -> str:
    lines = [f"Root: `{input_dir.as_posix()}`\n"]
    
    def _build_tree(current_dir: Path, prefix: str = ""):
        try:
            entries = []
            for entry in current_dir.iterdir():
                # Skip ignored directories
                if any(ignored in entry.parts for ignored in IGNORED_DIRS):
                    continue
                
                if entry.is_dir():
                    entries.append(entry)
                else:
                    # Only include files with allowed suffixes
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

def _create_and_write_map(input_dir: Path) -> str:
    metadata = _get_project_metadata(input_dir)
    
    content = ["# Codebase Structure & Summaries\n\n"]
    
    # Add File Map section with code block for console compatibility
    content.append("## File Map\n")
    map_tree = _generate_simple_map(input_dir)
    content.append(f"```\n{map_tree}```\n\n")
    content.append("## Detailed Descriptions\n\n")
    
    # Using rglob for consistent traversal
    for path in input_dir.rglob("*"):
        if path.is_dir():
            continue

        # Skip common dependency/build folders using path.parts
        if any(ignored in path.parts for ignored in IGNORED_DIRS):
            continue

        file_content = ""
        # Python Logic
        if path.suffix in PYTHON_SUFFIXES:
            file_content = parse_python(path, input_dir, metadata)
        
        # JS/TS Logic
        elif path.suffix in JS_TS_SUFFIXES:
            file_content = parse_js_ts(path, input_dir)
        
        elif path.suffix in OTHER_SUFFIXES:
            file_content = f"### `{path.relative_to(input_dir).as_posix()}`\n"

        if file_content:
            if len(content) > 0 and not content[-1].endswith("\n\n"):
                content.append("\n" + file_content)
            else:
                content.append(file_content)

    result_content = "".join(content)
    
    return result_content

# --- Public MCP Tools ---

@mcp.tool()
def generate_codebase_map() -> str:
    """
    Generate a structuremap of the codebase. 
    Supports Python (via AST) and React/JS/TS (via regex).
    Use this tool to gain starting information about all the code in the codebase.

    Returns:
        str: The generated codebase structure map as a string.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR} does not exist."

    try:
        content = _create_and_write_map(
            input_dir=INPUT_DIR
        )
        
        return f"Codebase structure map of {INPUT_DIR.as_posix()}\n\n{content}"

    except Exception as e:
        return f"Error generating map:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
