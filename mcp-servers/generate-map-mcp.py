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

# Top-level JS/TS declarations. They must start at the beginning of a line, so nested code is not listed.
_JS_IDENT = r"[A-Za-z_$][\w$]*"
_JS_FUNCTION = re.compile(rf"^(export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*({_JS_IDENT})?", re.M)
_JS_CLASS = re.compile(rf"^(export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?class\b\s*({_JS_IDENT})?([^{{]*)\{{", re.M)
_JS_INTERFACE = re.compile(rf"^(export\s+)?(?:declare\s+)?interface\s+({_JS_IDENT})", re.M)
_JS_TYPE = re.compile(rf"^(export\s+)?(?:declare\s+)?type\s+({_JS_IDENT})\b", re.M)
_JS_ENUM = re.compile(rf"^(export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+({_JS_IDENT})", re.M)
_JS_VARIABLE = re.compile(rf"^(export\s+)?(?:declare\s+)?(?:const|let|var)\s+({_JS_IDENT})\s*(?::[^=\n]+)?=(?![=>])", re.M)
_JS_DEFAULT_NAME = re.compile(rf"^export\s+default\s+({_JS_IDENT})\s*;?[ \t]*$", re.M)
_JS_DEFAULT_ANONYMOUS = re.compile(rf"^export\s+default\s+(?:async\s+)?(?:\(|{_JS_IDENT}\s*=>)", re.M)
_JS_EXPORT_LIST = re.compile(r"^export\s+(?:type\s+)?\{([^}]*)\}(?:\s*from\s*['\"]([^'\"]+)['\"])?", re.M)
_JS_EXPORT_ALL = re.compile(rf"^export\s+(?:type\s+)?\*\s*(?:as\s+({_JS_IDENT})\s*)?from\s*['\"]([^'\"]+)['\"]", re.M)
_JS_CJS_OBJECT = re.compile(r"^module\.exports\s*=\s*\{([^}]*)\}", re.M)
_JS_CJS_NAME = re.compile(rf"^(?:module\.exports\s*=\s*({_JS_IDENT})\s*;?[ \t]*$|(?:module\.)?exports\.({_JS_IDENT})\s*=)", re.M)
_JS_IMPORT = re.compile(r"^[ \t]*(?:import|export)\s+(?:type\s+)?(?:[\w$*{}\s,]+?\s+from\s+)?['\"]([^'\"]+)['\"]", re.M)
_JS_REQUIRE = re.compile(r"\b(?:require|import)\(\s*['\"]([^'\"]+)['\"]\s*\)")
# Right-hand sides of variable declarations that make the variable a function or a component
_JS_FUNCTION_VALUE = re.compile(r"\s*(?:async\s+)?(?:function\b|<[^>]*>\s*\([^()]*\)\s*(?::[^=]*?)?=>|\([^()]*\)\s*(?::[^=]*?)?=>|[A-Za-z_$][\w$]*\s*=>)")
_JS_COMPONENT_VALUE = re.compile(r"\s*(?:React\.)?(?:memo|forwardRef)\s*[(<]")
# Class members, matched on lines directly inside a class body
_JS_MODIFIERS = r"(?:(?:public|private|protected|static|readonly|abstract|override|async|get|set|declare)\s+)*"
_JS_METHOD = re.compile(rf"^\s*{_JS_MODIFIERS}\*?\s*(#?{_JS_IDENT})\s*(?:<[^>]*>)?\s*\(")
_JS_ARROW_PROPERTY = re.compile(rf"^\s*{_JS_MODIFIERS}(#?{_JS_IDENT})\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^()]*\)|{_JS_IDENT})\s*(?::[^=]*?)?=>")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "with", "new", "typeof", "await", "super", "throw"}
MAX_JS_PARSE_BYTES = 1024 * 1024  # Larger files (e.g. bundles) are listed but not parsed

def _blank(chars: list[str], start: int, end: int):
    """
    Replaces the characters between start and end with spaces, keeping line breaks.
    """
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "

def _mask_js(content: str) -> tuple[str, str]:
    """
    Returns two copies of JS/TS source with the same length and line breaks: one with comments
    replaced by spaces, and one with both comments and string contents replaced by spaces.
    Used so that code inside comments or strings is not mistaken for declarations or braces.
    """
    no_comments = list(content)
    no_strings = list(content)
    i, n = 0, len(content)
    while i < n:
        c = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = content.find("\n", i)
            end = n if end == -1 else end
            _blank(no_comments, i, end)
            _blank(no_strings, i, end)
            i = end
        elif c == "/" and nxt == "*":
            end = content.find("*/", i + 2)
            end = n if end == -1 else end + 2
            _blank(no_comments, i, end)
            _blank(no_strings, i, end)
            i = end
        elif c in "'\"`":
            j = i + 1
            while j < n and content[j] != c:
                if content[j] == "\\":
                    j += 1
                elif c != "`" and content[j] == "\n":
                    break
                j += 1
            _blank(no_strings, i + 1, min(j, n))
            i = j + 1
        else:
            i += 1
    return "".join(no_comments), "".join(no_strings)

