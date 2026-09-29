# --- mcp_outline.py ---
"""
Outlines of source and Markdown files: their classes, functions and headings with the line spans
of their content. Used by get_outline, by the section reads of read_file_with_metadata and
read_context_file, and by the outlines shown after a file is written.

Python files are parsed with the ast module. JS/TS files are parsed with regular expressions on a copy
of the source whose comments and strings are blanked out, so that code in them is not mistaken for
declarations or braces. Markdown headings are read line by line, skipping code blocks.

This module only parses text: it reads no configuration and no files, so it can be tested on its own.
"""
import ast
import bisect
import re
from collections import Counter
from dataclasses import dataclass, field

# --- Constants & Config ---

PYTHON_SUFFIXES = {".py"}
JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
MARKDOWN_SUFFIXES = {".md", ".markdown"}
OUTLINE_SUFFIXES = PYTHON_SUFFIXES | JS_TS_SUFFIXES | MARKDOWN_SUFFIXES
MAX_PARSE_CHARS = 1024 * 1024  # Larger files (e.g. bundles) are not outlined
MAX_SUMMARY_CHARS = 120
MAX_LISTED_SECTIONS = 30  # Section names listed at most when a section is not found
INDENT = "  "

# Detail levels of a rendered outline, from the most to the least detailed
FULL = 0         # Summaries, calls, imports and exports
SUMMARIES = 1    # All sections with their summaries
NAMES = 2        # All sections without summaries
TOP_LEVEL = 3    # Top-level sections only (the children of a single top-level section are still shown)

@dataclass
class Section:
    """
    A class, function, heading or other part of a file, with its first and last line (1-based).
    """
    kind: str  # class, function, method, component, hook, interface, type, enum, const, heading or block
    name: str
    start: int
    end: int
    summary: str = ""
    extra: str = ""  # e.g. "extends Base (export)"
    level: int = 0  # Heading level
    children: list["Section"] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    instantiates: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """
        Returns the section as shown in an outline, e.g. 'class App', 'run()' or '## Install'.
        """
        if self.kind == "heading":
            return f"{'#' * self.level} {self.name}"
        if self.kind == "block":
            return self.name
        if self.kind in ("function", "method"):
            return "function (anonymous)" if self.name == "(anonymous)" else f"{self.name}()"
        if self.kind == "hook":
            return f"hook {self.name}()"
        return f"{self.kind} {self.name}"

@dataclass
class Outline:
    """
    The outline of one file. error is set if the file could not be outlined, e.g. due to a Python syntax error.
    """
    line_count: int
    sections: list[Section] = field(default_factory=list)
    summary: str = ""
    notes: list[str] = field(default_factory=list)  # File-level lines such as imports and exports
    error: str = ""

# --- Shared Helpers ---

def count_lines(text: str) -> int:
    """
    Returns the number of lines in text, not counting an empty last line after a final line break.
    """
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)

