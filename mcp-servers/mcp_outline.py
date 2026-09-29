# --- mcp_outline.py ---
"""
Outlines of source and Markdown files: their classes, functions and headings with the line spans
of their content. Used by get_outline, by the section reads of read_file_with_metadata and
read_context_file, and by the outlines shown after a file is written.

Python files are parsed with the ast module and JS/TS files with tree-sitter, which gives exact
line spans and still outlines a file with syntax errors. Markdown headings are read line by line,
skipping code blocks.

This module only parses text: it reads no configuration and no files, so it can be tested on its own.
"""
import ast
import re
from collections import Counter
from dataclasses import dataclass, field

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

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
    syntax_error: str = ""  # A syntax error in a file that could still be outlined, e.g. in JS/TS

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

# TypeScript files are parsed with the TypeScript grammar (.tsx with its JSX variant), other files with the JavaScript grammar, which includes JSX
_JS_PARSERS = {
    ".ts": Parser(Language(tree_sitter_typescript.language_typescript())),
    ".tsx": Parser(Language(tree_sitter_typescript.language_tsx())),
    ".js": Parser(Language(tree_sitter_javascript.language())),
}
_JS_FUNCTION_NODES = {"function_declaration", "generator_function_declaration", "function_signature",
                      "function_expression", "function", "generator_function", "arrow_function"}
_JS_CLASS_NODES = {"class_declaration", "abstract_class_declaration", "class"}
_JS_TYPE_NODES = {"interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum"}
_JS_VARIABLE_NODES = {"lexical_declaration", "variable_declaration"}
_JS_MEMBER_NODES = {"method_definition", "method_signature", "abstract_method_signature"}
_JS_FIELD_NODES = {"public_field_definition", "field_definition"}
_JS_NAMESPACE_NODES = {"internal_module", "module"}  # namespace N { }, declare module "x" { }
_JS_COMPONENT_WRAPPERS = {"memo", "forwardRef", "React.memo", "React.forwardRef"}
_JS_NAME = re.compile(r"[A-Za-z_$][\w$.]*")

def _comment_summary(comment_body: str) -> str:
    """
    Returns the first descriptive line of a block comment body, skipping '*' prefixes and @tags.
    """
    for line in comment_body.splitlines():
        line = line.strip().lstrip("*").strip()
        if line and not line.startswith("@"):
            return _first_line(line)
    return ""

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

def _js_text(code: bytes, node: Node) -> str:
    """
    Returns the source text of a syntax tree node, from the UTF-8 source code the tree was parsed from.
    """
    return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

def _js_lines(node: Node) -> tuple[int, int]:
    """
    Returns the first and last line (1-based) of a syntax tree node.
    Points are read by index: reading Point.row crashes Python 3.14 with tree-sitter 0.26.
    """
    first = node.start_point[0] + 1
    last = node.end_point[0] + (1 if node.end_point[1] else 0)
    return first, max(first, last)

def _js_doc(code: bytes, siblings: list[Node], index: int) -> tuple[int | None, str]:
    """
    Returns the first line and the summary of the JSDoc comment directly above siblings[index],
    with no blank line in between, or (None, "") if there is none. Decorators above it are included.
    The siblings are passed as a list, which the callers already have, instead of using Node.prev_named_sibling.
    """
    node = siblings[index]
    decorated = None  # The line of the first decorator above node
    index -= 1
    while index >= 0 and siblings[index].type == "decorator":
        decorated = siblings[index].start_point[0] + 1
        index -= 1
    previous = siblings[index] if index >= 0 else None
    top = decorated or node.start_point[0] + 1
    # A comment after code on the same line, e.g. 'foo(); /** x */', belongs to that code
    after_code = previous is not None and index > 0 and siblings[index - 1].end_point[0] >= previous.start_point[0]
    if (previous is not None and previous.type == "comment" and _js_text(code, previous).startswith("/**")
            and top - (previous.end_point[0] + 1) <= 1 and not after_code):
        return previous.start_point[0] + 1, _comment_summary(_js_text(code, previous)[3:-2])
    return decorated, ""

