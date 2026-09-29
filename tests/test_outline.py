# --- test_outline.py ---
"""
Tests for mcp_outline: the Python, JS/TS and Markdown outlines, their line spans, finding sections
by name, rendering at each detail level and comparing outlines before and after a write.
"""
import re

import pytest

import mcp_outline as outline_module
from mcp_outline import FULL, NAMES, SUMMARIES, TOP_LEVEL, compare, find_sections, locate_section, outline_after_write, outline_text, render

def spans(text: str, suffix: str) -> dict[str, tuple[int, int]]:
    """Returns {full name: (first line, last line)} of every section."""
    outline = outline_text(text, suffix)
    assert outline is not None and not outline.error, outline and outline.error
    return {outline_module.qualified_name(path): (s.start, s.end) for path, s in outline_module.iter_sections(outline.sections)}

def lines_of(text: str, span: tuple[int, int]) -> list[str]:
    return text.split("\n")[span[0] - 1:span[1]]

# --- Python ---

PYTHON = '''"""Module summary."""
import os

LIMIT = 10


@decorator
def first(a):
    """First function."""
    return a


class Service(Base):
    """A service."""

    @property
    def name(self):
        return "x"

    class Inner:
        def deep(self):
            pass

    async def run(self):
        def nested():
            pass
        return nested


if __name__ == "__main__":
    first(1)
'''

def test_python_spans_include_decorators_and_whole_bodies():
    result = spans(PYTHON, ".py")
    assert result["first"] == (7, 10)
    assert result["Service"] == (13, 27)
    assert result["Service.name"] == (16, 18)
    assert result["Service.Inner"] == (20, 22)
    assert result["Service.Inner.deep"] == (21, 22)
    assert result["Service.run"] == (24, 27)
    assert "Service.run.nested" not in result  # functions inside functions are not listed

def test_python_top_level_statements_form_blocks():
    lines = render(outline_text(PYTHON, ".py"), NAMES)
    assert lines[0] == "1-4 docstring, imports, assignments: LIMIT"
    assert lines[-1] == "30-31 main block"

def test_python_summaries():
    outline = outline_text(PYTHON, ".py")
    assert outline.summary == "Module summary."
    lines = render(outline, SUMMARIES)
    assert "13-27 class Service: A service." in lines and "7-10 first(): First function." in lines

def test_python_calls_to_the_projects_own_code():
    text = 'def helper():\n    pass\n\ndef caller():\n    """Calls it."""\n    helper()\n    Service()\n\nclass Service:\n    pass\n'
    known = outline_module.python_symbols([("app", text)])
    full = render(outline_text(text, ".py", known), FULL)
    index = full.index("4-7 caller(): Calls it.")
    assert full[index + 1:index + 3] == ["  Calls: helper", "  Instantiates: Service"]
    assert not any("Calls:" in line for line in render(outline_text(text, ".py", known), SUMMARIES))  # only shown in full detail

def test_python_syntax_error_is_reported():
    outline = outline_text("def broken(:\n    pass\n", ".py")
    assert outline.error.startswith("Python syntax error at line 1")

# --- JS/TS ---

TSX = '''/**
 * Root component.
 */
import React from "react";

/** Props. */
export interface Props {
  name: string;
}

export type Mode =
  | "a"
  | "b";

/** Renders the app. */
export default function App({ name }: Props) {
  const x = { a: 1 };
  return <div>{name}</div>;
}

export const useThing = () => {
  return 1;
};

export const Header = React.memo(function Header() {
  return <h1>Title</h1>;
});

export class Store extends Base {
  /** Gets a value. */
  get(key: string): string {
    if (key) { return "x"; }
    return "";
  }

  handle = () => {
    return 2;
  };
}

const sql = `
SELECT {x}
`;

export const LIMIT = 5
export const other = 6
'''

def test_js_ts_spans():
    result = spans(TSX, ".tsx")
    assert result["Props"] == (6, 9)  # includes its JSDoc
    assert result["Mode"] == (11, 13)  # continues on lines starting with '|'
    assert result["App"] == (15, 19)
    assert result["useThing"] == (21, 23)
    assert result["Header"] == (25, 27)
    assert result["Store"] == (29, 39)
    assert result["Store.get"] == (30, 34)
    assert result["Store.handle"] == (36, 38)
    assert result["LIMIT"] == (45, 45) and result["other"] == (46, 46)  # no semicolons

