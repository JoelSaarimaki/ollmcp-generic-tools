# --- test_search.py ---
"""
Tests for search-tool-mcp: text search, file pattern search and relevance search.
"""
import re

import pytest

from conftest import content_of

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

@pytest.mark.parametrize("query", ["foo(", "items[0", "a+*"])
def test_invalid_regex_is_searched_as_literal_text(project, query):
    project.write("a.py", f"x = {query}\nfoo\nitems\n")
    response = project.load("search").search_text_in_files(query)
    assert response.startswith(f"- Note: '{query}' is not a valid regular expression")
    assert "searched for as literal text" in response
    assert "Found 1 matches in 1 files" in response and f"a.py:1: x = {query}" in response

def test_invalid_regex_without_matches_still_says_it_was_literal(project):
    project.write("a.py", "x = 1\n")
    response = project.load("search").search_text_in_files("missing(")
    assert "searched for as literal text" in response and "No matches found for 'missing('" in response

def test_valid_regex_is_used_as_a_regex(project):
    project.write("a.py", "value_1\nvalue_22\n")
    response = project.load("search").search_text_in_files(r"value_\d+$")
    assert "Found 2 matches" in response and "Note:" not in response

def test_no_matches_message(search_project):
    assert search_project.load("search").search_text_in_files("zzz", file_pattern="*.py").startswith("- No matches found for 'zzz'")

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
    found = sorted(content_of(response).splitlines()) if "```" in response else []
    assert found == sorted(expected)

# --- Relevance search ---

def ranked_files(response: str) -> list[str]:
    return [line.split(" ", 2)[1] for line in response.splitlines() if re.match(r"\d+\. ", line)]

def test_identifiers_are_split_into_keywords(project):
    search = project.load("search")
    assert search._query_keywords("getUserName(user_id) HTTPServer utf8 a 10 2024") == \
        ["getusername", "get", "user", "name", "userid", "id", "httpserver", "http", "server", "utf8", "utf", "2024"]
    assert search._query_keywords("Käyttäjän nimi") == ["käyttäjän", "nimi"]

def test_a_rare_keyword_outweighs_a_common_one(project):
    for i in range(10):
        project.write(f"common_{i}.py", "value = load()\n" * 20)
    project.write("rare.py", "value = parse_invoice()\n")
    response = project.load("search").search_relevant_files("load value parse invoice")
    assert ranked_files(response)[0] == "rare.py"
    assert "value 11 files 0.0" in response and "parse 1 file 1.00" in response

def test_a_whole_identifier_matches_both_name_styles_and_outranks_its_parts(project):
    project.write("a.py", "def get_user_name():\n    pass\n")
    project.write("b.ts", "function getUserName() {}\n")
    project.write("c.py", "user = get(name)\n")
    search = project.load("search")
    response = search.search_relevant_files("getUserName")
    assert sorted(ranked_files(response)[:2]) == ["a.py", "b.ts"] and "getusername 2 files" in response
    assert search._KeywordCounter(["getusername", "get", "user"])("get_user_name() or getname") == {"getusername": 1, "get": 1, "user": 1}

def test_keywords_match_whole_words_only(project):
    project.write("a.py", "width = valid = hidden = 1\n")
    response = project.load("search").search_relevant_files("id")
    assert response.startswith("- No files contain any of the keywords id")

def test_the_best_section_is_shown_with_the_call_to_read_it(project):
    project.write("src/service.py", "class Service:\n    def start(self):\n        pass\n\n"
                  "    def send_invoice(self, invoice):\n        mail(invoice)\n        return invoice\n")
    response = project.load("search").search_relevant_files("send the invoice")
    assert "1. src/service.py section 'Service.send_invoice' (lines 5-7)" in response
    assert "read_file_with_metadata(path='src/service.py', section='Service.send_invoice')" in response
    assert "5:     def send_invoice(self, invoice):" not in response and "5: def send_invoice(self, invoice):" in response

def test_markdown_points_to_the_subsection_not_the_whole_document(project):
    project.write("README.md", "# Project\n\nIntro.\n\n## Setup\n\nInstall it.\n\n## Proxy\n\nSet the proxy port with PROXY_PORT.\n")
    response = project.load("search").search_relevant_files("proxy port")
    assert "section 'Project > Proxy' (lines 9-11)" in response

def test_files_without_an_outline_show_the_best_lines(project):
    project.write("notes.txt", "\n".join(["filler"] * 50 + ["retry timeout", "retry again"] + ["filler"] * 50))
    response = project.load("search").search_relevant_files("retry timeout")
    assert "1. notes.txt lines 51-52," in response and "start_line=51, end_line=52" in response

def test_a_keyword_in_the_path_ranks_the_file(project):
    project.write("src/invoice.py", "x = 1\n")
    project.write("src/other.py", "y = 2\n")
    response = project.load("search").search_relevant_files("invoice")
    assert "1. src/invoice.py (name only), matched: invoice" in response

def test_missing_and_common_keywords_are_reported(project):
    for i in range(5):
        project.write(f"m{i}.py", "data = 1\n")
    response = project.load("search").search_relevant_files("data fetchUsr")
    assert "Not found: fetchusr, fetch, usr" in response and "All keywords are common" in response

def test_relevance_search_respects_scope_and_ignores(search_project):
    search = search_project.load("search")
    assert sorted(ranked_files(search.search_relevant_files("formatDate", path="src/components"))) == ["src/components/App.tsx"]
    found = ranked_files(search.search_relevant_files("formatDate", max_results=30))
    assert "src/logo.bin" not in found and not any(f.startswith(("node_modules", "generated")) for f in found)
    assert ranked_files(search.search_relevant_files("formatDate", file_pattern="tests/*.py")) == ["tests/test_a.py"]

def test_query_without_keywords_is_an_error(project):
    assert project.load("search").search_relevant_files("a + b = 1").startswith("Error: The query 'a + b = 1' has no keywords")

def test_relevance_results_are_limited_to_the_output_size(project):
    for i in range(40):
        project.write(f"src/mod_{i}.py", "".join(f"def lookup_{j}(): return lookup_value({j})  # {'x' * 200}\n" for j in range(5)))
    project.configure(max_output_chars=3000)
    response = project.load("search").search_relevant_files("lookup value", max_results=30)
    assert len(response) <= 3000 and "Output limited: only" in response

def test_a_definition_ranks_above_its_uses(project):
    project.write("a.py", "def parse_invoice(text):\n    return text.split()\n\n"
                  "def run():\n    parse_invoice(1)\n    parse_invoice(2)\n    parse_invoice(3)\n")
    assert "section 'parse_invoice' (lines 1-2)" in project.load("search").search_relevant_files("parse_invoice")

def test_a_definition_in_a_long_file_ranks_above_short_uses(project):
    project.write("lib/parse.py", "".join(f"def helper_{i}(url):\n    return url\n\n" for i in range(100)) + "def urlsplit(url):\n    return url\n")
    for i in range(5):
        project.write(f"app/use_{i}.py", "from lib.parse import urlsplit\nurlsplit('x')\n")
    response = project.load("search").search_relevant_files("urlsplit")
    assert ranked_files(response)[0] == "lib/parse.py" and "section 'urlsplit' (lines 301-302)" in response