def _js_first_error_line(node: Node) -> int:
    """
    Returns the line (1-based) of the first syntax error inside a node that has one.
    """
    while True:
        if node.is_error or node.is_missing:
            return node.start_point[0] + 1
        child = next((c for c in node.children if c.has_error or c.is_missing), None)
        if child is None:
            return node.start_point[0] + 1
        node = child

def _js_walk(node: Node):
    """
    Yields the node and all nodes inside it.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))

def _js_name(code: bytes, node: Node, field_name: str = "name") -> str:
    """
    Returns the text of a node's name field, or '(anonymous)' if it has none.
    """
    name = node.child_by_field_name(field_name)
    return _js_text(code, name) if name is not None else "(anonymous)"

def _js_string(code: bytes, node: Node) -> str:
    """
    Returns the value of a string literal node, without its quotes.
    """
    return _js_text(code, node)[1:-1]

def _js_extends(code: bytes, node: Node) -> str:
    """
    Returns 'extends X' for a class that extends another one, or an empty string.
    """
    heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
    if heritage is None:
        return ""
    clause = next((c for c in heritage.named_children if c.type == "extends_clause"), None)
    value = clause.child_by_field_name("value") if clause is not None else (heritage.named_children[0] if heritage.named_children else None)
    name = _js_text(code, value) if value is not None else ""
    return f"extends {name}" if _JS_NAME.fullmatch(name) else ""

def _js_kind(name: str, is_function: bool, suffix: str) -> str:
    """
    Classifies a declaration as a hook, component, function or const by its name and file type.
    """
    if not is_function:
        return "const"
    if re.match(r"use[A-Z0-9]", name):
        return "hook"
    if name[:1].isupper() and suffix in {".jsx", ".tsx"}:
        return "component"
    return "function"

def _js_value_kind(code: bytes, value: Node | None, name: str, suffix: str) -> str:
    """
    Classifies a variable by its value: a function, a component wrapped in memo or forwardRef, or a const.
    """
    if value is not None and value.type == "call_expression":
        function = value.child_by_field_name("function")
        if function is not None and _js_text(code, function) in _JS_COMPONENT_WRAPPERS:
            return "component"
    return _js_kind(name, value is not None and value.type in _JS_FUNCTION_NODES, suffix)

def _js_members(code: bytes, body: Node) -> list[Section]:
    """
    Returns the methods and function-valued fields directly inside a class body. An overloaded
    method, or a getter and setter pair, declared one after the other forms one section spanning all
    its declarations; a static and an instance member with the same name stay separate.
    """
    members = []
    previous_key = None
    nodes = body.named_children
    for index, node in enumerate(nodes):
        if node.type in _JS_FIELD_NODES:
            value = node.child_by_field_name("value")
            if value is None or value.type not in _JS_FUNCTION_NODES:
                continue
            name = _js_name(code, node, "name" if node.child_by_field_name("name") is not None else "property")
        elif node.type in _JS_MEMBER_NODES:
            name = _js_name(code, node)
        else:
            continue
        doc_start, summary = _js_doc(code, nodes, index)
        first, last = _js_lines(node)
        key = (name, any(c.type == "static" for c in node.children))
        if key == previous_key:
            members[-1].end = last
            continue
        members.append(Section("method", name, doc_start or first, last, summary=summary))
        previous_key = key
    return members

def _js_export_names(code: bytes, clause: Node, exports: dict):
    """
    Adds the names of an export list such as '{ a, b as c, d as default }' to the named or default exports.
    """
    for specifier in clause.named_children:
        if specifier.type != "export_specifier":
            continue
        alias = specifier.child_by_field_name("alias")
        if alias is not None and _js_text(code, alias) == "default":
            exports["default"].add(_js_name(code, specifier))
        else:
            exports["named"].append(_js_name(code, specifier))

def _js_statements(code: bytes, statements: list[Node], suffix: str, exports: dict) -> list[tuple[Section, str | None, bool]]:
    """
    Returns (section, export, private const) for the declarations among statements, in file order, and adds
    the imports, separate exports and re-exports found to exports. Namespaces are outlined with their contents.
    """
    entries = []
    functions = {}  # Functions by (kind, name), so that overloads declared one after the other form one section

    def add(index: int, node: Node, export: str | None):
        statement = statements[index]
        doc_start, summary = _js_doc(code, statements, index)
        first, last = _js_lines(statement)
        start = doc_start or first
        if node.type in _JS_FUNCTION_NODES:
            name = _js_name(code, node)
            kind = _js_kind(name, True, suffix)
            if (kind, name) in functions and entries and entries[-1][0] is functions[(kind, name)]:
                functions[(kind, name)].end = last
                return
            section = Section(kind, name, start, last, summary=summary)
            functions[(kind, name)] = section
            entries.append((section, export, False))
        elif node.type in _JS_CLASS_NODES:
            section = Section("class", _js_name(code, node), start, last, summary=summary, extra=_js_extends(code, node))
            body = node.child_by_field_name("body")
            section.children = _js_members(code, body) if body is not None else []
            entries.append((section, export, False))
        elif node.type in _JS_TYPE_NODES:
            entries.append((Section(_JS_TYPE_NODES[node.type], _js_name(code, node), start, last, summary=summary), export, False))
        elif node.type in _JS_VARIABLE_NODES:
            declarators = [d for d in node.named_children if d.type == "variable_declarator"]
            for declarator in declarators:
                name_node = declarator.child_by_field_name("name")
                if name_node is None or name_node.type != "identifier":
                    continue  # Destructuring, e.g. 'const { a } = require("./x")'
                name = _js_text(code, name_node)
                kind = _js_value_kind(code, declarator.child_by_field_name("value"), name, suffix)
                span = (start, last) if len(declarators) == 1 else _js_lines(declarator)
                entries.append((Section(kind, name, *span, summary=summary), export, kind == "const" and export is None))
        elif node.type in _JS_NAMESPACE_NODES or node.type == "statement_block":
            # namespace N { }, declare module "x" { }, and declare global { } (a block inside 'declare')
            name_node = node.child_by_field_name("name")
            if node.type == "statement_block":
                name, body = "global", node
            else:
                name = _js_string(code, name_node) if name_node is not None and name_node.type == "string" else _js_name(code, node)
                body = node.child_by_field_name("body")
            section = Section("namespace", name, start, last, summary=summary)
            if body is not None:
                inner = {"imports": [], "named": [], "default": set(), "re": []}
                section.children = _js_finish(_js_statements(code, body.named_children, suffix, inner), inner)
            entries.append((section, export, False))
        elif export == "default":
            # export default { ... }, export default memo(Header), export default a + b
            entries.append((Section(_js_value_kind(code, node, "(anonymous)", suffix), "(anonymous)", start, last, summary=summary), export, False))

    for index, statement in enumerate(statements):
        if statement.type == "import_statement":
            source = statement.child_by_field_name("source")
            if source is not None and _js_string(code, source).startswith("."):
                exports["imports"].append(_js_string(code, source))
        elif statement.type == "export_statement":
            source = statement.child_by_field_name("source")
            clause = next((c for c in statement.named_children if c.type == "export_clause"), None)
            if source is not None:  # export { a } from "./x", export * from "./x"
                if _js_string(code, source).startswith("."):
                    exports["imports"].append(_js_string(code, source))
                namespace = next((c for c in statement.named_children if c.type == "namespace_export"), None)
                if clause is not None:
                    label = ", ".join(_js_name(code, s, "alias") if s.child_by_field_name("alias") else _js_name(code, s) for s in clause.named_children)
                else:
                    label = f"all as {_js_text(code, namespace.named_children[0])}" if namespace is not None and namespace.named_children else "all"
                exports["re"].append(f"{label} from {_js_string(code, source)}")
            elif clause is not None:  # export { a, b as c }
                _js_export_names(code, clause, exports)
            else:
                # 'export = f' (TypeScript) exports f as the module itself, like a default export
                export = "default" if any(c.type in ("default", "=") for c in statement.children) else "named"
                node = statement.child_by_field_name("declaration") or statement.child_by_field_name("value")
                if node is None and export == "default" and statement.named_children:
                    node = statement.named_children[-1]
                if node is not None and node.type == "identifier":
                    exports["default"].add(_js_text(code, node))  # export default Name
                elif node is not None:
                    if node.type == "ambient_declaration" and node.named_children:
                        node = node.named_children[-1]
                    add(index, node, export)
        elif statement.type == "ambient_declaration" and statement.named_children:
            add(index, statement.named_children[-1], None)  # declare function f(): void; declare module "x" { }
        elif statement.type == "expression_statement" and statement.named_children:
            expression = statement.named_children[0]
            if expression.type in _JS_NAMESPACE_NODES:
                add(index, expression, None)  # namespace N { } is parsed as an expression
            elif expression.type == "assignment_expression":
                # CommonJS: module.exports = { a, b: local } / name / function or class, exports.name = ...
                target, value = _js_text(code, expression.child_by_field_name("left")), expression.child_by_field_name("right")
                if target == "module.exports" and value is not None and value.type == "object":
                    for part in value.named_children:
                        local = part if part.type == "shorthand_property_identifier" else part.child_by_field_name("value") if part.type == "pair" else None
                        if local is not None and local.type in ("shorthand_property_identifier", "identifier"):
                            exports["named"].append(_js_text(code, local))
                elif target == "module.exports" and value is not None and value.type == "identifier":
                    exports["default"].add(_js_text(code, value))
                elif target == "module.exports" and value is not None and (value.type in _JS_FUNCTION_NODES or value.type in _JS_CLASS_NODES):
                    add(index, value, "default")
                elif target.startswith(("exports.", "module.exports.")):
                    exports["named"].append(target.rsplit(".", 1)[1])
        else:
            add(index, statement, None)
    return entries

def _js_finish(entries: list[tuple[Section, str | None, bool]], exports: dict) -> list[Section]:
    """
    Returns the sections of entries with their export tags. Constants are only included if they are
    exported, also when exported separately from their declaration.
    """
    sections = []
    for section, export, private_const in entries:
        if section.name in exports["default"]:
            export = "default"
        elif section.name in exports["named"] and not export:
            export = "named"
        if private_const and not export:
            continue
        tag = {"default": "(default export)", "named": "(export)"}.get(export, "")
        section.extra = " ".join(part for part in (section.extra, tag) if part)
        sections.append(section)
    return sections

def _outline_js(text: str, suffix: str) -> Outline:
    """
    Outlines a JS/TS file with tree-sitter: top-level functions, components, hooks, classes with their
    methods, interfaces, types, enums, namespaces and exported constants, with their JSDoc summaries.
    Local imports, separate exports and re-exports are listed as notes. The outline is made even if
    the file has syntax errors, which are reported in syntax_error.
    """
    outline = Outline(count_lines(text), summary=_file_comment_summary(text))
    # The source and the tree are kept in variables while their nodes are used
    code = text.encode("utf-8")
    tree = _JS_PARSERS.get(suffix, _JS_PARSERS[".js"]).parse(code)
    root = tree.root_node
    if root.has_error:
        outline.syntax_error = f"JS/TS syntax error near line {_js_first_error_line(root)}"

    exports = {"imports": [], "named": [], "default": set(), "re": []}
    outline.sections = _js_finish(_js_statements(code, root.named_children, suffix, exports), exports)

    # require("./x") and import("./x") anywhere in the file
    for node in _js_walk(root):
        if node.type == "call_expression":
            function, arguments = node.child_by_field_name("function"), node.child_by_field_name("arguments")
            if function is not None and _js_text(code, function) in ("require", "import") and arguments is not None:
                source = next((a for a in arguments.named_children if a.type == "string"), None)
                if source is not None and _js_string(code, source).startswith("."):
                    exports["imports"].append(_js_string(code, source))
    if exports["imports"]:
        outline.notes.append(f"Imports: {', '.join(dict.fromkeys(exports['imports']))}")

    declared = {section.name for section in outline.sections}
    undeclared = [n for n in dict.fromkeys(exports["named"]) if n not in declared]
    undeclared += [n for n in sorted(exports["default"]) if n not in declared]
    if undeclared:
        outline.notes.append(f"Exports: {', '.join(undeclared)}")
    outline.notes.extend(f"Re-exports {re_export}" for re_export in exports["re"])
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
    if after.syntax_error and not (before and before.syntax_error):
        # Only a new error is reported, so that syntax the grammar does not know is not reported after every write
        report["warnings"].append(f"Warning: {after.syntax_error} after this change. Check and fix it; the outline below may be incomplete.")

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