def _comment_summary(comment_body: str) -> str:
    """
    Returns the first descriptive line of a block comment body, skipping '*' prefixes and @tags.
    """
    for line in comment_body.splitlines():
        line = line.strip().lstrip("*").strip()
        if line and not line.startswith("@"):
            return line
    return ""

def _jsdoc_summary(content: str, pos: int) -> str:
    """
    Returns the summary of the JSDoc comment directly before position pos, or an empty string.
    """
    end = pos
    while end > 0 and content[end - 1].isspace():
        end -= 1
    if content[end - 2:end] != "*/":
        return ""
    start = content.rfind("/**", 0, end)
    if start == -1 or content.find("*/", start, end - 2) != -1:
        return ""
    return _comment_summary(content[start + 3:end - 2])

def _file_comment_summary(content: str) -> str:
    """
    Returns the summary of a block comment at the top of the file that describes the whole file,
    i.e. one followed by a blank line or an import rather than directly by a declaration.
    """
    start = content.find("\n") + 1 if content.startswith("#!") else 0
    while start < len(content) and content[start].isspace():
        start += 1
    if not content.startswith("/*", start):
        return ""
    end = content.find("*/", start + 2)
    if end == -1:
        return ""
    rest = content[end + 2:]
    gap = rest[:len(rest) - len(rest.lstrip())]
    if "\n\n" in gap.replace("\r", "") or rest.lstrip().startswith(("import", "'use", "\"use", "require")):
        return _comment_summary(content[start + 2:end].lstrip("*"))
    return ""

