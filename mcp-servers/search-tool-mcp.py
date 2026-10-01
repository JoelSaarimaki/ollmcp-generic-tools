# --- search-tool-mcp.py ---
import math
import os
import re
import traceback
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp_common import (
    ALLOWED_DIR,
    IGNORED_DIRS,
    MAX_OUTPUT_CHARS,
    access_denied_message,
    code_block,
    code_language,
    display_path,
    is_ignored,
    is_path_allowed,
    load_gitignore_patterns,
    prepare_tools,
    resolve_path,
    text_response,
    walk
)
from mcp_outline import (
    iter_sections,
    outline_text,
    qualified_name
)

# --- Constants & Config ---
mcp = MCPServer("Search-Tool-Server")

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit
MAX_RESULTS = 100  # Matches shown at most
MAX_LINE_CHARS = 300  # Longer lines (e.g. minified code) are shortened around the match
MAX_CONTEXT_LINES = 5
BINARY_CHECK_BYTES = 8192  # Files with a null byte in this many first bytes are treated as binary

# Relevance search: files are ranked with BM25, where a keyword found in few files weighs much more than a common one
IDENTIFIER_PATTERN = re.compile(r"\w+")
MIN_KEYWORD_CHARS = 2
MIN_NUMBER_DIGITS = 3  # Shorter numbers such as 0 or 10 are too common to be keywords
MAX_KEYWORDS = 40
DEFAULT_RELEVANT_RESULTS = 10
MAX_RELEVANT_RESULTS = 30
BM25_K1 = 1.2  # How quickly repeats of a keyword in one file stop adding to its score
BM25_B = 0.75  # How much a file's length lowers its score
SPAN_WINDOW_LINES = 30  # Lines compared at a time in files without an outline
SHOWN_LINES_PER_RESULT = 3
RERANKED_RESULTS_FACTOR = 3  # The best max_results times this many files are reranked by their best section
DEFINITION_WEIGHT = BM25_K1 + 1  # A section named after a keyword adds as much as the most its content could
COMMON_KEYWORD_WEIGHT = 0.3  # Keywords below this weight (0-1) are too common to rank results well

# --- Internal Helpers ---

def _resolve_scope(path: str, gitignore_patterns: list[str]) -> tuple[Path, str | None]:
    """
    Resolves the folder or file a search is limited to. Relative paths are resolved against ALLOWED_DIR.
    Returns the path and an error message, which is None if the path can be searched.
    """
    if not path:
        return ALLOWED_DIR, None
    p = resolve_path(path)
    if not is_path_allowed(p):
        return p, f"Error: {access_denied_message(p)}"
    if not p.exists():
        return p, f"Error: Path '{path}' does not exist in the allowed directory {ALLOWED_DIR.as_posix()}."
    if p != ALLOWED_DIR and is_ignored(p, ALLOWED_DIR, IGNORED_DIRS, gitignore_patterns):
        return p, f"Error: Path '{path}' is ignored: it is in an ignored folder or matched by .gitignore. Use get_config to see why."
    return p, None

def _glob_to_regex(pattern: str) -> re.Pattern:
    """
    Converts a glob pattern into a regex that matches a whole relative path.
    '*' and '?' do not match '/', while '**' matches any number of folders.
    Matching is case-insensitive on Windows, like the file system.
    """
    i, n, regex = 0, len(pattern), ""
    while i < n:
        if pattern.startswith("**/", i):
            regex += "(?:[^/]+/)*"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        elif pattern[i] == "[" and "]" in pattern[i + 1:]:
            end = pattern.index("]", i + 1)
            regex += "[" + pattern[i + 1:end].replace("!", "^", 1) + "]"
            i = end + 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.compile(f"{regex}$", re.IGNORECASE if os.name == "nt" else 0)

def _is_binary(path: Path) -> bool:
    """
    Checks if a file looks binary, i.e. has a null byte near its start.
    """
    with open(path, "rb") as f:
        return b"\0" in f.read(BINARY_CHECK_BYTES)

