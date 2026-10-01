# Development notes

Context for continuing development of the MCP servers in this repository: what the tools are for, the principles they follow and why, what has been verified, and what is still open. The [README](README.md) describes how to use the tools; this file describes how and why they are built the way they are.

Last updated: 2026-10-01, after adding `search_relevant_files`. On 2026-09-29: formatting the text responses for the ollmcp console, and before that outlines and section reads replaced the codebase maps, tree-sitter replaced the regex JS/TS parser, and the tools were merged from 27 to 23 with shorter definitions.

## Purpose and target

The servers give a **local AI model** (run with Ollama through [ollmcp](https://github.com/jonigl/mcp-client-for-ollama)) what it needs for AI-driven development of Python and JS/TS projects: understanding the code, editing files, running tests and reviewing changes. Web search and fetch come from `web-search-mcp`, which uses Ollama's hosted web search API.

Most design decisions follow from the target model being small and local:
- **Small context windows** (8k–32k tokens): every response must be limited in size, and the tool definitions themselves cost context on every request.
- **Weak at exact copying and escaping**: models copy `\n`, `\"` or `ä` escapes and line number prefixes literally, drop trailing whitespace and use placeholders such as `// ... existing code ...`.
- **Follow instructions in tool descriptions and responses** fairly well, so errors and limits always say what to do next.

## Current state

Seven servers, 24 tools. All servers share `mcp-servers/mcp_common.py` and read one config file; the map, filesystem, context and search servers also use the outline parsers in `mcp-servers/mcp_outline.py`.

| Server | Tools |
|---|---|
| `safe-filesystem-mcp` | `read_file_with_metadata`, `edit_file`, `write_file`, `create_file`, `read_image`, `list_directory`, `move_file`, `delete_file`, `get_config` |
| `generate-map-mcp` | `get_outline` |
| `search-tool-mcp` | `search_text_in_files`, `search_files_by_pattern`, `search_relevant_files` |
| `git-diff-mcp` | `get_diff`, `get_file_history`, `get_git_status` |
| `commands-mcp` | `list_available_commands`, `run_predefined_command` |
| `context-record-mcp` | `list_context_files`, `read_context_file`, `write_context_file`, `remove_context_file` |
| `web-search-mcp` | `web_search`, `web_fetch` |

Configuration:
- `.mcp.json` only starts the servers and gives each one `MCP_TOOLS_CONFIG`, the path of the tools config file. ollmcp names tools `<server key>.<tool>` (e.g. `codebase-mapper.get_outline`); for cloud providers the dot becomes `_`.
- `tools-config.json` holds all settings: `allowed_dir` (required), `forbidden_paths`, `ignored_dirs`, `gitignore_path`, `max_output_chars`, `commands_config`, `context_folder`, `read_only_files`, `ollama_api_key`.
- `mcp-servers/mcp_common.py` loads and validates the config and holds the shared helpers and constants.
- Dependencies: `mcp`, `ollama` (for `web-search-mcp`) and `tree-sitter` with `tree-sitter-javascript` and `tree-sitter-typescript` (for JS/TS outlines), listed with the `ollmcp` client in `requirements.txt` for users; `requirements-dev.txt` includes it and adds the test runner. The tree-sitter packages are pinned to one minor version each, as the grammars must match the tree-sitter version.
- `mcp-servers/mcp_outline.py` holds the Python, JS/TS and Markdown parsers and everything built on them: rendering outlines at a detail level, finding sections by name and comparing outlines before and after a write. It reads no config and no files.
- `_commands/app-commands.json`, `_context/` and `_instructions/app-instructions.md` are **example** files showing how a project would use the tools. They are not development notes.

## Principles applied

### Security model

- **`allowed_dir` is the boundary.** Every path given to a tool is resolved against it (relative paths too) and must stay inside it. Commands also run in it.
- **`forbidden_paths`** (relative to `allowed_dir`) are denied and hidden by every file-accessing server: filesystem, outline, search and git (through `:(top,exclude)` pathspecs, so a file's history cannot reveal its contents). Deleting or moving a folder that contains a forbidden path is denied.
- **Protected files** are always denied, regardless of the config: files named `.mcp.json` at any depth, and the tools config file in use. Otherwise the AI could remove its own restrictions, which would take effect at the next server start. The name check covers Windows aliases of the same file: letter case, trailing dots or spaces, `::$DATA` streams, 8.3 short names and symlinks.
- **`context-record-mcp`** only accepts plain `.md` names directly inside its folder (no `../`, no `:`), and `read_only_files` cannot be written, appended to or removed. A context file with the same name as a read-only file is hidden and cannot be created.
- **`commands-mcp`** runs registered templates without a shell. The AI's argument is always one argument; arguments starting with `-` or containing line breaks are rejected, and for `.bat`/`.cmd` programs (e.g. `npm` on Windows) also `& | < > ^ % ! " ( )`. Commands get no input (prompts end immediately), have a timeout and succeed only with exit code 0.
- **Known limit, documented in the README:** commands execute project code that the AI can write, so they can read or change any file. Complete protection requires keeping `.mcp.json`, the tools config, the server scripts and the command registry outside the project folder (`ollmcp --servers-json <path>`).
- **`web-search-mcp`** only calls Ollama's hosted API (ollama.com), with the key from `ollama_api_key`; it is passed to the `ollama` client explicitly, so the library's own `OLLAMA_API_KEY` environment variable is not used. `get_config` only reports whether the key is set. `web_fetch` accepts only `http(s)` URLs, and the page is fetched by Ollama's service, not locally. Web content can contain prompt injection; this is not filtered.
- **Ignored folders are not a security measure**: they only keep noise out of the outline and search tools.

### One configuration, strictly validated

- **Config file only.** Servers read no environment variables except `MCP_TOOLS_CONFIG`. There is deliberately no fallback to the old per-server variables (`ALLOWED_DIR`, `INPUT_DIR`, `FORBIDDEN_PATHS`, `MAX_OUTPUT_CHARS`, `GITIGNORE_PATH`, `COMMANDS_CONFIG`, `CONTEXT_FOLDER_PATH`, `READ_ONLY_FILES`, `PROJECT_INSTRUCTIONS_FILE`): one source guarantees that all servers see the same settings.
- **Fail at startup instead of silently degrading.** A server refuses to start if the config is missing or invalid JSON, has an unknown key (typo protection), lacks `allowed_dir` or points it to a missing folder, or has a value of the wrong type. The same applies to the command registry (missing file, invalid JSON, command without `template`/`description`, invalid `timeout`) and to `read_only_files` (missing file, duplicate names).
- **Paths in the config are relative to the config file's folder**, except `forbidden_paths`, which are relative to `allowed_dir`. This keeps the config working when it is moved outside the project.
- **`null` means "use the default"** for every optional setting, so a config can list a setting without using it. An empty list is different: `"ignored_dirs": []` skips nothing, while `null` uses the defaults.
- **Project-specific values belong in the config; code only holds defaults** (e.g. `DEFAULT_IGNORED_DIRS`). A given `ignored_dirs` list replaces the defaults completely, so that a default such as `build` can be un-ignored. Entries are folder names, not paths.
- **One config tool**: `get_config` in `safe-filesystem-mcp` (the most essential server) reports the shared settings and what each server sees. Other servers point to it ("Use get_config to see why") instead of having their own config tools.

### Output limits

- `max_output_chars` (default 40 000 characters, about 10 000 tokens, minimum 2 000) limits every response. Tool-specific limits sit on top: 1000 lines per read, lines over 2000 characters shortened, 100 search matches, 250 directory entries, 200 listed changed files, files over 1 MB not outlined, folders with over 1000 files outlined by subfolder only, outlines after writes up to `min(2500, max_output_chars // 8)` characters, text files over 50 MB not read.
- **A limited response always contains `Output limited:`** (in JSON: an `output_limited` field) saying what was left out and the exact next call: which `start_line` to read next, which subfolders to outline with `path` (with file counts, descending into a folder that holds everything), which `path` has the most search matches, a smaller history `limit`, a narrower command.
- Room for the notes scales with the budget (`min(X, max_output_chars // 4)`), and text inside JSON uses 90 % of its budget to leave room for escaping.
- `_instructions/app-instructions.md` tells the AI to follow these messages instead of repeating the call and never to assume a limited response is complete.
- Command output keeps the start and the end (errors are usually at the end); diffs and files keep the start.

### Outlines and section reads instead of cut-off content

- **Outline first, then read only what is needed.** `get_outline` returns files with their sections (classes, methods, functions, components, types, headings) and the line span of each section; the AI then reads one section with `read_file_with_metadata(path, section=...)` or `start_line`/`end_line`. This replaced `generate_codebase_map`, which described files in walk order until the output limit and dropped the rest, and `generate_file_map`, which only listed the map's file types.
- **Reduce detail instead of cutting.** A folder outline that does not fit drops, in order: summaries, sections below the top level, all sections (files with section counts remain), and finally files in subfolders (subfolders with file counts remain). Every level still covers the whole folder, and the response names the level and suggests subfolders. Folders with more than 1000 files go straight to the last level without reading the files. A single file's outline drops calls, then summaries, then nested sections, and is only cut short as a last resort.
- **Full relative paths instead of a tree with connectors**, so that a path can be copied into the other tools as-is.
- **Reading by name** (`section`), because small models copy line numbers wrongly and line numbers go stale after an edit, while a name resolves against the current file. A name matches the full name (`App.run`, `Guide > Install`) or the name alone, first exactly and then ignoring letter case; an outline line copied as the name (`18-60 class App`, `## Install`, `run()`, even a whole line such as `class App extends Base (export): Summary`) also works, but only if the name as given matches nothing, so that a heading such as `type checking` is not also found as `checking`. Several matches return the candidates with their spans instead of guessing; if they have the same full name, the error points to `start_line` and `end_line`.
- **Outline after every write** to a Python, JS/TS or Markdown file (`edit_file`, `write_file`, `create_file`, `write_context_file`), with the sections removed and added compared to the outline before the write. An explicit "no longer in the file" warning is more reliable than expecting a small model to notice a missing entry. A Python file that parsed before but not after the write is also warned about, which catches broken indentation early, and so is a new JS/TS syntax error (only a new one, so that syntax the grammar does not know is not reported after every write). Blocks of top-level statements are not compared, as their names change with every new constant.
- **Spans**: Python from the first decorator to `end_lineno`, top-level statements between definitions grouped as blocks (`docstring, imports, assignments: A, B`); JS/TS from the JSDoc (and decorators) to the end of the declaration's syntax tree node, overloads and getter/setter pairs spanning all their declarations; Markdown from the heading to the line before the next heading of the same or a higher level, without trailing blank lines, skipping code blocks and front matter.
- The parsers are in their own module, `mcp_outline.py`, which reads no config and no files.
- **JS/TS outlines** list functions, components, hooks, classes with methods, interfaces, types, enums, exported constants, default/named/CommonJS exports, re-exports, local imports and JSDoc summaries. They include namespaces and modules (`namespace N {}`, `declare module "x" {}`, `declare global {}`) with their contents, and every form of default export (`export default` of any value, `export = f`, `export { a as default }`, `module.exports = function/class/name`). Overloads, and getter/setter pairs, form one section only when declared one after the other.
- **JS/TS uses tree-sitter** (`tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`), which replaced a regex parser in 2026-09. The regex parser needed a special case for every construct (type arguments, object return types, apostrophes in JSX text, regex literals, statements continued on the next line) and kept producing wrong spans, which matter now that section reads and the outline after writes depend on them. `.ts` files use the TypeScript grammar, `.tsx` its TSX variant and other files the JavaScript grammar, which includes JSX. tree-sitter also outlines files with syntax errors and reports the first one (`Outline.syntax_error`).

### Responses in the console

- **ollmcp renders a non-JSON response as Markdown when it counts more than 7 Markdown-like patterns** (backticks, `#` lines, list markers, `*`), and otherwise as plain text (`_count_markdown_patterns` in its `utils/tool_display.py`). File content almost always crossed that threshold, and Markdown then joined the header lines into one paragraph, turned `# comments` into headings and ran the `----- BEGIN CONTENT -----` markers into the text.
- **Text responses are therefore written for both modes** with `text_response()` and `code_block()` in `mcp_common.py`: header lines as a list, content and other line-based output (outlines, search matches, listings) in fenced code blocks with the file's language (`code_language()`), and blank lines between the parts. The list markers and fences count as Markdown patterns themselves, so in practice the responses are consistently rendered as Markdown. The fence is longer than any backtick run in the content.
- Models know code fences well; `edit_file` removes copied fence lines from `old_text` like copied line numbers. Short single-line responses (errors, `Successfully removed ...`) stay plain sentences.

### Local-model-friendly reading and editing

- **`read_file_with_metadata` and `edit_file` return plain text**, not JSON: a short metadata header and the exact content in a fenced code block. JSON escaping made models copy `\n` and `\"` into `edit_file`. Errors of these two tools are `Error (<code>): <message>`. `web_fetch` uses the same format, as code is often copied from web pages.
- **`edit_file` tolerates common copying mistakes**: copied `N| ` line number prefixes and code block fence lines are removed, trailing whitespace does not need to match, and when nothing matches, the error shows the lines that match with different indentation so they can be copied exactly. Multiple matches are reported with their line numbers.
- **Hash-checked writes**: every edit needs the SHA-256 from the last read or edit, and the edit response shows the changed lines and the new hash, so edits can be chained without re-reading.
- **Placeholder comments** (`// ... existing code ...`, `# rest of the code`, etc.) are rejected in `edit_file` and `write_file` unless the original already contains one. `write_file` warns if a file of 20+ lines shrinks below half.
- Line endings (LF/CRLF) and BOMs are preserved; writes are atomic.
- **JSON responses use `ensure_ascii=False`**, so `ä` stays readable instead of `\u00e4`.
- `read_image` returns a real MCP image (ollmcp forwards it to vision models) with the format detected from the file's bytes.

### Outline and search

- Folders are skipped without being entered (`walk` in `mcp_common.py`), so a large `node_modules` costs nothing. Only the part of a path inside `allowed_dir` is compared with `ignored_dirs`, so a project inside a folder named e.g. `out` is not hidden.
- The outline and search tools respect `.gitignore` with a simplified matcher (no `!` negations; folders without a trailing `/` match at any depth). The repository's own `.gitignore` is written for that.
- `search_text_in_files` searches for a query that is not a valid regex (e.g. `functionName(`, which small models often search for) as literal text, and says so in a `Note:` line, instead of returning an error.
- `search_relevant_files` is for when the exact text is unknown: the model gives a sentence, code or an error message and gets the best files ranked, each with its best outline section and the call to read it. How it ranks:
  - Keywords are the words and identifiers of the query, each identifier also split into its parts (`getUserName` → `getusername`, `get`, `user`, `name`; `get_user_name` gives the same whole form, so both styles match each other). Keywords have at least 2 characters, numbers at least 3 digits. Files are matched by whole tokens, not substrings, so `id` does not match `width`.
  - Files are scored with BM25: a keyword found in few files weighs much more than one found in almost every file (inverse document frequency), repeats of a keyword add less and less, and long files are scored down. The score is scaled by the share of the query's keyword weight the file has, so a file with several rare keywords beats one with a single keyword repeated. A keyword in the file path adds its weight once.
  - The best section of a file is scored the same way by its own lines, without its subsections, so a document under a single `#` heading or a whole class does not win just by containing every match. Files without an outline are compared 30 lines at a time.
  - The top `3 × max_results` files are reranked with a bonus when their best section is named after a keyword, so the definition of a name ranks above files that only use it.
  - The response lists the keywords with their file counts and weights (0–1), the keywords not found anywhere (often a misspelled or invented name), and a note when all keywords are common.
  - There is no index: every search reads the files again. Text that cannot contain a keyword is skipped with one regex check per line and identifier, which keeps a 2 800-file project (Python's standard library) at about 2 seconds.
- Outlines and text search can be limited to a folder with `path`. The outline of one Python file scans the whole project for Python symbols, so that "Calls:" works across folders; folder outlines do not show calls.

### Tool count

Every tool definition is sent with every request, so tools that duplicate others were removed: `get_file_stats` (covered by the read and the hash check), `is_git_repository` (`get_git_status` returns `not_a_repository`), `instructions-mcp` (now `read_only_files` in `context-record-mcp`), `generate_codebase_map` and `generate_file_map` (now `get_outline`), `get_file_diff` and `get_all_changes_diff` (now `get_diff`), `read_all_context_files` (now `read_context_file` without a filename), `append_to_context_file` (now `write_context_file(append=True)`), `create_directory` (`create_file` and `move_file` create missing folders), the four per-server config tools (now `get_config`) and `read_image_as_base64` (replaced by `read_image`).

### Code conventions

- Files start with `# --- file.py ---`, then stdlib imports, third-party imports (e.g. `ollama`, `tree_sitter`), then `from mcp.server.mcpserver import ...`, `from mcp_common import (...)` and, if needed, `from mcp_outline import (...)`. Sections: `Constants & Config` (`mcp = MCPServer("<Name>-Server")` first), `Internal Helpers`, `Public MCP Tools`.
- Helpers are `_`-prefixed with short summary docstrings. Public names in `mcp_common.py` have no prefix.
- **Tool docstrings are sent to the model with every request**, so they are short: what the tool does and what the model must get right, then `Args:` with one short line per parameter (no types, which are in the schema) and `Returns:` only if the response is not obvious. Guidance that errors give when it is needed (e.g. read again after `hash_mismatch`) is not repeated in descriptions. Optional strings default to `""` instead of `None`, and every server calls `prepare_tools(mcp)` from `mcp_common.py` before `mcp.run()`. It removes the generated titles and `anyOf: [type, null]` from the schemas, and makes an argument sent as `null` use the parameter's default, as small models often send `null` for optional parameters (`test_null_arguments_use_the_defaults`). `test_tool_definitions_stay_small` fails if the definitions grow past 10 800 characters (raised from 10 500 on 2026-10-01 for `search_relevant_files` and the pointers between the two text searches).
- Shared code lives in `mcp_common.py`, and the parsers and outline functions in `mcp_outline.py`; servers never re-implement the path checks, config loading, walking or parsing.
- Built-in generics and `X | None`, double quotes, no bare `except:`.
- Text tools return `Error: <Action> failed:\n{e}\n{traceback}`, except the tools whose responses contain file content (`read_file_with_metadata`, `edit_file`, `web_fetch`), which return `Error (<code>): <message>`; JSON tools always include `"success"` and use `indent=2, ensure_ascii=False`. Error codes are snake_case: a specific condition (`file_not_found`, `hash_mismatch`) or `<action>_error` for an unexpected exception (`read_error`, `search_error`).
- Paths in responses are relative to `allowed_dir` (`display_path`), as the tools accept and show them elsewhere, so that a small model copies the same form everywhere. Only paths outside it, such as the context folder, are shown in full.
- Errors and limits say what to do next and name the tool to use, with its arguments filled in where they are known (e.g. `read_file_with_metadata(path='src/app.py', section=...)`).
- Files use CRLF line endings in the working tree (`core.autocrlf=true`); keep them that way when writing files.

## How changes have been verified

The automated test suite in `tests/` (pytest, 314 tests, about a minute) covers every server. Run it from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

- `conftest.py` gives every test its own temporary project and tools config (`project` fixture; `make_project` for projects at a specific path, e.g. inside a folder named `out`). `project.load("filesystem")` loads a server fresh for that config, and `start_server()` imports a server in a separate process to test startup errors. The working directory is outside the project, so resolving paths against the working directory instead of `allowed_dir` fails the tests.
- One test file per server (`test_filesystem.py`, `test_map.py`, `test_search.py`, `test_git.py`, `test_commands.py`, `test_context.py`, `test_web.py`, which replaces the Ollama client with a fake one, so no requests are sent), plus `test_outline.py` for the parsers, spans, section names and outline comparison, `test_config.py` for config validation, `test_protection.py` for the access rules across all servers, and `test_servers_start.py`, which starts every server over stdio, including from the repository's own `.mcp.json` and `tools-config.json`.
- Windows-only behaviour (name aliases such as `.mcp.json::$DATA`, batch file arguments) is skipped on other systems, and the git tests are skipped if git is not installed.
- The suite was checked by reintroducing earlier bugs (ignored folders compared on the absolute path, protected files not protected, commands run in the working directory); each made tests fail.

Add tests for every new tool or fixed bug.

## Accepted limitations

- `commands-mcp` can reach any file through the code it runs (see the security model).
- In `git-diff-mcp`, a file moved into a forbidden folder is still visible in the history of its old, allowed path, and commit messages are not filtered.
- The `.gitignore` matcher is simplified.
- In a JS/TS file with a syntax error, tree-sitter's error recovery can swallow the declarations after the error, so the outline may be incomplete until the error is fixed. The syntax error itself is always reported.
- `ignored_dirs` names are matched case-sensitively.
- `prepare_tools()` changes the schemas and argument handling through MCPServer's internal `_tool_manager`, as `mcp` has no option for it. An `mcp` upgrade can break it; `test_tool_definitions_stay_small` and `test_null_arguments_use_the_defaults` then fail.
- The console formatting relies on ollmcp's heuristic for when to render a response as Markdown (see Responses in the console). Another client, or a change in ollmcp, can show the responses differently, but they stay readable as plain text.
- `search_relevant_files` does not match word forms: `search` does not find `searching`. Splitting identifiers covers many such cases in code, and stemming would make exact name matches less reliable. Common English words in a query (`the`, `is`) get almost no weight, but words such as `how` can still weigh something in a small project.
- `search_text_in_files` only treats a query as literal text if it is not a valid regex: `func()` is a valid regex that matches `func` without the parentheses, and `a.b` also matches `axb`. The results then include the intended text, with some extra matches.
- Command output is decoded as UTF-8. Python programs are made to write UTF-8 (`PYTHONIOENCODING=utf-8`, found by the test suite), but other programs writing in the Windows code page may still show `�` for characters such as `ä`.

## What should still be done

In recommended order.

1. **Test with a local model on real tasks** (fix a bug, add a feature, rename something), compared with other tools such as the MCP reference servers. The test suite shows that the tools behave as designed, not that a small model finishes tasks better with them.
2. **Add a repo-wide commit log**, e.g. `get_recent_commits(limit, path=None)` and a way to show one commit. Git history is currently only available per file.
3. **Allow a working folder per command**: an optional `cwd` key in the command registry, relative to `allowed_dir`, for monorepos.
4. **Find where a name is defined: a `name` parameter for `get_outline`**, e.g. `get_outline(path="", name="validate_order")` → `src/orders/service.py  42-67 validate_order(): Checks the order`. Small models often know a name (from a traceback, a call site or the task) but not its file, and `read_file_with_metadata(path, section=...)` needs the file. `search_text_in_files` finds every use of the name, not only the definition, and a regex covering all JS/TS definition forms (`function x`, `const x = () =>`, class methods and fields, default exports) is too hard for a small model; a folder outline of a large project leaves out nested sections such as methods to fit the output limit. The parameter would outline the files within `path` as now and return only the sections matching `name`, with the matching of section reads (`find_sections` in `mcp_outline.py`, also `Class.method`), each with its file and line span, and the usual limits if many match. A parameter instead of a new `find_definition` tool keeps the tool definitions smaller (about 100 characters instead of 300) and fits the outline → section read workflow. `search_relevant_files` with the name as the query already ranks a section named after it high, which may be enough. Do this after item 1 shows whether models actually get stuck finding definitions.

Also worth considering: allowing more than one argument in command templates.

## What is deliberately left out

- **Git write tools** (stage, commit, branch): a person reviewing and committing the AI's changes is a sensible safety line.
- **Background processes** (starting a dev server and reading its logs): useful for web apps, but a large addition with its own safety questions. Reconsider if web app development with local models becomes a goal.
- **Environment variable fallback** for the old settings: it would reintroduce the risk of servers seeing different settings.
- **Shell features in command templates** (pipes, `&&`, redirection): no safe way to allow them with arguments on Windows. Such logic goes into a script that a template calls.
- **Operating system read-only flags or a `readonly/` subfolder** for read-only files: platform-dependent and easy to lose; `read_only_files` in the config is used instead.
- **An opt-out for `.mcp.json` protection**: not needed, as the user edits the file directly.

## Notes for working on this repository

- Environment: Windows, Python 3.14, `mcp` 2.1.1 (`MCPServer`, `Image` from `mcp.server.mcpserver`), `tree-sitter` 0.26.0 with `tree-sitter-javascript` 0.25.0 and `tree-sitter-typescript` 0.23.2, ollmcp 0.35, git 2.54.
- **Read tree-sitter `Point`s by index** (`node.start_point[0]`), never as `.row`/`.column`: attribute access crashes Python 3.14 with an access violation as soon as a file has a few hundred declarations. `test_points_are_read_by_index` guards this.
- The servers need `MCP_TOOLS_CONFIG` set to start, also when a script imports them for testing. When loading several servers in one test process, remove `mcp_common` from `sys.modules` before each load so that each gets a fresh config; the test fixtures in `tests/conftest.py` do this.
- ollmcp passes tool responses to the model as text; `ImageContent` is forwarded only to vision-capable models.