def _first_line(text: str | None) -> str:
    """
    Returns the first non-empty line of a docstring or comment, shortened to MAX_SUMMARY_CHARS.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= MAX_SUMMARY_CHARS else line[:MAX_SUMMARY_CHARS - 3] + "..."
    return ""

def _line_finder(text: str):
    """
    Returns a function that converts a character index of text into a 1-based line number.
    """
    starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    return lambda index: bisect.bisect_right(starts, index)

# --- Python ---

def _python_calls(node, known: dict) -> list[str]:
    """
    Finds calls to the project's own functions within an AST node.
    """
    symbols, classes, modules = known["symbols"], known["classes"], known["modules"]
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            try:
                call_name = ast.unparse(child.func)
                if call_name in symbols:
                    calls.add(call_name)
                    continue
                if "." in call_name:
                    prefix, remainder = call_name.split(".", 1)
                    if prefix != "self" and (prefix in classes or prefix in modules):
                        if remainder in symbols or f"{prefix}.{remainder}" in symbols:
                            calls.add(call_name)
            except Exception:
                pass
    return sorted(calls)

def _python_instantiations(node, known: dict) -> list[str]:
    """
    Finds instantiations of the project's own classes within an AST node.
    """
    classes, modules = known["classes"], known["modules"]
    instantiations = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            try:
                call_name = ast.unparse(child.func)
                if call_name in classes:
                    instantiations.add(call_name)
                    continue
                if "." in call_name:
                    prefix, remainder = call_name.split(".", 1)
                    if prefix != "self" and (prefix in classes or prefix in modules):
                        class_name = remainder.split(".")[-1]
                        if class_name in classes:
                            instantiations.add(class_name)
            except Exception:
                pass
    return sorted(instantiations)

def python_symbols(modules: list[tuple[str, str]]) -> dict:
    """
    Returns the names of the modules, classes and functions in the given (module name, source) pairs,
    used to recognize calls to the project's own code.
    """
    known = {"symbols": set(), "classes": set(), "modules": set()}
    for module_name, text in modules:
        known["modules"].add(module_name)
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                known["symbols"].update({node.name, f"{module_name}.{node.name}"})
            elif isinstance(node, ast.ClassDef):
                known["classes"].add(node.name)
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        known["symbols"].update({sub.name, f"{node.name}.{sub.name}"})
    return known

def _python_section(node, known: dict | None, in_class: bool) -> Section:
    """
    Returns the section of a class or function node, starting at its first decorator.
    Classes include their methods and nested classes; functions nested in functions are not listed.
    """
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    if isinstance(node, ast.ClassDef):
        section = Section("class", node.name, start, node.end_lineno, summary=_first_line(ast.get_docstring(node)))
        section.children = [_python_section(sub, known, True) for sub in node.body
                            if isinstance(sub, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    else:
        section = Section("method" if in_class else "function", node.name, start, node.end_lineno, summary=_first_line(ast.get_docstring(node)))
    if known is not None:
        section.calls = _python_calls(node, known)
        section.instantiates = _python_instantiations(node, known)
    return section

def _python_block(nodes: list) -> Section:
    """
    Returns a section for consecutive top-level statements other than classes and functions,
    named by what they contain, e.g. 'imports, assignments: A, B'.
    """
    parts, names = [], []
    for i, node in enumerate(nodes):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            part = "docstring"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            part = "imports"
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            part = "assignments"
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.extend(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            part = "main block"
        else:
            part = "statements"
        if part not in parts:
            parts.append(part)
    if names:
        listed = ", ".join(names[:3]) + (f", +{len(names) - 3} more" if len(names) > 3 else "")
        parts[parts.index("assignments")] = f"assignments: {listed}"
    return Section("block", ", ".join(parts), nodes[0].lineno, nodes[-1].end_lineno)

def _outline_python(text: str, known: dict | None) -> Outline:
    """
    Outlines a Python file: blocks of top-level statements, classes with their methods, and functions.
    """
    outline = Outline(count_lines(text))
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        outline.error = f"Python syntax error at line {e.lineno}: {e.msg}"
        return outline
    except ValueError as e:
        outline.error = f"Python could not parse the file: {e}"
        return outline

    outline.summary = _first_line(ast.get_docstring(tree))
    run = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if run:
                outline.sections.append(_python_block(run))
                run = []
            outline.sections.append(_python_section(node, known, False))
        else:
            run.append(node)
    if run:
        outline.sections.append(_python_block(run))
    return outline

# --- JS/TS ---

# Top-level JS/TS declarations. They must start at the beginning of a line, so nested code is not listed.
_JS_IDENT = r"[A-Za-z_$][\w$]*"
_JS_FUNCTION = re.compile(rf"^(export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*({_JS_IDENT})?", re.M)
_JS_CLASS = re.compile(rf"^(export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?class\b\s*(?!extends\b)({_JS_IDENT})?([^{{]*)\{{", re.M)
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
# Right-hand sides of variable declarations that make the variable a function or a component,
# matched after the start ('async', 'function', type arguments) and after the parameter list ('=>')
_JS_FUNCTION_START = re.compile(r"\s*(?:async\s+)?(?:(function)\b|<[^>]*>\s*|(?=\()|([A-Za-z_$][\w$]*)\s*=>)")
_JS_ARROW_AFTER_PARAMETERS = re.compile(r"\s*(?::[^=;]*?)?=>")
_JS_COMPONENT_VALUE = re.compile(r"\s*(?:React\.)?(?:memo|forwardRef)\s*[(<]")
# Class members, matched on lines directly inside a class body
_JS_MODIFIERS = r"(?:(?:public|private|protected|static|readonly|abstract|override|async|get|set|declare)\s+)*"
_JS_METHOD = re.compile(rf"^\s*{_JS_MODIFIERS}\*?\s*(#?{_JS_IDENT})\s*(?:<[^>]*>)?\s*\(")
_JS_ARROW_PROPERTY = re.compile(rf"^\s*{_JS_MODIFIERS}(#?{_JS_IDENT})\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^()]*\)|{_JS_IDENT})\s*(?::[^=]*?)?=>")
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "with", "new", "typeof", "await", "super", "throw"}
# A statement continues on the next line if its line ends with one of these, or the next line starts with one of them
_JS_CONTINUES_AFTER = set("=,+-*/%&|^?:.<>(")  # Not '!': a line ending with it is a non-null assertion, e.g. 'getElementById("x")!'
_JS_CONTINUES_BEFORE = set(".?:|&+-*/=,>")

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
            if c != "`" and (j >= n or content[j] == "\n"):
                # A quote without a closing one on the same line is not a string, e.g. the apostrophe in JSX text "Can't"
                i += 1
                continue
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
            return _first_line(line)
    return ""

def _jsdoc_start(content: str, pos: int) -> int | None:
    """
    Returns the index of the JSDoc comment ('/**') directly before position pos, or None.
    """
    end = pos
    while end > 0 and content[end - 1].isspace():
        end -= 1
    if content[end - 2:end] != "*/":
        return None
    start = content.rfind("/**", 0, end)
    if start == -1 or content.find("*/", start, end - 2) != -1:
        return None
    return start

def _jsdoc_summary(content: str, pos: int) -> str:
    """
    Returns the summary of the JSDoc comment directly before position pos, or an empty string.
    """
    start = _jsdoc_start(content, pos)
    if start is None:
        return ""
    end = content.find("*/", start + 3)
    return _comment_summary(content[start + 3:end])

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

def _js_line_complete(code: str, newline: int) -> bool:
    """
    Checks if the statement on the line ending at index newline is complete, i.e. it does not
    continue on the next line, as in 'a =' or a next line starting with '.method()'.
    """
    j = newline - 1
    while j >= 0 and code[j] in " \t\r":
        j -= 1
    if j < 0 or code[j] == "\n":
        return False
    last, before = code[j], code[j - 1] if j > 0 else ""
    if last == ">" and before != "=":
        return True  # Closes a type argument, e.g. 'Record<string, number>', unlike an arrow '=>'
    if last in "+-" and before == last:
        return True  # 'count++'
    if last in _JS_CONTINUES_AFTER:
        return False
    k = newline + 1
    while k < len(code) and code[k] in " \t\r\n":
        k += 1
    return k >= len(code) or code[k] not in _JS_CONTINUES_BEFORE

def _js_statement_end(code: str, start: int, block: bool) -> tuple[int, int | None]:
    """
    Returns the index of the last character of the declaration starting at start, and the index of
    the brace opening its body (None if it has none): for a block, the end is the brace closing its body,
    otherwise the ';' or line break that ends the statement.
    In a block, braces in type arguments ('<T extends { a: 1 }>') and after a ':' (a return type
    such as '): { a: string } {') are not taken as the body.
    """
    depth = 0
    angle = 0  # Depth of type arguments before a block's body
    in_template = False
    body = None
    last = ""  # The previous character other than whitespace
    for i in range(start, len(code)):
        c = code[i]
        if c == "`":
            in_template = not in_template
        elif in_template:
            continue
        elif block and body is None and depth == 0 and c == "<":
            angle += 1
        elif block and body is None and depth == 0 and c == ">" and angle and last != "=":
            angle -= 1
        elif c in "([{":
            if block and c == "{" and body is None and depth == 0 and not angle and last not in (":", "|", "&", ","):
                body = i
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return max(start, i - 1), body
            if depth == 0 and c == "}" and body is not None:
                return i, body
        elif depth == 0:
            if c == ";":
                return i, body
            if c == "\n" and not block and _js_line_complete(code, i):
                return i - 1, body
        if not c.isspace():
            last = c
    return len(code) - 1, body

def _js_class_members(code: str, body_start: int, body_end: int) -> list[tuple[int, str, bool, int]]:
    """
    Returns (position, name, is_method, position of the last declaration) for the methods and arrow
    function properties directly inside a class body.
    """
    members = []
    seen = {}  # Member name: index in members
    depth = 0
    pos = body_start
    for line in code[body_start:body_end].split("\n"):
        if depth == 0:
            method = _JS_METHOD.match(line)
            match = method or _JS_ARROW_PROPERTY.match(line)
            if match and match.group(1) not in _JS_KEYWORDS:
                name = match.group(1)
                if name in seen:
                    # An overload or a getter and setter pair: the member ends where its last declaration ends
                    members[seen[name]] = (*members[seen[name]][:3], pos)
                else:
                    seen[name] = len(members)
                    members.append((pos, name, bool(method), pos))
        # Brackets and parentheses too, so that the lines of a multi-line call are not taken as members
        depth += sum(line.count(c) for c in "{([") - sum(line.count(c) for c in "})]")
        pos += len(line) + 1
    return members

def _js_is_function_value(code: str, pos: int) -> bool:
    """
    Checks if the value of a variable declaration starting at pos is a function: 'function ...',
    'x => ...' or '(...) => ...', also async and with type arguments. The parameter list is matched
    by its parentheses, so that it can be long and span many lines.
    """
    start = _JS_FUNCTION_START.match(code, pos)
    if not start:
        return False
    if start.group(1) or start.group(2):
        return True
    i = start.end()
    if i >= len(code) or code[i] != "(":
        return False
    depth = 0
    for j in range(i, len(code)):
        depth += {"(": 1, ")": -1}.get(code[j], 0)
        if depth == 0:
            return _JS_ARROW_AFTER_PARAMETERS.match(code, j + 1) is not None
    return False

def _js_kind(name: str, is_function: bool, suffix: str) -> str:
    """
    Classifies a declaration as a Hook, Component, Function or Const by its name and file type.
    """
    if not is_function:
        return "Const"
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

def _outline_js(text: str, suffix: str) -> Outline:
    """
    Outlines a JS/TS file: top-level functions, components, hooks, classes with their methods,
    interfaces, types, enums and exported constants, with their JSDoc summaries. Local imports,
    separate exports and re-exports are listed as notes.
    """
    outline = Outline(count_lines(text), summary=_file_comment_summary(text))
    no_comments, code = _mask_js(text)
    line_of = _line_finder(text)

    imports = []
    for match in list(_JS_IMPORT.finditer(no_comments)) + list(_JS_REQUIRE.finditer(no_comments)):
        source = match.group(1)
        if source.startswith(".") and source not in imports:
            imports.append(source)
    if imports:
        outline.notes.append(f"Imports: {', '.join(imports)}")

    # Names exported separately from their declarations
    default_names = {m.group(1) for m in _JS_DEFAULT_NAME.finditer(code)} - {"function", "class", "async"}
    named_exports = []
    re_exports = []
    for match in _JS_EXPORT_LIST.finditer(no_comments):
        pairs = _parse_export_names(match.group(1))
        if match.group(2):
            re_exports.append(f"{', '.join(e for _, e in pairs)} from {match.group(2)}")
        else:
            named_exports.extend(local for local, _ in pairs)
    for match in _JS_EXPORT_ALL.finditer(no_comments):
        label = f"all as {match.group(1)}" if match.group(1) else "all"
        re_exports.append(f"{label} from {match.group(2)}")
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

    # Declarations as (position, kind, name, export, extra, members, block). A block ends at the brace
    # closing its body (functions, classes, interfaces, enums), other declarations at ';' or a line break.
    declarations = []
    seen = set()
    last_positions = {}  # Overloaded functions end where their last declaration (the implementation) ends

    def add(pos, kind, name, export, extra="", members=None, block=False):
        if (kind, name) in seen:
            if kind in ("Function", "Component", "Hook"):
                last_positions[(kind, name)] = pos
            return
        seen.add((kind, name))
        declarations.append((pos, kind, name, export, extra, members or [], block))

    def export_type(prefix):
        if not prefix:
            return None
        return "default" if "default" in prefix else "named"

    for match in _JS_FUNCTION.finditer(code):
        name = match.group(2)
        if name:
            add(match.start(), _js_kind(name, True, suffix), name, export_type(match.group(1)), block=True)
        elif export_type(match.group(1)) == "default":
            add(match.start(), "Function", "(anonymous)", "default", block=True)

    for match in _JS_CLASS.finditer(code):
        name = match.group(2) or ("(anonymous)" if export_type(match.group(1)) == "default" else None)
        if not name:
            continue
        extends = re.search(r"\bextends\s+([\w$.]+)", match.group(3))
        body_end, body_start = _js_statement_end(code, match.start(), True)
        members = _js_class_members(code, body_start + 1, body_end) if body_start is not None else []
        add(match.start(), "Class", name, export_type(match.group(1)), f"extends {extends.group(1)}" if extends else "", members, block=True)

    for pattern, kind in [(_JS_INTERFACE, "Interface"), (_JS_TYPE, "Type"), (_JS_ENUM, "Enum")]:
        for match in pattern.finditer(code):
            add(match.start(), kind, match.group(2), export_type(match.group(1)), block=kind != "Type")

    for match in _JS_VARIABLE.finditer(code):
        name = match.group(2)
        kind = "Component" if _JS_COMPONENT_VALUE.match(code, match.end()) else _js_kind(name, _js_is_function_value(code, match.end()), suffix)
        export = export_type(match.group(1))
        if kind != "Const" or export or name in default_names or name in named_exports:
            add(match.start(), kind, name, export)

    for match in _JS_DEFAULT_ANONYMOUS.finditer(code):
        add(match.start(), "Function", "(anonymous)", "default")

    # A declaration and its JSDoc form its section; the class body's methods form the class's children
    declared = {d[2] for d in declarations}
    for pos, kind, name, export, extra, members, block in sorted(declarations):
        if name in default_names:
            export = "default"
        elif name in named_exports and not export:
            export = "named"
        tag = {"default": "(default export)", "named": "(export)"}.get(export, "")
        start = _jsdoc_start(text, pos)
        end = _js_statement_end(code, last_positions.get((kind, name), pos), block)[0]
        section = Section(kind.lower(), name, line_of(pos if start is None else start), line_of(end),
                          summary=_jsdoc_summary(text, pos), extra=" ".join(part for part in (extra, tag) if part))
        for member_pos, member_name, is_method, member_last in members:
            member_start = _jsdoc_start(text, member_pos)
            section.children.append(Section("method", member_name, line_of(member_pos if member_start is None else member_start),
                                            line_of(_js_statement_end(code, member_last, is_method)[0]), summary=_jsdoc_summary(text, member_pos)))
        outline.sections.append(section)

    undeclared = [n for n in dict.fromkeys(named_exports) if n not in declared]
    undeclared += [n for n in sorted(default_names) if n not in declared]
    if undeclared:
        outline.notes.append(f"Exports: {', '.join(undeclared)}")
    outline.notes.extend(f"Re-exports {re_export}" for re_export in re_exports)
    return outline

# --- Markdown ---

_MD_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_MD_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# Lines that cannot be the text of a setext heading, such as list items, quotes and tables
_MD_NOT_PARAGRAPH = re.compile(r"^(?: {4}|\t| {0,3}(?:[-*+>|]|\d+[.)])(?:\s|$)| {0,3}<)")

def _outline_markdown(text: str) -> Outline:
    """
    Outlines a Markdown file by its headings, nested by level. A heading's section runs until the next
    heading of the same or a higher level. Text before the first heading forms its own section.
    """
    lines = text.split("\n")
    outline = Outline(count_lines(text))
    headings = []  # (line number, level, title)
    fence = None
    paragraph = None  # The previous line, if it can be the text of a setext heading
    first = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                first = i + 1
                break

    for i in range(first, len(lines)):
        line = lines[i].rstrip("\r")
        if fence:
            if re.match(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", line):
                fence = None
            continue
        fence_match = _MD_FENCE.match(line)
        if fence_match:
            fence, paragraph = fence_match.group(1), None
            continue
        atx = _MD_ATX.match(line)
        if atx:
            title = re.sub(r"(?:^|[ \t]+)#+$", "", atx.group(2) or "").strip()
            if title:
                headings.append((i + 1, len(atx.group(1)), title))
            paragraph = None
            continue
        if paragraph is not None and _MD_SETEXT.match(line):
            headings.append((i, 1 if line.strip()[0] == "=" else 2, paragraph.strip()))
            paragraph = None
            continue
        paragraph = line if line.strip() and not _MD_NOT_PARAGRAPH.match(line) else None

    def trimmed_end(start: int, end: int) -> int:
        while end > start and not lines[end - 1].strip():
            end -= 1
        return end

    first_heading = headings[0][0] if headings else outline.line_count + 1
    before = [n for n in range(1, first_heading) if lines[n - 1].strip()]
    if before:
        outline.sections.append(Section("block", "(text before the first heading)", before[0], before[-1]))

    stack = []  # Open sections as (level, section)
    for index, (line_number, level, title) in enumerate(headings):
        end = outline.line_count
        for next_line, next_level, _ in headings[index + 1:]:
            if next_level <= level:
                end = next_line - 1
                break
        section = Section("heading", title, line_number, trimmed_end(line_number, end), level=level)
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1].children if stack else outline.sections).append(section)
        stack.append((level, section))
    return outline

# --- Public Functions ---

def outline_text(text: str, suffix: str, known: dict | None = None) -> Outline | None:
    """
    Returns the outline of a file's text (with LF line endings), or None if files with the suffix have no outline.
    known (from python_symbols) adds the project's own functions and classes each Python function uses.
    """
    suffix = suffix.lower()
    if suffix not in OUTLINE_SUFFIXES:
        return None
    if len(text) > MAX_PARSE_CHARS:
        return Outline(count_lines(text), error=f"the file is larger than {MAX_PARSE_CHARS} characters")
    if suffix in PYTHON_SUFFIXES:
        return _outline_python(text, known)
    if suffix in JS_TS_SUFFIXES:
        return _outline_js(text, suffix)
    return _outline_markdown(text)

def iter_sections(sections: list[Section], parents: tuple = ()):
    """
    Yields (path, section) for every section and its children, depth first. path is the tuple of
    the section's parents and the section itself.
    """
    for section in sections:
        path = parents + (section,)
        yield path, section
        yield from iter_sections(section.children, path)

def qualified_name(path: tuple) -> str:
    """
    Returns the full name of a section, e.g. 'App.run' for code or 'Setup > Install' for headings.
    """
    separator = " > " if path[-1].kind == "heading" else "."
    return separator.join(section.name for section in path)

def section_count(outline: Outline) -> int:
    """
    Returns the number of sections in the outline, not counting blocks of top-level statements.
    """
    return sum(1 for _, section in iter_sections(outline.sections) if section.kind != "block")

_QUERY_SPAN = re.compile(r"^\d+-\d+\s+")
_QUERY_EXPORT = re.compile(r"\s*\((?:default )?export\)\s*$")
_QUERY_EXTENDS = re.compile(r"\s+extends\s+[\w$.]+\s*$")
_QUERY_KIND = re.compile(r"^(?:#{1,6}\s+|(?:class|def|function|component|hook|interface|type|enum|const|async)\s+)")

def _query_variants(query: str) -> list[set[str]]:
    """
    Returns the forms of a section name to look for, so that a name copied from an outline line
    such as '18-60 class App extends Base (export): Summary' or '## Install' is also found.
    The name as given comes first; the forms with parts of an outline line removed are only
    tried if it matches nothing, so that a heading such as 'type checking' is not also found as 'checking'.
    """
    base = _QUERY_SPAN.sub("", query.strip()).strip()
    # An outline line is 'label extends X (export): summary', so the parts are removed from the end
    without_summary = base.split(": ", 1)[0]
    without_export = _QUERY_EXPORT.sub("", without_summary)
    forms = [without_summary, without_export, _QUERY_EXTENDS.sub("", without_export)]
    forms += [_QUERY_KIND.sub("", form) for form in [base] + forms]
    return [{base}, {form.strip() for form in forms if form.strip()} - {base}]

def find_sections(outline: Outline, query: str) -> list[tuple[str, Section]]:
    """
    Returns (full name, section) for the sections matching query: first by full name (e.g. 'App.run'),
    then by name alone (e.g. 'run'), each first with the exact letter case and then ignoring it.
    """
    entries = [(qualified_name(path), section) for path, section in iter_sections(outline.sections)]
    for variants in _query_variants(query):
        for fold in (False, True):
            # '()' is removed from both sides, so that 'run()' finds 'run' and a heading such as 'Use foo() here' is found as given
            norm = (lambda value: value.replace("()", "").casefold()) if fold else (lambda value: value.replace("()", ""))
            wanted = {norm(v) for v in variants}
            by_full_name = [(name, section) for name, section in entries if norm(name) in wanted]
            if by_full_name:
                return by_full_name
            by_name = [(name, section) for name, section in entries if norm(section.name) in wanted]
            if by_name:
                return by_name
    return []

def locate_section(outline: Outline | None, query: str) -> tuple[Section | None, str, str]:
    """
    Finds the one section of a file matching query, for reading it by name.
    Returns (section, "", "") or, if there is not exactly one match, (None, error code, message).
    """
    if outline is None:
        return None, "no_outline", f"Sections can only be read from {', '.join(sorted(OUTLINE_SUFFIXES))} files. Use start_line and end_line instead."
    if outline.error:
        return None, "no_outline", f"The file could not be outlined: {outline.error}. Use start_line and end_line instead."
    matches = find_sections(outline, query)
    if not matches:
        names = [qualified_name(path) for path, section in iter_sections(outline.sections) if section.kind != "block"]
        listed = ", ".join(f"'{name}'" for name in names[:MAX_LISTED_SECTIONS]) + (f" and {len(names) - MAX_LISTED_SECTIONS} more" if len(names) > MAX_LISTED_SECTIONS else "")
        return None, "section_not_found", f"No section is named '{query}'. Sections in the file: {listed or '(none)'}."
    if len(matches) > 1:
        listed = ", ".join(f"'{name}' (lines {section.start}-{section.end})" for name, section in matches[:10])
        if len({name for name, _ in matches}) < len(matches):
            advice = "Some of them have the same full name, so read the one you need with start_line and end_line."
        else:
            advice = "Use the full name, or start_line and end_line."
        return None, "multiple_sections", f"'{query}' matches {len(matches)} sections: {listed}. {advice}"
    return matches[0][1], "", ""

def render(outline: Outline, detail: int, depth: int = 0) -> list[str]:
    """
    Returns the outline's lines at the given detail level, as 'first-last label: summary',
    indented by nesting. The children of a single section at any level are always shown,
    not counting blocks such as the text before the first heading.
    """
    lines = [f"{INDENT * depth}{note}" for note in outline.notes] if detail == FULL else []

    def add(sections: list[Section], level: int):
        single = sum(1 for s in sections if s.kind != "block") == 1
        for section in sections:
            line = f"{INDENT * level}{section.start}-{section.end} {section.label}"
            if section.extra:
                line += f" {section.extra}"
            if section.summary and detail <= SUMMARIES:
                line += f": {section.summary}"
            lines.append(line)
            if detail == FULL:
                if section.calls:
                    lines.append(f"{INDENT * (level + 1)}Calls: {', '.join(section.calls)}")
                if section.instantiates:
                    lines.append(f"{INDENT * (level + 1)}Instantiates: {', '.join(section.instantiates)}")
            if detail < TOP_LEVEL or single:
                add(section.children, level + 1)

    add(outline.sections, depth)
    return lines

def fit_lines(lines: list[str], max_chars: int, noun: str = "lines") -> list[str]:
    """
    Returns the lines that fit in max_chars. If not all fit, the last line says how many were left out.
    """
    if sum(len(line) + 1 for line in lines) <= max_chars:
        return lines
    total, kept = 0, []
    for line in lines:
        total += len(line) + 1
        if total > max_chars - 60:
            kept.append(f"... ({len(lines) - len(kept)} more {noun} not shown)")
            return kept
        kept.append(line)
    return kept

def _section_keys(outline: Outline) -> list[str]:
    """
    Returns the sections of an outline as 'parent > child' labels in outline order, for comparing outlines.
    """
    return [" > ".join(s.label for s in path) for path, section in iter_sections(outline.sections) if section.kind != "block"]

def compare(before: Outline, after: Outline) -> tuple[list[str], list[str]]:
    """
    Returns the sections that are in before but not in after (removed), and in after but not in before (added).
    """
    before_keys, after_keys = _section_keys(before), _section_keys(after)
    removed_counts = Counter(before_keys) - Counter(after_keys)
    added_counts = Counter(after_keys) - Counter(before_keys)
    removed, added = [], []
    for keys, counts, result in ((before_keys, removed_counts, removed), (after_keys, added_counts, added)):
        for key in keys:
            if counts[key] > 0:
                counts[key] -= 1
                result.append(key)
    return removed, added

def outline_after_write(before_text: str | None, after_text: str, suffix: str, max_chars: int) -> dict | None:
    """
    Returns what a write did to a file's outline, or None if files with the suffix have no outline:
    'outline' (the lines of the new outline, fitted in max_chars), 'removed' and 'added' (changed sections)
    and 'warnings' (removed sections, or a file that can no longer be parsed).
    """
    after = outline_text(after_text, suffix)
    if after is None:
        return None
    before = outline_text(before_text, suffix) if before_text is not None else None
    report = {"outline": [], "removed": [], "added": [], "warnings": []}

    if after.error:
        if before and not before.error:
            report["warnings"].append(f"Warning: the file could be outlined before this change, but not anymore: {after.error}. Check and fix the change.")
        else:
            report["warnings"].append(f"Warning: the file could not be outlined: {after.error}.")
        return report

    lines = render(after, NAMES)
    if sum(len(line) + 1 for line in lines) > max_chars:
        lines = render(after, TOP_LEVEL)
    report["outline"] = fit_lines(lines, max_chars, "sections")

    if before and not before.error:
        report["removed"], report["added"] = compare(before, after)
        if report["removed"]:
            listed = ", ".join(report["removed"][:20]) + (f" and {len(report['removed']) - 20} more" if len(report["removed"]) > 20 else "")
            report["warnings"].append(f"Warning: these sections are no longer in the file: {listed}. If you did not mean to remove them, restore them.")
    return report