def _shorten_line(line: str, match: re.Match | None = None) -> str:
    """
    Shortens a line longer than MAX_LINE_CHARS, keeping the part around the match.
    """
    if len(line) <= MAX_LINE_CHARS:
        return line
    center = match.start() if match else 0
    start = max(0, min(center - MAX_LINE_CHARS // 3, len(line) - MAX_LINE_CHARS))
    end = start + MAX_LINE_CHARS
    return ("..." if start > 0 else "") + line[start:end] + ("..." if end < len(line) else "")

def _read_lines(path: Path) -> list[str] | None:
    """
    Returns the lines of a text file, or None for binary, too large and unreadable files.
    """
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES or _is_binary(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return None

def _search_file(path: Path, pattern: re.Pattern) -> tuple[list[str], dict[int, re.Match]]:
    """
    Returns the lines of a text file and its matches as {line index: match}.
    Binary, too large and unreadable files return no matches.
    """
    lines = _read_lines(path)
    if lines is None:
        return [], {}
    matches = {}
    for i, line in enumerate(lines):
        match = pattern.search(line)
        if match:
            matches[i] = match
    return lines, matches

def _format_file_matches(rel_path: str, lines: list[str], matches: dict[int, re.Match], context_lines: int, limit: int) -> list[tuple[str, bool]]:
    """
    Formats up to limit matches of one file, grep-style: 'path:line: text' for matching lines and
    'path-line- text' for context lines, with '--' between separate groups of lines.
    Returns (line, is_match) pairs, so that the caller can count the matches it shows.
    """
    shown = sorted(matches)[:limit]
    if not context_lines:
        return [(f"{rel_path}:{i + 1}: {_shorten_line(lines[i].strip(), matches[i])}", True) for i in shown]

    # Merge overlapping context ranges into groups
    groups = []
    for i in shown:
        start, end = max(0, i - context_lines), min(len(lines) - 1, i + context_lines)
        if groups and start <= groups[-1][1] + 1:
            groups[-1][1] = max(groups[-1][1], end)
        else:
            groups.append([start, end])

    out = []
    for start, end in groups:
        if out:
            out.append(("--", False))
        for i in range(start, end + 1):
            if i in matches and i in shown:
                out.append((f"{rel_path}:{i + 1}: {_shorten_line(lines[i], matches[i])}", True))
            else:
                out.append((f"{rel_path}-{i + 1}- {_shorten_line(lines[i])}", False))
    return out

def _suggest_folder(scope: Path, file_matches: list[tuple[tuple[str, ...], int]]) -> tuple[str, int] | None:
    """
    Returns the subfolder of scope with the most matches and its match count, from (folder parts, match count)
    pairs of the matching files. While one subfolder holds all the matches, its subfolders are compared instead.
    Returns None if the matches are not in subfolders.
    """
    prefix = ()
    while True:
        counts, direct = {}, 0
        for folders, count in file_matches:
            if folders[:len(prefix)] != prefix:
                continue
            if len(folders) > len(prefix):
                counts[folders[len(prefix)]] = counts.get(folders[len(prefix)], 0) + count
            else:
                direct += count
        if len(counts) == 1 and not direct:
            prefix += (next(iter(counts)),)
            continue
        if counts:
            top = max(counts, key=counts.get)
            prefix, count = prefix + (top,), counts[top]
        else:
            count = direct
        break
    if not prefix:
        return None
    return scope.joinpath(*prefix).relative_to(ALLOWED_DIR).as_posix(), count

def _format_list(items: list[str], location: str) -> str:
    """
    Formats a list of matching paths, capped at MAX_RESULTS.
    """
    header = [f"Found {len(items)} matches in {location}:"]
    if len(items) > MAX_RESULTS:
        header.append(f"Output limited: only the first {MAX_RESULTS} of {len(items)} matches are shown."
                      f" Use a more specific pattern to see the rest, e.g. 'src/**/*.py' instead of '*.py' with recursive=True.")
    return text_response(header, code_block("\n".join(items[:MAX_RESULTS])))

def _files_in_scope(scope: Path, gitignore_patterns: list[str], file_pattern: str) -> list[Path]:
    """
    Returns the files to search: scope itself if it is a file, or the files within it that are not ignored
    and match file_pattern, if given.
    """
    if scope.is_file():
        return [scope]
    # Anchored at a '/' so that 'tests/*.py' matches 'src/tests/a.py' but not 'src/mytests/a.py'
    file_regex = _glob_to_regex("/" + file_pattern.replace("\\", "/").lstrip("/")) if file_pattern else None
    return [p for p, is_dir in walk(ALLOWED_DIR, gitignore_patterns, scope)
            if not is_dir and (not file_regex or file_regex.search("/" + p.relative_to(ALLOWED_DIR).as_posix()))]

def _is_keyword(word: str) -> bool:
    """
    Checks if a word or identifier part is long enough to be a keyword.
    """
    return len(word) >= (MIN_NUMBER_DIGITS if word.isdigit() else MIN_KEYWORD_CHARS)

@lru_cache(maxsize=100_000)
def _identifier_keywords(identifier: str) -> tuple[str, tuple[str, ...]]:
    """
    Splits an identifier such as 'getUserName', 'get_user_name' or 'HTTPServer2' into lowercase parts.
    Returns the whole identifier without '_' (so that both forms of a name match each other) and
    the parts that are keywords. Cached, as the same identifiers repeat throughout a project.
    """
    parts = []
    for chunk in identifier.split("_"):
        start = 0
        for i in range(1, len(chunk)):
            prev, cur, nxt = chunk[i - 1], chunk[i], chunk[i + 1:i + 2]
            # Boundaries: 'getUser', 'utf8', and 'HTTPServer' before the 'S'
            if (prev.islower() and cur.isupper()) or prev.isdigit() != cur.isdigit() \
               or (prev.isupper() and cur.isupper() and nxt.islower()):
                parts.append(chunk[start:i])
                start = i
        if chunk:
            parts.append(chunk[start:])
    return "".join(parts).casefold(), tuple(dict.fromkeys(p.casefold() for p in parts if _is_keyword(p)))

def _query_keywords(query: str) -> list[str]:
    """
    Returns the keywords of a query in order: each identifier as a whole and its parts.
    """
    keywords = {}
    for identifier in IDENTIFIER_PATTERN.findall(query):
        whole, parts = _identifier_keywords(identifier)
        for keyword in (whole, *parts):
            if _is_keyword(keyword):
                keywords[keyword] = None
    return list(keywords)[:MAX_KEYWORDS]

class _KeywordCounter:
    """
    Counts the keywords of one query in texts, each identifier as a whole and by its parts, so that
    'getUserName' ranks a file above one that only has 'get', 'user' and 'name' in separate places.
    """
    def __init__(self, keywords: list[str]):
        self.keywords = set(keywords)
        self._regex = re.compile("|".join(re.escape(k) for k in self.keywords))
        self._found = {}  # Identifier: the keywords it matches

    def may_match(self, text: str) -> bool:
        """
        Checks quickly if text can contain a keyword: every keyword is part of an identifier without '_'.
        """
        return bool(self._regex.search(text.casefold().replace("_", "")))

    def __call__(self, text: str) -> dict[str, int]:
        """
        Returns the keywords found in text and their counts.
        """
        counts = {}
        if not self.may_match(text):
            return counts
        for identifier in IDENTIFIER_PATTERN.findall(text):
            found = self._found.get(identifier)
            if found is None:
                found = ()
                if self.may_match(identifier):
                    whole, parts = _identifier_keywords(identifier)
                    found = tuple(k for k in dict.fromkeys((whole, *parts)) if k in self.keywords)
                self._found[identifier] = found
            for keyword in found:
                counts[keyword] = counts.get(keyword, 0) + 1
        return counts

def _idf(file_count: int, total_files: int) -> float:
    """
    Returns the BM25 weight of a keyword found in file_count of total_files files: highest for a keyword
    in one file and close to 0, never negative, for a keyword in almost every file.
    """
    return math.log(1 + (total_files - file_count + 0.5) / (file_count + 0.5))

def _saturated(count: int, norm: float = BM25_K1) -> float:
    """
    Returns the BM25 term frequency factor: repeats of a keyword add less and less.
    """
    return count * (BM25_K1 + 1) / (count + norm)

def _span_counts(line_hits: dict[int, dict[str, int]], line_indexes) -> dict[str, int]:
    """
    Counts the keywords found in the given lines (0-based).
    """
    counts = {}
    for i in line_indexes:
        for keyword, count in line_hits.get(i, {}).items():
            counts[keyword] = counts.get(keyword, 0) + count
    return counts

def _bm25(counts: dict[str, int], length: float, avg_length: float, idf: dict[str, float], extra: set[str] = frozenset()) -> float:
    """
    Scores a file or section by its keyword counts and length, scaled by the share of the query's keyword
    weight it has. Keywords in extra (e.g. found in the file path) add their weight once.
    """
    norm = BM25_K1 * (1 - BM25_B + BM25_B * length / max(1, avg_length))
    score = sum(idf[k] * _saturated(c, norm) for k, c in counts.items()) + sum(idf[k] for k in extra)
    return score * sum(idf[k] for k in set(counts) | extra) / sum(idf.values())

def _best_span(lines: list[str], line_hits: dict[int, dict[str, int]], suffix: str, idf: dict[str, float], counter: _KeywordCounter) -> tuple[str, bool, int, int, set[str]]:
    """
    Finds the part of a file that best matches the keywords. Outline sections (classes, functions,
    headings) are scored like files by their name and own lines, without their subsections, so that a whole
    document under one heading does not win just by containing every match, and a method is shown
    instead of its class. Files without an outline, or with matches outside all sections, are compared
    SPAN_WINDOW_LINES lines at a time.
    Returns the section's full name ("" for none), whether the span is the whole section (so that it
    can be read by name), the first and last line of the span (0-based) and the keywords in the section's name.
    """
    outline = outline_text("\n".join(lines), suffix)
    if outline and not outline.error:
        own_lines = []
        for path, section in iter_sections(outline.sections):
            nested = {i for child in section.children for i in range(child.start - 1, child.end)}
            own = [i for i in range(section.start - 1, section.end) if i not in nested]
            own_lines.append((qualified_name(path), section, own))
        avg_length = sum(len(own) for _, _, own in own_lines) / max(1, len(own_lines))
        best, best_score = None, 0.0
        for name, section, own in own_lines:
            counts = _span_counts(line_hits, own)
            if not counts:
                continue
            # A keyword in the section's name, e.g. the function asked about, ranks its definition above its uses
            defined = set(counter(section.name)) if section.kind != "block" else set()
            score = _bm25(counts, len(own), avg_length, idf, defined)
            if score > best_score:
                best, best_score = (name, section, own, defined), score
        if best:
            name, section, own, defined = best
            if not section.children and section.kind != "block":
                return name, True, section.start - 1, section.end - 1, defined
            hit_own = [i for i in own if i in line_hits]
            return name, False, hit_own[0], hit_own[-1], defined

    hit_lines = sorted(line_hits)
    best_span, best_score = (hit_lines[0], hit_lines[0]), -1.0
    for j, start in enumerate(hit_lines):
        window = hit_lines[j:bisect_left(hit_lines, start + SPAN_WINDOW_LINES)]
        score = _bm25(_span_counts(line_hits, window), SPAN_WINDOW_LINES, SPAN_WINDOW_LINES, idf)
        if score > best_score:
            best_span, best_score = (start, window[-1]), score
    return "", False, best_span[0], best_span[1], set()

def _format_relevant_lines(lines: list[str], line_hits: dict[int, dict[str, int]], start: int, end: int, idf: dict[str, float]) -> str:
    """
    Formats the best matching lines between start and end (0-based) as 'line: text', in file order.
    """
    in_span = [i for i in line_hits if start <= i <= end]
    best = sorted(in_span, key=lambda i: -sum(idf[k] for k in line_hits[i]))[:SHOWN_LINES_PER_RESULT]
    shown = []
    for i in sorted(best):
        text = lines[i].strip()
        match = next((m for k in line_hits[i] if (m := re.search(re.escape(k), text, re.IGNORECASE))), None)
        shown.append(f"{i + 1}: {_shorten_line(text, match)}")
    return "\n".join(shown)

# --- Public MCP Tools ---

@mcp.tool()
def search_text_in_files(query: str, case_sensitive: bool = False, path: str = "", file_pattern: str = "", context_lines: int = 0) -> str:
    """
    Searches the project's text files for a regex, skipping ignored folders and .gitignored files.
    A query that is not a valid regex, such as 'foo(', is searched for as literal text.
    If the exact text is unknown, use search_relevant_files.

    Args:
        query: The regex or text.
        case_sensitive: Match upper and lower case exactly. Defaults to False.
        path: A folder or file to search in. Defaults to the whole project.
        file_pattern: A glob the file path must match, e.g. '*.py' or 'tests/*.ts'.
        context_lines: Lines to show around each match, up to 5. Defaults to 0.

    Returns:
        Matches as 'path:line: text', context lines as 'path-line- text'.
    """
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        # Small models often search for code such as 'functionName(', which is not a valid regex
        literal_note = ""
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            pattern = re.compile(re.escape(query), flags)
            literal_note = f"Note: '{query}' is not a valid regular expression ({e}), so it was searched for as literal text."

        gitignore_patterns = load_gitignore_patterns()
        scope, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error

        context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
        files = _files_in_scope(scope, gitignore_patterns, file_pattern)

        # Show matches until MAX_RESULTS matches or MAX_OUTPUT_CHARS characters, leaving room for the notes
        budget = MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4)
        total_matches, matched_files, shown_matches, used, full = 0, 0, 0, 0, False
        file_matches = []
        output = []
        for file in files:
            rel_path = file.relative_to(ALLOWED_DIR).as_posix()
            lines, matches = _search_file(file, pattern)
            if not matches:
                continue
            total_matches += len(matches)
            matched_files += 1
            folders = file.relative_to(scope).parts[:-1] if scope.is_dir() else ()
            file_matches.append((folders, len(matches)))

            if full or shown_matches >= MAX_RESULTS:
                continue
            for line, is_match in _format_file_matches(rel_path, lines, matches, context_lines, MAX_RESULTS - shown_matches):
                if used + len(line) + 1 > budget:
                    full = True
                    break
                output.append(line)
                used += len(line) + 1
                shown_matches += is_match
            output.append("")

        location = f"'{display_path(scope, ALLOWED_DIR)}'" if scope != ALLOWED_DIR else "the project"
        if not total_matches:
            filter_note = f" (files matching '{file_pattern}')" if file_pattern else ""
            return text_response([literal_note, f"No matches found for '{query}' in {location}{filter_note}"])

        header = [literal_note, f"Found {total_matches} matches in {matched_files} files in {location}:"]
        if shown_matches < total_matches:
            hints = []
            suggestion = _suggest_folder(scope, file_matches) if scope.is_dir() else None
            if suggestion:
                hints.append(f"path (e.g. path='{suggestion[0]}', which has {suggestion[1]} matches)")
            if not file_pattern:
                hints.append("file_pattern (e.g. file_pattern='*.py')")
            if context_lines:
                hints.append("fewer context_lines")
            hints.append("a more specific query")
            header.append(f"Output limited: only {shown_matches} of {total_matches} matches are shown, as the output is limited to {MAX_RESULTS} matches or {MAX_OUTPUT_CHARS} characters."
                       f" Narrow the search with {', '.join(hints[:-1])} or {hints[-1]}.")
        return text_response(header, code_block("\n".join(output).rstrip()))
    except Exception as e:
        return f"Error: Searching text in files failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def search_files_by_pattern(pattern: str, recursive: bool = False) -> str:
    """
    Finds files and folders by a glob pattern relative to the project, e.g. '*.ts', 'src/utils/*.py'
    or 'src/**/*.test.ts' ('**' matches any number of folders). Folders end with '/'.

    Args:
        pattern: The glob pattern.
        recursive: Also match in all subfolders, e.g. '*.py' anywhere. Defaults to False.
    """
    try:
        glob = pattern.strip().replace("\\", "/").removeprefix("./")
        if not glob:
            return "Error: The pattern must not be empty."
        if recursive:
            glob = "**/" + glob
        regex = _glob_to_regex(glob)
        # Without '**', only paths with as many parts as the pattern can match, so deeper folders are not entered
        max_depth = None if "**" in glob else len(glob.split("/"))

        gitignore_patterns = load_gitignore_patterns()
        matches = []
        for path, is_dir in walk(ALLOWED_DIR, gitignore_patterns, ALLOWED_DIR, max_depth):
            rel_path = path.relative_to(ALLOWED_DIR).as_posix()
            if regex.match(rel_path):
                matches.append(rel_path + ("/" if is_dir else ""))

        if not matches:
            msg = f"No files found matching pattern: '{pattern}'"
            if not recursive:
                msg += ". Try setting recursive=True to search subdirectories."
            return msg

        return _format_list(matches, "the project")
    except Exception as e:
        return f"Error: Searching files by pattern failed:\n{e}\n{traceback.format_exc()}"

@mcp.tool()
def search_relevant_files(query: str, path: str = "", file_pattern: str = "", max_results: int = 10) -> str:
    """
    Ranks files and their best section by how well they match the words and names in a sentence,
    code or error message. Names are also matched by parts (getUserName: get, user, name), and words
    found in few files weigh most. Use it when the exact text or file is unknown. Shows only the best
    files: to find every use of a name, e.g. before renaming it, use search_text_in_files.

    Args:
        query: Words, names or code.
        path: A folder or file to search in. Defaults to the whole project.
        file_pattern: A glob the file path must match, e.g. '*.py'.
        max_results: Files to show, up to 30. Defaults to 10.
    """
    try:
        keywords = _query_keywords(query)
        if not keywords:
            return f"Error: The query '{query}' has no keywords. Use words or names of at least {MIN_KEYWORD_CHARS} letters."
        counter = _KeywordCounter(keywords)

        gitignore_patterns = load_gitignore_patterns()
        scope, error = _resolve_scope(path, gitignore_patterns)
        if error:
            return error
        max_results = max(1, min(int(max_results), MAX_RELEVANT_RESULTS))

        # First pass: count the keywords in every file, keeping only the counts of the matching files
        total_files, total_chars, matched = 0, 0, []
        for file in _files_in_scope(scope, gitignore_patterns, file_pattern):
            lines = _read_lines(file)
            if lines is None:
                continue
            total_files += 1
            total_chars += sum(len(line) for line in lines)
            rel_path = file.relative_to(ALLOWED_DIR).as_posix()
            path_hits = set(counter(rel_path))
            line_hits = {}
            if counter.may_match("\n".join(lines)):
                for i, line in enumerate(lines):
                    hits = counter(line)
                    if hits:
                        line_hits[i] = hits
            if line_hits or path_hits:
                counts = {}
                for hits in line_hits.values():
                    for keyword, count in hits.items():
                        counts[keyword] = counts.get(keyword, 0) + count
                matched.append((file, rel_path, counts, path_hits, line_hits, sum(len(line) for line in lines)))

        location = f"'{display_path(scope, ALLOWED_DIR)}'" if scope != ALLOWED_DIR else "the project"
        filter_note = f" (files matching '{file_pattern}')" if file_pattern else ""
        if not matched:
            return text_response([f"No files contain any of the keywords {', '.join(keywords)} in {location}{filter_note}."
                                  " Check the spelling, or use other words or names."])

        file_counts = {k: sum(1 for m in matched if k in m[2] or k in m[3]) for k in keywords}
        idf = {k: _idf(n, total_files) for k, n in file_counts.items() if n}
        top_idf = _idf(1, total_files)
        avg_chars = total_chars / total_files

        ranked = sorted(((_bm25(counts, chars, avg_chars, idf, path_hits), rel_path, file, counts, path_hits, line_hits)
                         for file, rel_path, counts, path_hits, line_hits, chars in matched), key=lambda r: (-r[0], r[1]))

        found = sorted(idf, key=lambda k: -idf[k])
        weights = ", ".join(f"{k} {file_counts[k]} {'file' if file_counts[k] == 1 else 'files'} {idf[k] / top_idf:.2f}" for k in found)
        header = [f"Keywords (files containing it, weight 0-1): {weights}"]
        missing = [k for k in keywords if k not in idf]
        if missing:
            header.append(f"Not found: {', '.join(missing)}")
        if max(idf[k] / top_idf for k in found) < COMMON_KEYWORD_WEIGHT:
            header.append("All keywords are common, so the ranking is weak. Add a more specific word or name.")

        # Second pass: find the best section of the top files. A file whose best section is named after
        # a keyword, e.g. the definition of a function asked about, is ranked above files that only use it.
        results = []
        for score, rel_path, file, counts, path_hits, line_hits in ranked[:max_results * RERANKED_RESULTS_FACTOR]:
            lines, span, defined = [], None, set()
            if line_hits:
                lines = _read_lines(file) or []
                span = _best_span(lines, line_hits, file.suffix, idf, counter)
                defined = span[4]
            results.append((score + DEFINITION_WEIGHT * sum(idf[k] for k in defined), rel_path, file, counts, path_hits, line_hits, lines, span))
        results.sort(key=lambda r: (-r[0], r[1]))

        budget = MAX_OUTPUT_CHARS - min(1500, MAX_OUTPUT_CHARS // 4)
        parts, used, next_call = [], 0, ""
        for _, rel_path, file, counts, path_hits, line_hits, lines, span in results[:max_results]:
            matched_keywords = ", ".join(k for k in found if k in counts or k in path_hits)
            if not span:
                part = f"{len(parts) + 1}. {rel_path} (name only), matched: {matched_keywords}"
            else:
                section, whole, start, end, _ = span
                where = f"section '{section}' (lines {start + 1}-{end + 1})" if whole \
                    else f"lines {start + 1}-{end + 1}" + (f" in '{section}'" if section else "")
                part = f"{len(parts) + 1}. {rel_path} {where}, matched: {matched_keywords}\n" \
                       + code_block(_format_relevant_lines(lines, line_hits, start, end, idf), code_language(file))
                if not next_call:
                    target = f"section='{section}'" if whole else f"start_line={start + 1}, end_line={end + 1}"
                    next_call = f"read_file_with_metadata(path='{rel_path}', {target})"
            if used + len(part) > budget:
                break
            parts.append(part)
            used += len(part) + 2

        if len(parts) < min(max_results, len(matched)):
            header.append(f"Output limited: only {len(parts)} results fit in {MAX_OUTPUT_CHARS} characters. Use a lower max_results, path or file_pattern.")
        if next_call:
            header.append(f"Read a result with e.g. {next_call}")
        header.append(f"{len(matched)} of {total_files} files in {location}{filter_note} match, the best {len(parts)} are shown:")
        return text_response(header, *parts)
    except Exception as e:
        return f"Error: Searching relevant files failed:\n{e}\n{traceback.format_exc()}"

prepare_tools(mcp)

if __name__ == "__main__":
    mcp.run()