def test_js_ts_labels():
    lines = render(outline_text(TSX, ".tsx"), SUMMARIES)
    assert "15-19 component App (default export): Renders the app." in lines
    assert "21-23 hook useThing() (export)" in lines
    assert "29-39 class Store extends Base (export)" in lines
    assert "  30-34 get(): Gets a value." in lines

@pytest.mark.parametrize("text, expected", [
    ("export type Props = Record<string, number>\nexport const x = 1\n", {"Props": (1, 1), "x": (2, 2)}),
    ("export const el = document.getElementById('x')!\nexport const y = 2\n", {"el": (1, 1), "y": (2, 2)}),
    ("export const f = (x) =>\n  x + 1\nexport const g = 2\n", {"f": (1, 2), "g": (3, 3)}),
    ("export const q = db\n  .select()\n  .where()\nexport const h = 1\n", {"q": (1, 3), "h": (4, 4)}),
    ("export function a<T extends { x: 1 }>(v: T) {\n  return v;\n}\n", {"a": (1, 3)}),
    ("export function f(): { a: string } {\n  return { a: '' };\n}\n", {"f": (1, 3)}),
    ("export class A extends B<{ x: 1 }> {\n  m() {\n  }\n}\n", {"A": (1, 4), "A.m": (2, 3)}),
])
def test_js_ts_statement_ends(text, expected):
    assert spans(text, ".ts") == expected

def test_template_literals_do_not_end_statements():
    text = "const q = `\nline;\n{`;\nexport function after() {\n  return 1;\n}\n"
    assert spans(text, ".js")["after"] == (4, 6)

def test_js_notes_list_imports_and_re_exports():
    outline = outline_text('import { a } from "./a";\nexport * from "./b";\nexport { a };\n', ".ts")
    assert outline.notes == ["Imports: ./a, ./b", "Exports: a", "Re-exports all from ./b"]

# --- Markdown ---

MARKDOWN = '''Intro text.

# Guide

Some text.

## Install

```bash
# not a heading
pip install x
```

## Usage
Text

Setext heading
--------------

### Details
More.

# Appendix #
'''

def test_markdown_spans_nest_by_level_and_skip_code_blocks():
    result = spans(MARKDOWN, ".md")
    assert result["(text before the first heading)"] == (1, 1)
    assert result["Guide"] == (3, 21)
    assert result["Guide > Install"] == (7, 12)
    assert result["Guide > Usage"] == (14, 15)
    assert result["Guide > Setext heading"] == (17, 21)
    assert result["Guide > Setext heading > Details"] == (20, 21)
    assert result["Appendix"] == (23, 23)
    assert not any("not a heading" in name for name in result)

def test_markdown_front_matter_is_not_a_heading():
    result = spans("---\ntitle: x\n---\n# Title\n", ".md")
    assert "Title" in result and len([n for n in result if "---" in n]) == 0

def test_markdown_list_before_dashes_is_not_a_heading():
    assert list(spans("- item\n---\n", ".md")) == ["(text before the first heading)"]

def test_unsupported_file_types_have_no_outline():
    assert outline_text("x = 1\n", ".toml") is None

# --- Rendering ---

def test_top_level_detail_still_expands_a_single_root():
    text = "Intro.\n# Title\n## One\n### Deep\n## Two\n"
    assert render(outline_text(text, ".md"), TOP_LEVEL) == ["1-1 (text before the first heading)", "2-5 # Title", "  3-4 ## One", "  5-5 ## Two"]
    assert render(outline_text(MARKDOWN, ".md"), TOP_LEVEL) == ["1-1 (text before the first heading)", "3-21 # Guide", "23-23 # Appendix"]

def test_names_detail_leaves_out_summaries():
    assert "13-27 class Service" in render(outline_text(PYTHON, ".py"), NAMES)

# --- Finding sections ---