def _find_block_end(code: str, open_index: int) -> int:
    """
    Returns the index of the brace that closes the block opened at open_index.
    """
    depth = 0
    for i in range(open_index, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(code)

def _js_class_members(content: str, code: str, body_start: int, body_end: int) -> list[str]:
    """
    Returns markdown lines for the methods directly inside a class body.
    """
    members = []
    seen = set()
    depth = 0
    pos = body_start
    for line in code[body_start:body_end].split("\n"):
        if depth == 0:
            match = _JS_METHOD.match(line) or _JS_ARROW_PROPERTY.match(line)
            if match and match.group(1) not in _JS_KEYWORDS and match.group(1) not in seen:
                seen.add(match.group(1))
                doc = _jsdoc_summary(content, pos)
                members.append(f"  - `{match.group(1)}()`" + (f": *{doc}*" if doc else ""))
        depth += line.count("{") - line.count("}")
        pos += len(line) + 1
    return members

def _js_kind(name: str, is_function: bool, suffix: str) -> str:
    """
    Classifies a function-like declaration as a Hook, Component or Function by its name and file type.
    """
    if not is_function:
        return "Constant"
    if re.match(r"use[A-Z0-9]", name):
        return "Hook"
    if name[:1].isupper() and suffix in {".jsx", ".tsx"}:
        return "Component"
    return "Function"

def _parse_export_names(names: str) -> list[tuple[str, str]]:
    """
    Parses the contents of an export list such as "a, b as c, type D" into (local, exported) name pairs.
    """
    pairs = []
    for part in names.split(","):
        part = re.sub(r"^\s*type\s+", "", part).strip()
        if not part:
            continue
        local, _, exported = part.partition(" as ")
        pairs.append((local.strip(), (exported or local).strip()))
    return pairs

def _parse_js_ts(filepath: Path, input_dir: Path) -> str:
    """
    Parses a JS/TS/JSX/TSX file with regex and returns a markdown summary of its local imports,
    top-level functions, components, hooks, classes (with methods), interfaces, types, enums,
    exported constants and re-exports, with their JSDoc summaries.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return ""

    rel_path = filepath.relative_to(input_dir)
    out = [f"### `{rel_path.as_posix()}`\n"]
    if len(content) > MAX_JS_PARSE_BYTES:
        out.append("- (File too large to parse)\n")
        return "".join(out)

    no_comments, code = _mask_js(content)
    suffix = filepath.suffix.lower()

    file_doc = _file_comment_summary(content)
    if file_doc:
        out.append(f"> {file_doc}\n")

    imports = []
    for match in list(_JS_IMPORT.finditer(no_comments)) + list(_JS_REQUIRE.finditer(no_comments)):
        source = match.group(1)
        if source.startswith(".") and source not in imports:
            imports.append(source)
    if imports:
        out.append(f"- Imports: {', '.join(f'`{s}`' for s in imports)}\n")

    # Collect declarations as (position, kind, name, export, extra, members)
    declarations = []
    seen = set()

    def add(pos, kind, name, export, extra="", members=None):
        if (kind, name) in seen:
            return
        seen.add((kind, name))
        declarations.append((pos, kind, name, export, extra, members or []))

    def export_type(prefix):
        if not prefix:
            return None
        return "default" if "default" in prefix else "named"

    for match in _JS_FUNCTION.finditer(code):
        name = match.group(2)
        if name:
            add(match.start(), _js_kind(name, True, suffix), name, export_type(match.group(1)))
        elif export_type(match.group(1)) == "default":
            add(match.start(), "Function", "(anonymous)", "default")

    for match in _JS_CLASS.finditer(code):
        name = match.group(2) or ("(anonymous)" if export_type(match.group(1)) == "default" else None)
        if not name:
            continue
        extends = re.search(r"\bextends\s+([\w$.]+)", match.group(3))
        body_start = match.end() - 1
        body_end = _find_block_end(code, body_start)
        members = _js_class_members(content, code, body_start + 1, body_end)
        add(match.start(), "Class", name, export_type(match.group(1)), f" extends `{extends.group(1)}`" if extends else "", members)

    for pattern, kind in [(_JS_INTERFACE, "Interface"), (_JS_TYPE, "Type"), (_JS_ENUM, "Enum")]:
        for match in pattern.finditer(code):
            add(match.start(), kind, match.group(2), export_type(match.group(1)))

    for match in _JS_VARIABLE.finditer(code):
        name = match.group(2)
        value = code[match.end():match.end() + 300]
        if _JS_COMPONENT_VALUE.match(value):
            kind = "Component"
        else:
            kind = _js_kind(name, bool(_JS_FUNCTION_VALUE.match(value)), suffix)
        export = export_type(match.group(1))
        if kind != "Constant" or export:
            add(match.start(), kind, name, export)

    for match in _JS_DEFAULT_ANONYMOUS.finditer(code):
        add(match.start(), "Function", "(anonymous)", "default")

    # Names exported separately from their declarations
    declared = {d[2] for d in declarations}
    default_names = {m.group(1) for m in _JS_DEFAULT_NAME.finditer(code)} - {"function", "class", "async"}
    named_exports = []
    re_exports = []
    for match in _JS_EXPORT_LIST.finditer(no_comments):
        pairs = _parse_export_names(match.group(1))
        if match.group(2):
            re_exports.append(f"{', '.join(f'`{e}`' for _, e in pairs)} from `{match.group(2)}`")
        else:
            named_exports.extend(local for local, _ in pairs)
    for match in _JS_EXPORT_ALL.finditer(no_comments):
        label = f"all as `{match.group(1)}`" if match.group(1) else "all"
        re_exports.append(f"{label} from `{match.group(2)}`")
    for match in _JS_CJS_OBJECT.finditer(code):
        # module.exports = { a, b: localName }
        for part in match.group(1).split(","):
            local = part.split(":")[-1].strip()
            if re.fullmatch(_JS_IDENT, local):
                named_exports.append(local)
    for match in _JS_CJS_NAME.finditer(code):
        if match.group(1):
            default_names.add(match.group(1))  # module.exports = name
        else:
            named_exports.append(match.group(2))  # exports.name = ...

    symbols = []
    for pos, kind, name, export, extra, members in sorted(declarations):
        if name in default_names:
            export = "default"
        elif name in named_exports and not export:
            export = "named"
        tag = {"default": " (default export)", "named": " (export)"}.get(export, "")
        doc = _jsdoc_summary(content, pos)
        doc_str = f": *{doc}*" if doc else ""
        if name == "(anonymous)":
            label = f"**{kind}** (anonymous)"
        elif kind == "Function":
            label = f"`{name}()`"
        else:
            label = f"**{kind}** `{name}`"
        symbols.append(f"- {label}{extra}{tag}{doc_str}")
        symbols.extend(members)

    undeclared = [n for n in dict.fromkeys(named_exports) if n not in declared]
    undeclared += [n for n in sorted(default_names) if n not in declared]
    if undeclared:
        symbols.append(f"- Exports: {', '.join(f'`{n}`' for n in undeclared)}")
    for re_export in re_exports:
        symbols.append(f"- Re-exports {re_export}")

    if symbols:
        out.append("\n".join(symbols) + "\n")
    else:
        out.append("- (No declarations detected)\n")

    return "".join(out)

def _iter_files(input_dir: Path, gitignore_patterns: list[str], start_dir: Path | None = None):
    """
    Yields the files within start_dir (defaulting to input_dir) that are not ignored, in sorted order.
    Ignored folders are skipped without being entered, which keeps large folders such as node_modules fast.
    """
    for dirpath, dirnames, filenames in os.walk(start_dir or input_dir):
        current_dir = Path(dirpath)
        dirnames[:] = sorted(
            (d for d in dirnames if not _is_ignored(current_dir / d, input_dir, IGNORED_DIRS, gitignore_patterns)),
            key=str.lower
        )
        for name in sorted(filenames, key=str.lower):
            path = current_dir / name
            if not _is_ignored(path, input_dir, IGNORED_DIRS, gitignore_patterns):
                yield path

def _get_project_metadata(input_dir: Path, gitignore_patterns: list[str]) -> dict:
    """
    Scans the project's own Python files for the names of its modules, classes and functions.
    """
    metadata = {
        "symbols": set(),
        "classes": set(),
        "modules": set()
    }

    for path in _iter_files(input_dir, gitignore_patterns):
        if path.suffix in PYTHON_SUFFIXES:
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

def _resolve_scope(path: str | None, gitignore_patterns: list[str]) -> tuple[Path, str | None]:
    """
    Resolves the folder a map is limited to. Relative paths are resolved against INPUT_DIR.
    Returns the folder and an error message, which is None if the folder can be mapped.
    """
    if not path:
        return INPUT_DIR, None
    p = Path(path)
    if not p.is_absolute():
        p = INPUT_DIR / p
    p = p.resolve()
    if not p.is_relative_to(INPUT_DIR):
        return p, f"Error: Path '{path}' is not within the input directory {INPUT_DIR.as_posix()}."
    if not p.is_dir():
        return p, f"Error: Path '{path}' is not a directory within the input directory {INPUT_DIR.as_posix()}."
    if p != INPUT_DIR and _is_ignored(p, INPUT_DIR, IGNORED_DIRS, gitignore_patterns):
        return p, f"Error: Path '{path}' is ignored or forbidden. Use get_codebase_map_config to see why."
    return p, None

def _generate_simple_map(input_dir: Path, gitignore_patterns: list[str], root_dir: Path | None = None) -> str:
    """
    Generates a directory tree string of the allowed files within root_dir (defaulting to input_dir).
    """
    root_dir = root_dir or input_dir
    lines = [f"Root: `{root_dir.as_posix()}`\n"]

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

    _build_tree(root_dir)
    return "".join(lines)

def _create_map_content(input_dir: Path, gitignore_patterns: list[str], scope_dir: Path) -> str:
    """
    Creates the codebase map content for scope_dir: the file tree followed by per-file summaries.
    The whole project is still scanned for symbols, so calls into other folders are recognized.
    """
    metadata = _get_project_metadata(input_dir, gitignore_patterns)

    content = ["# Codebase Structure & Summaries\n\n"]
    content.append("## File Map\n")
    map_tree = _generate_simple_map(input_dir, gitignore_patterns, scope_dir)
    content.append(f"```\n{map_tree}```\n\n")
    content.append("## Detailed Descriptions\n\n")

    for path in _iter_files(input_dir, gitignore_patterns, scope_dir):
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
def generate_codebase_map(path: str | None = None) -> str:
    """
    Generate a filemap and structuremap of the codebase.
    Supports Python (via AST) and React/JS/TS (via regex): lists functions, classes and methods,
    components, hooks, types, exports, local imports and docstring/JSDoc summaries.
    Use this tool to gain starting information about all the code in the codebase.
    For large codebases, limit the map to one folder at a time with the path argument.

    Args:
        path (str, optional): A folder to limit the map to, relative to the input directory
            (e.g., 'src/components'). Defaults to the whole input directory.

    Returns:
        str: The generated codebase structure map as a string, or an error message.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR.as_posix()} does not exist."

    try:
        gitignore_patterns = _load_gitignore_patterns(INPUT_DIR)
        scope_dir, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error

        content = _create_map_content(INPUT_DIR, gitignore_patterns, scope_dir)
        return f"Codebase structure map of {scope_dir.as_posix()}\n\n{content}"
    except Exception as e:
        return f"Error: Generating codebase map failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def generate_file_map(path: str | None = None) -> str:
    """
    Generate the file tree map of the codebase.
    Useful for a quick overview of the directory structure without detailed summaries.

    Args:
        path (str, optional): A folder to limit the map to, relative to the input directory
            (e.g., 'src/components'). Defaults to the whole input directory.

    Returns:
        str: The generated file tree map as a string, or an error message.
    """
    if not INPUT_DIR.exists():
        return f"Error: Input directory {INPUT_DIR.as_posix()} does not exist."

    try:
        gitignore_patterns = _load_gitignore_patterns(INPUT_DIR)
        scope_dir, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error

        map_tree = _generate_simple_map(INPUT_DIR, gitignore_patterns, scope_dir)
        return f"File map of {scope_dir.as_posix()}\n\n```\n{map_tree}```"
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
