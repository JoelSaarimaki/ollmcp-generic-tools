# Development notes

Context for continuing development of the MCP servers in this repository: what the tools are for, the principles they follow and why, what has been verified, and what is still open. The [README](README.md) describes how to use the tools; this file describes how and why they are built the way they are.

Last updated: 2026-09-29, after the commit "Major rewrite: implement single tools configuration file".

## Purpose and target

The servers give a **local AI model** (run with Ollama through [ollmcp](https://github.com/jonigl/mcp-client-for-ollama)) what it needs for AI-driven development of Python and JS/TS projects: understanding the code, editing files, running tests and reviewing changes. Web search is expected to come from a separate tool.

Most design decisions follow from the target model being small and local:
- **Small context windows** (8k–32k tokens): every response must be limited in size, and the tool definitions themselves cost context on every request.
- **Weak at exact copying and escaping**: models copy `\n`, `\"` or `ä` escapes and line number prefixes literally, drop trailing whitespace and use placeholders such as `// ... existing code ...`.
- **Follow instructions in tool descriptions and responses** fairly well, so errors and limits always say what to do next.

## Current state

Six servers, 26 tools. All servers share `mcp-servers/mcp_common.py` and read one config file.

| Server | Tools |
|---|---|
| `safe-filesystem-mcp` | `read_file_with_metadata`, `edit_file`, `write_file`, `create_file`, `read_image`, `list_directory`, `create_directory`, `move_file`, `delete_file`, `get_config` |
| `generate-map-mcp` | `generate_codebase_map`, `generate_file_map` |
| `search-tool-mcp` | `search_text_in_files`, `search_files_by_pattern` |
| `git-diff-mcp` | `get_file_diff`, `get_all_changes_diff`, `get_file_history`, `get_git_status` |
| `commands-mcp` | `list_available_commands`, `run_predefined_command` |
| `context-record-mcp` | `list_context_files`, `read_context_file`, `write_context_file`, `append_to_context_file`, `read_all_context_files`, `remove_context_file` |

Configuration:
- `.mcp.json` only starts the servers and gives each one `MCP_TOOLS_CONFIG`, the path of the tools config file. ollmcp names tools `<server key>.<tool>` (e.g. `codebase-mapper.generate_codebase_map`); for cloud providers the dot becomes `_`.
- `tools-config.json` holds all settings: `allowed_dir` (required), `forbidden_paths`, `ignored_dirs`, `gitignore_path`, `max_output_chars`, `commands_config`, `context_folder`, `read_only_files`.
- `mcp-servers/mcp_common.py` loads and validates the config and holds the shared helpers and constants.
- `_commands/app-commands.json`, `_context/` and `_instructions/app-instructions.md` are **example** files showing how a project would use the tools. They are not development notes.

## Principles applied

### Security model

- **`allowed_dir` is the boundary.** Every path given to a tool is resolved against it (relative paths too) and must stay inside it. Commands also run in it.
- **`forbidden_paths`** (relative to `allowed_dir`) are denied and hidden by every file-accessing server: filesystem, map, search and git (through `:(top,exclude)` pathspecs, so a file's history cannot reveal its contents). Deleting or moving a folder that contains a forbidden path is denied.
- **Protected files** are always denied, regardless of the config: files named `.mcp.json` at any depth, and the tools config file in use. Otherwise the AI could remove its own restrictions, which would take effect at the next server start. The name check covers Windows aliases of the same file: letter case, trailing dots or spaces, `::$DATA` streams, 8.3 short names and symlinks.
- **`context-record-mcp`** only accepts plain `.md` names directly inside its folder (no `../`, no `:`), and `read_only_files` cannot be written, appended to or removed. A context file with the same name as a read-only file is hidden and cannot be created.
- **`commands-mcp`** runs registered templates without a shell. The AI's argument is always one argument; arguments starting with `-` or containing line breaks are rejected, and for `.bat`/`.cmd` programs (e.g. `npm` on Windows) also `& | < > ^ % ! " ( )`. Commands get no input (prompts end immediately), have a timeout and succeed only with exit code 0.
- **Known limit, documented in the README:** commands execute project code that the AI can write, so they can read or change any file. Complete protection requires keeping `.mcp.json`, the tools config, the server scripts and the command registry outside the project folder (`ollmcp --servers-json <path>`).
- **Ignored folders are not a security measure**: they only keep noise out of the map and search tools.

### One configuration, strictly validated

- **Config file only.** Servers read no environment variables except `MCP_TOOLS_CONFIG`. There is deliberately no fallback to the old per-server variables (`ALLOWED_DIR`, `INPUT_DIR`, `FORBIDDEN_PATHS`, `MAX_OUTPUT_CHARS`, `GITIGNORE_PATH`, `COMMANDS_CONFIG`, `CONTEXT_FOLDER_PATH`, `READ_ONLY_FILES`, `PROJECT_INSTRUCTIONS_FILE`): one source guarantees that all servers see the same settings.
- **Fail at startup instead of silently degrading.** A server refuses to start if the config is missing or invalid JSON, has an unknown key (typo protection), lacks `allowed_dir` or points it to a missing folder, or has a value of the wrong type. The same applies to the command registry (missing file, invalid JSON, command without `template`/`description`, invalid `timeout`) and to `read_only_files` (missing file, duplicate names).
- **Paths in the config are relative to the config file's folder**, except `forbidden_paths`, which are relative to `allowed_dir`. This keeps the config working when it is moved outside the project.
- **`null` means "use the default"** for every optional setting, so a config can list a setting without using it. An empty list is different: `"ignored_dirs": []` skips nothing, while `null` uses the defaults.
- **Project-specific values belong in the config; code only holds defaults** (e.g. `DEFAULT_IGNORED_DIRS`). A given `ignored_dirs` list replaces the defaults completely, so that a default such as `build` can be un-ignored. Entries are folder names, not paths.
- **One config tool**: `get_config` in `safe-filesystem-mcp` (the most essential server) reports the shared settings and what each server sees. Other servers point to it ("Use get_config to see why") instead of having their own config tools.

### Output limits

- `max_output_chars` (default 40 000 characters, about 10 000 tokens, minimum 2 000) limits every response. Tool-specific limits sit on top: 1000 lines per read, lines over 2000 characters shortened, 100 search matches, 400-line file tree, 250 directory entries, 200 listed changed files, files over 1 MB not parsed for the map, text files over 50 MB not read.
- **A limited response always contains `Output limited:`** (in JSON: an `output_limited` field) saying what was left out and the exact next call: which `start_line` to read next, which subfolders to map with `path` (with file counts, descending into a folder that holds everything), which `path` has the most search matches, a smaller history `limit`, a narrower command.
- Room for the notes scales with the budget (`min(X, max_output_chars // 4)`), and text inside JSON uses 90 % of its budget to leave room for escaping.
- `_instructions/app-instructions.md` tells the AI to follow these messages instead of repeating the call and never to assume a limited response is complete.
- Command output keeps the start and the end (errors are usually at the end); diffs and files keep the start.

### Local-model-friendly reading and editing

- **`read_file_with_metadata` and `edit_file` return plain text**, not JSON: a short metadata header and the exact content between `----- BEGIN CONTENT -----` and `----- END CONTENT -----`. JSON escaping made models copy `\n` and `\"` into `edit_file`. Errors of these two tools are `Error (<code>): <message>`.
- **`edit_file` tolerates common copying mistakes**: copied `N| ` line number prefixes are removed, trailing whitespace does not need to match, and when nothing matches, the error shows the lines that match with different indentation so they can be copied exactly. Multiple matches are reported with their line numbers.
- **Hash-checked writes**: every edit needs the SHA-256 from the last read or edit, and the edit response shows the changed lines and the new hash, so edits can be chained without re-reading.
- **Placeholder comments** (`// ... existing code ...`, `# rest of the code`, etc.) are rejected in `edit_file` and `write_file` unless the original already contains one. `write_file` warns if a file of 20+ lines shrinks below half.
- Line endings (LF/CRLF) and BOMs are preserved; writes are atomic.
- **JSON responses use `ensure_ascii=False`**, so `ä` stays readable instead of `ä`.
- `read_image` returns a real MCP image (ollmcp forwards it to vision models) with the format detected from the file's bytes.

### Map and search

- Folders are skipped without being entered (`walk` in `mcp_common.py`), so a large `node_modules` costs nothing. Only the part of a path inside `allowed_dir` is compared with `ignored_dirs`, so a project inside a folder named e.g. `out` is not hidden.
- The map and search tools respect `.gitignore` with a simplified matcher (no `!` negations; folders without a trailing `/` match at any depth). The repository's own `.gitignore` is written for that.
- The JS/TS parser is regex-based but masks comments and strings first, so commented-out code is not reported and braces in strings do not break class bodies. It lists functions, components, hooks, classes with methods, interfaces, types, enums, exported constants, default/named/CommonJS exports, re-exports, local imports and JSDoc summaries.
- Maps and text search can be limited to a folder with `path`; the whole project is still scanned for Python symbols so that "Calls:" works across folders.

### Tool count

Every tool definition is sent with every request, so tools that duplicate others were removed: `get_file_stats` (covered by the read and the hash check), `is_git_repository` (`get_git_status` returns `not_a_repository`), `instructions-mcp` (now `read_only_files` in `context-record-mcp`), the four per-server config tools (now `get_config`) and `read_image_as_base64` (replaced by `read_image`).

### Code conventions

- Files start with `# --- file.py ---`, then stdlib imports, then `from mcp.server.mcpserver import ...` and `from mcp_common import (...)`. Sections: `Constants & Config` (`mcp = MCPServer("<Name>-Server")` first), `Internal Helpers`, `Public MCP Tools`.
- Helpers are `_`-prefixed with short summary docstrings; only `@mcp.tool()` functions get full `Args:`/`Returns:` sections, because only those are read by the AI. Public names in `mcp_common.py` have no prefix.
- Shared code lives in `mcp_common.py`; servers never re-implement the path checks, config loading or walking.
- Built-in generics and `X | None`, double quotes, no bare `except:`.
- Text tools return `Error: <Action> failed:\n{e}\n{traceback}`; JSON tools always include `"success"` and use `indent=2, ensure_ascii=False`.
- Files use CRLF line endings in the working tree (`core.autocrlf=true`); keep them that way when writing files.

## How changes have been verified

There is no automated test suite in the repository yet. Changes were verified with scripts run against temporary test projects, covering among others: exact read/edit round trips with CRLF, BOM, quotes, backslashes and tabs; every `edit_file` recovery path; `.mcp.json` and config-file protection through every server, including Windows aliases; relative paths with the server started from another folder; output limits at 4 000 and 40 000 characters; forbidden paths in git output; command injection attempts; config validation errors; and starting all servers over stdio from `.mcp.json`. See "What should still be done" for turning these into a real test suite.

## Accepted limitations

- `commands-mcp` can reach any file through the code it runs (see the security model).
- In `git-diff-mcp`, a file moved into a forbidden folder is still visible in the history of its old, allowed path, and commit messages are not filtered.
- The `.gitignore` matcher is simplified, and the JS/TS parser is regex-based: declarations that do not start at the beginning of a line, or a `/` in a regex literal mistaken for a string or comment, can be missed.
- `ignored_dirs` names are matched case-sensitively.
- Command output is decoded as UTF-8; programs writing in the Windows code page may show `�` for characters such as `ä`.

## What should still be done

In recommended order. Items 1–3 were found by measuring the running tools on 2026-09-29.

1. **Show all files in the file map.** The tree only lists the map's file types (`.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.md`, `.json`, `.css`, `.scss`, `.html`). In a typical Python project it hid `pyproject.toml`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `Makefile`, `setup.cfg` and `schema.sql`, so the AI does not know they exist. List every non-ignored file in the tree and keep the type filter only for the detailed descriptions.
2. **Fall back to literal search.** `search_text_in_files("foo(")` fails as an invalid regex; small models often search for `functionName(`. When the query is not a valid regex, search for it literally and say so in the response.
3. **Reduce the tool definitions.** They cost about 5 400 tokens (21 400 characters) with every request, two thirds of an 8k context. The largest are `edit_file` (2 300 characters), `read_file_with_metadata` (1 800), `search_text_in_files` (1 800) and `write_file` (1 300). Tighten the longest descriptions and merge tools (26 → 22):
   - `read_context_file` + `read_all_context_files` → `read_context_file(filename=None)`
   - `write_context_file` + `append_to_context_file` → `write_context_file(..., append=False)`
   - `get_file_diff` + `get_all_changes_diff` → `get_diff(mode, path=None)` for a file or a folder
   - remove `create_directory` (`create_file` creates missing folders)
   - keep `list_directory` (sizes and dates add information).
4. **Add a web page reader**, e.g. `read_web_page(url)` returning plain text or Markdown within the output limit. Web search alone only gives snippets.
5. **Add a repo-wide commit log**, e.g. `get_recent_commits(limit, path=None)` and a way to show one commit. Git history is currently only available per file.
6. **Allow a working folder per command**: an optional `cwd` key in the command registry, relative to `allowed_dir`, for monorepos.
7. **Add `find_definition(name)`** using the map's Python and JS/TS parsers, returning where a function or class is defined. Regex search finds every use of a name.
8. **Add an automated test suite** (e.g. `tests/` with pytest) based on the verification scripts described above, so refactors can be checked quickly.
9. **Remove the stray `¨`** at the end of practice 1 in `_instructions/app-instructions.md`.

Also worth considering: allowing more than one argument in command templates.

## What is deliberately left out

- **Git write tools** (stage, commit, branch): a person reviewing and committing the AI's changes is a sensible safety line.
- **Background processes** (starting a dev server and reading its logs): useful for web apps, but a large addition with its own safety questions. Reconsider if web app development with local models becomes a goal.
- **Environment variable fallback** for the old settings: it would reintroduce the risk of servers seeing different settings.
- **Shell features in command templates** (pipes, `&&`, redirection): no safe way to allow them with arguments on Windows. Such logic goes into a script that a template calls.
- **Operating system read-only flags or a `readonly/` subfolder** for read-only files: platform-dependent and easy to lose; `read_only_files` in the config is used instead.
- **An opt-out for `.mcp.json` protection**: not needed, as the user edits the file directly.

## Notes for working on this repository

- Environment: Windows, Python 3.14, `mcp` 2.1.1 (`MCPServer`, `Image` from `mcp.server.mcpserver`), ollmcp 0.35, git 2.54.
- The servers need `MCP_TOOLS_CONFIG` set to start, also when a script imports them for testing. When loading several servers in one test process, remove `mcp_common` from `sys.modules` before each load so that each gets a fresh config.
- ollmcp passes tool responses to the model as text; `ImageContent` is forwarded only to vision-capable models.