@pytest.mark.parametrize("query, expected", [
    ("Service.run", (24, 27)),
    ("run", (24, 27)),
    ("run()", (24, 27)),
    ("class Service", (13, 27)),
    ("24-27 run()", (24, 27)),
    ("service", (13, 27)),  # letter case is ignored if nothing matches exactly
])
def test_sections_are_found_by_name(query, expected):
    matches = find_sections(outline_text(PYTHON, ".py"), query)
    assert [(s.start, s.end) for _, s in matches] == [expected]

@pytest.mark.parametrize("query", ["Install", "## Install", "Guide > Install", "install"])
def test_headings_are_found_by_name(query):
    matches = find_sections(outline_text(MARKDOWN, ".md"), query)
    assert [(s.start, s.end) for _, s in matches] == [(7, 12)]

def test_heading_with_parentheses_is_found_as_written():
    assert len(find_sections(outline_text("# Use foo() here\ntext\n", ".md"), "Use foo() here")) == 1

def test_ambiguous_and_missing_sections():
    text = "# A\n## Notes\n# B\n## Notes\n"
    section, error, message = locate_section(outline_text(text, ".md"), "Notes")
    assert section is None and error == "multiple_sections" and "'A > Notes' (lines 2-2)" in message
    assert locate_section(outline_text(text, ".md"), "A > Notes")[0].start == 2
    section, error, message = locate_section(outline_text(text, ".md"), "Missing")
    assert error == "section_not_found" and "'A', 'A > Notes'" in message

# --- Comparing outlines ---

def test_compare_finds_removed_and_added_sections():
    before = outline_text("def a():\n    pass\n\ndef b():\n    pass\n", ".py")
    after = outline_text("def a():\n    pass\n\ndef c():\n    pass\n", ".py")
    assert compare(before, after) == (["b()"], ["c()"])

def test_compare_counts_duplicate_names():
    before = outline_text("# A\n## Notes\n## Notes\n", ".md")
    after = outline_text("# A\n## Notes\n", ".md")
    assert compare(before, after) == (["# A > ## Notes"], [])

def test_outline_after_write_warns_about_removed_sections_and_syntax_errors():
    before = "class A:\n    def keep(self):\n        pass\n\n    def lost(self):\n        pass\n"
    report = outline_after_write(before, "class A:\n    def keep(self):\n        pass\n", ".py", 2000)
    assert report["removed"] == ["class A > lost()"]
    assert "no longer in the file: class A > lost()" in report["warnings"][0]
    assert report["outline"] == ["1-3 class A", "  2-3 keep()"]

    broken = outline_after_write(before, "class A:\n    def keep(self)\n", ".py", 2000)
    assert "could be outlined before this change, but not anymore" in broken["warnings"][0]
    assert outline_after_write(None, "x", ".txt", 2000) is None

def test_outline_after_write_is_limited():
    text = "".join(f"def function_{i}():\n    pass\n\n" for i in range(200))
    report = outline_after_write(None, text, ".py", 500)
    assert sum(len(line) + 1 for line in report["outline"]) <= 500
    assert report["outline"][-1].startswith("... (") and "more sections not shown" in report["outline"][-1]

# --- Regressions found in review ---

def test_apostrophe_in_jsx_text_does_not_hide_braces():
    text = ("export function App() {\n  const [open] = useState(false);\n  return (\n    <div>\n      {open && <p>Can't open</p>}\n"
            "    </div>\n  );\n}\n\nexport function Other() {\n  return 1;\n}\n")
    assert spans(text, ".tsx") == {"App": (1, 8), "Other": (10, 12)}

def test_arrow_component_with_a_long_parameter_list():
    props = ",\n".join(f"  prop{i}" for i in range(20))
    outline = outline_text("const Card = ({\n" + props + "\n}: Props) => {\n  return null;\n};\n\nexport default Card;\n", ".tsx")
    assert render(outline, NAMES) == ["1-24 component Card (default export)"]

def test_overloads_and_accessor_pairs_span_all_their_declarations():
    overloads = "export function f(a: string): void;\nexport function f(a: number): void;\nexport function f(a: any) {\n  return a;\n}\n"
    assert spans(overloads, ".ts") == {"f": (1, 5)}
    accessors = "class A {\n  get value() {\n    return 1;\n  }\n  set value(v) {\n    this.v = v;\n  }\n}\n"
    assert spans(accessors, ".ts") == {"A": (1, 8), "A.value": (2, 7)}

def test_anonymous_default_class_and_multi_line_calls_in_class_bodies():
    text = "export default class extends React.Component {\n  private items = new Map(\n    compute(),\n  );\n  render() {\n    return null;\n  }\n}\n"
    assert spans(text, ".jsx") == {"(anonymous)": (1, 8), "(anonymous).render": (5, 7)}

def test_constants_exported_separately_are_listed():
    assert render(outline_text("const x = 5;\nexport { x };\n", ".js"), NAMES) == ["1-1 const x (export)"]

@pytest.mark.parametrize("query", ["class App extends Base (export)", "1-4 class App extends Base (export): Main.", "  3-3 run()"])
def test_whole_outline_lines_are_found(query):
    outline = outline_text("/** Main. */\nexport class App extends Base {\n  run() {}\n}\n", ".ts")
    assert len(find_sections(outline, query)) == 1

def test_heading_starting_with_a_kind_word_is_found_as_written():
    outline = outline_text("# A\n## type checking\n## checking\n", ".md")
    assert [name for name, _ in find_sections(outline, "type checking")] == ["A > type checking"]

def test_same_full_names_point_to_line_numbers():
    text = "class A:\n    @property\n    def x(self):\n        return 1\n\n    @x.setter\n    def x(self, v):\n        pass\n"
    _, error, message = locate_section(outline_text(text, ".py"), "x")
    assert error == "multiple_sections" and "read the one you need with start_line and end_line" in message

# --- tree-sitter: cases the earlier regex parser could not handle ---

def test_regex_literal_with_a_backtick():
    assert spans("const re = /a`b/g\nexport function after() {\n  return 1\n}\n", ".js") == {"after": (2, 4)}

def test_decorators_are_part_of_their_section():
    text = ("@Component({\n  selector: 'x',\n})\nexport class Widget {\n  @Input() name: string;\n  /** Handles it. */\n"
            "  @HostListener('click')\n  onClick() {\n    return 1;\n  }\n}\n")
    assert spans(text, ".ts") == {"Widget": (1, 11), "Widget.onClick": (6, 10)}

def test_typescript_declarations():
    text = ("declare function g(): void;\nexport abstract class Base {\n  abstract run(): void;\n  protected helper() {}\n}\n"
            "export function* gen() {\n  yield 1;\n}\nexport const load = async (id: string): Promise<{ a: string }> => {\n  return { a: id };\n};\n")
    assert spans(text, ".ts") == {"g": (1, 1), "Base": (2, 5), "Base.run": (3, 3), "Base.helper": (4, 4), "gen": (6, 8), "load": (9, 11)}

def test_js_syntax_error_is_reported_with_the_outline():
    outline = outline_text("export function a() {\n  return 1;\n}\nexport function b( {\n", ".ts")
    assert outline.syntax_error == "JS/TS syntax error near line 4" and not outline.error
    assert "a" in [s.name for s in outline.sections]

def test_outline_after_write_warns_about_a_new_js_syntax_error_only():
    good, broken = "export function a() {\n  return 1;\n}\n", "export function a() {\n  return 1;\n"
    report = outline_after_write(good, broken, ".ts", 2000)
    assert "Warning: JS/TS syntax error near line" in report["warnings"][0] and "after this change" in report["warnings"][0]
    assert not any("syntax error" in w for w in outline_after_write(broken, broken + "// more\n", ".ts", 2000)["warnings"])

def test_large_js_file_is_outlined():
    # Reading Point.row crashed Python 3.14 with tree-sitter 0.26 as soon as a file had a few hundred declarations
    text = "".join(f"/** Doc {i}. */\nexport function f{i}(a: number): number {{\n  return a + {i};\n}}\n\n" for i in range(3000))
    outline = outline_text(text, ".ts")
    assert len(outline.sections) == 3000 and (outline.sections[-1].start, outline.sections[-1].end) == (14996, 14999)

def test_points_are_read_by_index():
    source = open(outline_module.__file__, encoding="utf-8").read()
    assert not re.search(r"_point\.(row|column)", source), "Read tree-sitter Points by index: Point.row crashes Python 3.14"
