# ollmcp-generic-tools

Generic custom MCP servers for Ollmcp for Python and JS/TS development purposes.

For the design principles behind the servers and the planned improvements, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Setup

### Prerequisites

1.  **Install Ollama**:
    Download and install Ollama from [ollama.com](https://ollama.com/). Once installed, you can run a model (for example gemma4:26b), using:
    ```bash
    ollama run gemma4:26b
    ```

2.  **Install ollmcp**:
    Install `ollmcp` via pip:
    ```bash
    pip install ollmcp
    ```

3.  **Install the packages the servers use**:
    The servers are developed and tested with Python 3.14 and need at least Python 3.10. They use the `mcp` package, and `web-search-mcp` also uses `ollama`:
    ```bash
    pip install mcp ollama
    ```

### Configuration

The tools are configured with two files:
- `.mcp.json` in your project root tells `ollmcp` which servers to start. Each server only gets the path of the tools config file, in the `MCP_TOOLS_CONFIG` environment variable.
- The tools config file, e.g. `tools-config.json`, holds all settings. Every server reads the same file, so they all see exactly the same project directory, forbidden paths and limits.

```json
{
    "mcpServers": {
        "filesystem": {
            "command": "python",
            "args": [".\\mcp-servers\\safe-filesystem-mcp.py"],
            "env": {
                "MCP_TOOLS_CONFIG": ".\\tools-config.json"
            }
        }
    }
}
```

Add every server the same way (see this repository's [`.mcp.json`](.mcp.json)). The server scripts share code in `mcp-servers/mcp_common.py` (configuration and access checks) and `mcp-servers/mcp_outline.py` (the outline parsers), so keep all the scripts in the same folder. The scripts, the tools config file and the special directories (`_context`, `_instructions` and `_commands`) do **not** need to be located within your project folder.

### Tools config file

```json
{
    "allowed_dir": ".",
    "forbidden_paths": ["secrets", "config/keys.json"],
    "ignored_dirs": null,
    "gitignore_path": null,
    "max_output_chars": 40000,
    "commands_config": "_commands/app-commands.json",
    "context_folder": "_context",
    "read_only_files": ["_instructions/app-instructions.md"],
    "ollama_api_key": null
}
```

All settings except `allowed_dir` are optional. An optional setting that is left out or set to `null` uses its default, so `null` can be used to show that a setting exists without using it. Note that an empty list is not the same as `null`: `"ignored_dirs": []` skips no folders at all, while `"ignored_dirs": null` skips the default folders.

Relative paths are resolved against the folder of the config file, except `forbidden_paths`, which are resolved against `allowed_dir`. A server refuses to start if the file is missing, is not valid JSON, lacks `allowed_dir`, points `allowed_dir` to a folder that does not exist or contains an unknown setting, so a typo cannot silently weaken the configuration. The config file itself is always [protected](#protected-files).

| Setting | Description |
|---|---|
| `allowed_dir` | The project directory the tools can access. Relative paths given to the tools are resolved against it, so a path returned by one tool (e.g. `src/app.py` from a search) works as-is in the others. To focus the AI on part of the project, such as `src`, it can limit outlines and searches with their `path` argument. (Required) |
| `forbidden_paths` | List of folders and/or files the tools must never access, even within `allowed_dir`. See [Forbidden paths](#forbidden-paths). (Optional) |
| `max_output_chars` | The largest size of a tool response in characters, about 4 characters per token. Lower it for models with small context windows, e.g. `12000` for an 8k-token context. See [Output limits](#output-limits). (Optional, defaults to `40000`, at least `2000`) |
| `gitignore_path` | The `.gitignore` file used by the outline and search tools. (Optional, defaults to `.gitignore` in `allowed_dir`) |
| `commands_config` | The command registry of `commands-mcp`. See [commands-mcp](#commands-mcp). (Optional) |
| `context_folder` | The folder of the context Markdown files of `context-record-mcp`. (Optional) |
| `read_only_files` | List of files maintained by you, such as project instructions, that the AI can read with `context-record-mcp` but not change or remove. They can be located anywhere, and are read by their file name. (Optional) |
| `ollama_api_key` | The Ollama API key used by `web-search-mcp`. Create one at [ollama.com/settings/keys](https://ollama.com/settings/keys). The key is never shown to the AI, as the config file is [protected](#protected-files). If the config file is committed to git, do not commit the key. (Optional, without it the web search tools only return a `not_configured` error) |
| `ignored_dirs` | List of folder names the outline and search tools skip at any depth. Replaces the defaults completely. See [Ignored directories](#ignored-directories). (Optional, defaults to `node_modules`, `.git`, `build` and other common build and cache folders) |

The AI can see the effective configuration with the `get_config` tool of `safe-filesystem-mcp`.

### Running

Once configured, start `ollmcp` in your project root directory:

```bash
ollmcp
```

### Testing

The servers have an automated test suite in `tests/`. Run it from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

See [DEVELOPMENT.md](DEVELOPMENT.md#how-changes-have-been-verified) for how the tests are organized.

## MCP Servers

### commands-mcp

This is a tool for allowing AI Agent access to very specific console commands and giving it context for when it could or should use those commands.

- **list_available_commands** Returns a list of all allowed console commands and their descriptions.
- **run_predefined_command** Executes a specific console command from the allowed registry.

Commands are defined in the JSON file set as `commands_config` in the tools config:

```json
{
    "add_package": {
        "template": "poetry add {arg}",
        "description": "Adds a runtime dependency to the project. Argument: the package name (e.g., 'requests').",
        "timeout": 300
    }
}
```

See [`_commands/app-commands.json`](_commands/app-commands.json) for a fuller example covering dependencies, tests, linting, type checking and npm scripts.

- `template`: A single program and its arguments. `{arg}` is replaced with the argument given by the AI, which is always passed as one argument.
- `description`: Tells the AI when to use the command.
- `timeout`: Seconds before the command is stopped. (Optional, defaults to 120)

Commands run in `allowed_dir`, so commands such as `pytest` or `ruff check .` work on the project regardless of where `ollmcp` was started. The server refuses to start if the registry file is missing or not valid JSON, or if a command lacks its `template` or `description` or has an invalid `timeout`.

To keep the AI from running anything other than the defined commands:
- Commands run without a shell, so shell features such as pipes (`|`), chaining (`&&`), redirection (`>`) and shell built-ins (`echo`, `dir`, `cd`) are not available in templates. Put such logic in a script and call the script from the template.
- Arguments that start with `-`, or contain line breaks, are rejected so the AI cannot add unintended options.
- On Windows, if the program is a batch file (`.bat` / `.cmd`, e.g. `npm`), arguments containing `& | < > ^ % ! " ( )` are rejected.
- Commands cannot read input, so interactive prompts end immediately instead of hanging.
- A command succeeds only if it exits with code 0, and long output is cut from the middle (see [Output limits](#output-limits)).

### context-record-mcp

This tool manages a context folder containing Markdown files, allowing for listing, reading, writing, appending, and removing context files. File names must be plain `.md` names such as `notes.md`: names with folders (`../x.md`, `sub/x.md`) or other extensions are rejected, so the tools cannot reach files outside the context folder.

It also gives the AI your project instructions: list them in `read_only_files`, and they are listed and read first, marked `(read-only)`, while writing, appending to or removing them is refused. A context file with the same name as a read-only file is hidden and cannot be created, so it cannot take the instructions' place.

- **list_context_files** Lists the read-only files and all Markdown files in the context folder, with their line counts and headings with line spans.
- **read_context_file** Reads a Markdown file in the context folder, or a read-only file: the whole file, the section of one heading (`section`), or a range of lines (`start_line`, `end_line`). Long files are read in parts.
- **write_context_file** Creates a new Markdown file or overwrites an existing one in the context folder. The response shows the new headings with their line spans, and warns if headings of the old content are gone.
- **append_to_context_file** Appends content to a specific Markdown file in the context folder, and shows its headings with their line spans.
- **read_all_context_files** Reads the read-only files and all Markdown files in the context folder at once. Context files that do not fit are shown as their headings with line spans, to be read by section with `read_context_file`. Meant to be called at the start of a session.
- **remove_context_file** Removes a specific Markdown file from the context folder.

Read-only only applies to `context-record-mcp`. If the instructions file is inside the project, `safe-filesystem-mcp` can still change it: add its folder (e.g. `_instructions`) to `forbidden_paths` to prevent that. `context-record-mcp` does not use `forbidden_paths`, so it can still read the file.

### generate-map-mcp

Gives the AI an outline of the project, so that it can find the code it needs and then read only that part, instead of reading whole files or a map that is cut off at the output limit. The parsers are in `mcp_outline.py`: Python via `ast`, JS/TS via regular expressions (code in comments and strings is ignored), and Markdown by its headings.

- **get_outline** Returns an outline with the line span (`first-last`) of each section.
  - For a folder (by default the whole allowed directory): every file that is not ignored, with its line count, and for Python, JS/TS and Markdown files their sections: classes and methods, functions, React components and hooks, interfaces, types, enums, exported constants, blocks of top-level statements, and headings, with docstring and JSDoc summaries.
  - For a file (`path='src/app.py'`): all its sections with their summaries, and for Python the calls to the project's own functions and classes, and for JS/TS the local imports, exports and re-exports.

```
src/app.py (120 lines): Application entry point.
  1-16 docstring, imports, assignments: LIMIT, TIMEOUT
  18-60 class App: Main application
    20-34 run(): Starts the server
  63-120 main(): Entry point
docs/guide.md (80 lines)
  1-80 # Guide
    13-40 ## Install
```

The AI then reads a section by its name, e.g. `read_file_with_metadata(path, section="App.run")`, or by its line span. An outline that does not fit in `max_output_chars` is not cut short: its detail is reduced step by step until it fits, first leaving out the summaries, then showing only top-level sections, then only the files with their section counts, and finally only the files in the folder and its subfolders with their file counts. The response says what was left out and suggests subfolders to outline with `path`.

### git-diff-mcp

Provides tools to inspect git history and differences for files within a repository.

- **get_file_diff** Retrieves the differences for a specified file.
- **get_all_changes_diff** Retrieves the differences for all changed files at once, with a list of changed and new untracked files. Useful for reviewing all changes before committing.
- **get_file_history** Retrieves the commit history and associated diffs for a specified file.
- **get_git_status** Returns the results of `git status`. Also tells whether a folder is in a git repository: if not, the error is `not_a_repository`.

### safe-filesystem-mcp

Provides safe and robust file system operations, including metadata retrieval and atomic writes.

- **write_file** Safely replaces the whole content of an existing file with hash validation. Rejects placeholder comments such as `// ... existing code ...` and warns if the file shrinks to less than half.
- **edit_file** Changes part of an existing file by replacing an exact piece of text, with hash validation. Keeps the file's line endings and BOM, and shows the changed lines, the new SHA-256 and the file's outline after the edit.
- **create_file** Creates a new file with the provided content, and shows its outline.
- **read_file_with_metadata** Reads a text file and returns its exact content along with metadata (sha256, line count, encoding, etc.). Can read a class, function or Markdown heading by its name (`section`, e.g. `App.run` or `Install`) or a range of lines (`start_line`, `end_line`) with optional line numbers, and reads long files in parts of up to 1000 lines.
- **read_image** Reads a PNG, JPEG, GIF or WebP image (up to 10 MB) and returns it as an image the AI can see, along with its metadata. Requires a vision-capable model; with other models, ollmcp skips the image and shows a warning.
- **list_directory** Lists all files and directories within the specified path.
- **create_directory** Creates a new directory at the specified path.
- **move_file** Moves or renames a file or directory.
- **delete_file** Deletes a file or a directory.
- **get_config** Returns the configuration shared by all servers (allowed directory, forbidden and protected paths, output limit) and what each server sees: the files the outline and searches skip, the git repository, the command registry and the context files. Helps the AI find out why a file is missing or a path is denied.

The file reading and editing tools are designed to let local AI models read and edit files accurately:
- File content is returned as plain text between `----- BEGIN CONTENT -----` and `----- END CONTENT -----` markers, not inside JSON, so quotes, backslashes and line breaks appear exactly as in the file and can be copied into `edit_file` as-is.
- `edit_file` tolerates common copying mistakes: line number prefixes copied from `read_file_with_metadata` are removed, and trailing whitespace does not need to match. If the text is not found, the error shows the closest matching lines to copy.
- Placeholder comments such as `// ... existing code ...` are rejected, as they would otherwise replace real code.
- After every write to a Python, JS/TS or Markdown file (`edit_file`, `write_file`, `create_file`), the response shows the file's outline with line spans, lists the sections that were added, and warns about sections that are no longer in the file and about a Python file that no longer parses. An accidental overwrite or deletion is noticed right away, instead of relying on the AI to spot a missing entry.

### search-tool-mcp

Performs text or regex searches across files in a specified directory.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase. A query that is not a valid regex, such as `foo(`, is searched for as literal text, and the response says so. Can be limited to a folder or file (`path`) and to matching file names (`file_pattern`, e.g. `*.py`), and can show lines around each match (`context_lines`). Very long lines are shortened around the match.
- **search_files_by_pattern** Searches for files and directories that match a glob-style pattern, such as `src/**/*.test.ts`.

### web-search-mcp

Searches the web and reads web pages using Ollama's hosted [web search API](https://docs.ollama.com/capabilities/web-search). Requires `ollama_api_key` in the [tools config file](#tools-config-file) and the `ollama` Python package (`pip install ollama`). The queries and URLs are sent to ollama.com.

- **web_search** Searches the web and returns the title, URL and content of the best matching pages (`max_results`, 1–10, default 3).
- **web_fetch** Fetches a web page and returns its title, links and text content. The content is shown as-is between `----- BEGIN CONTENT -----` and `----- END CONTENT -----` markers, so code copied from a page is not JSON-escaped. Long pages are read in parts with `start_char`.

Web pages can contain text written to mislead the AI (prompt injection). Review the AI's changes when it has read web content.

## Details and patterns

### Forbidden paths

The `forbidden_paths` setting of the [tools config file](#tools-config-file) lists sub-folders and individual files that `generate-map-mcp`, `search-tool-mcp`, `safe-filesystem-mcp` and `git-diff-mcp` exclude from all tool operations. A forbidden folder also forbids everything inside it. Leave the list empty (or omit it) to forbid nothing.

- Relative paths are resolved against `allowed_dir`. Absolute paths are also accepted:
  ```json
  "forbidden_paths": ["folder/sub_folder/code-file.py", "another_folder"]
  ```
- In `safe-filesystem-mcp`, forbidden paths are hidden from `list_directory`, and deleting or moving a folder that contains a forbidden path is denied.
- In `git-diff-mcp`, forbidden paths are denied and left out of status and diff output. Otherwise a file's history would reveal its contents.
- There is no need to add the [ignored directories](#ignored-directories) to `forbidden_paths` for `generate-map-mcp` or `search-tool-mcp`, as they are already skipped by default.

Limitations:
- `commands-mcp` does not use `forbidden_paths`. Do not register commands that can print arbitrary files (e.g., `git show` or `cat {arg}`) if some files must stay hidden. See also [Protected files](#protected-files).
- In `git-diff-mcp`, a file that was moved into a forbidden folder can still be seen in the history of its old, allowed path. Commit messages are not filtered either.

### Protected files

Files named `.mcp.json` contain the MCP server configuration, which servers run and which tools config file they read, and the [tools config file](#tools-config-file) contains the allowed directory, forbidden paths and command registry. If the AI could change them, it could remove its own restrictions, which would take effect the next time the servers start. Therefore `.mcp.json` files, at any depth, and the tools config file in use are always protected, regardless of `forbidden_paths` (defined as `PROTECTED_FILE_NAMES` and `CONFIG_PATH` in `mcp_common.py`):

- `safe-filesystem-mcp` denies reading, writing, editing, moving and deleting them, and creating a file or renaming a file to that name. Folders containing one cannot be moved or deleted either.
- `generate-map-mcp` and `search-tool-mcp` leave them out of outlines and search results.
- `git-diff-mcp` leaves them out of diffs, status and history.
- `context-record-mcp` only accepts plain `.md` file names directly inside its context folder, so it cannot reach them either.

The protection also covers other spellings of the name that Windows treats as the same file, such as a different letter case, a trailing dot or space, a `::$DATA` suffix or an 8.3 short name.

**The tools cannot fully protect files from `commands-mcp`.** Commands such as tests, scripts or `npm run` execute project code, which the AI can write and which can read or change any file. For a real project, keep the MCP configuration and the tools config file outside the project folder, where no tool can reach them, and start `ollmcp` with its path:

```bash
ollmcp --servers-json C:\path\outside\project\.mcp.json
```

When the tools config file is outside the project, set `allowed_dir` in it to the project folder, as an absolute path or relative to the config file.

For the same reason, keep the `mcp-servers` scripts and the `commands_config` registry outside the project folder, or add them to `forbidden_paths`.

### Ignored directories

`generate-map-mcp` and `search-tool-mcp` skip folders with the names listed in the `ignored_dirs` setting of the [tools config file](#tools-config-file), at any depth within `allowed_dir`. If the setting is not given, these defaults are used (defined as `DEFAULT_IGNORED_DIRS` in `mcp_common.py`):

```json
"ignored_dirs": ["node_modules", ".git", "__pycache__", "dist", "build", ".next", ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode", "target", "out", ".mypy_cache", ".ruff_cache"]
```

A list in the config file replaces the defaults completely: copy the list above and add or remove names to suit the project. For example, remove `build` or `env` if the project has real source code in folders with those names, or add `coverage` or `.gradle`. An empty list skips no folders by name.

- The entries are folder names, not paths: `build` skips every folder named `build`. To exclude one specific folder, such as `docs/build`, add it to `forbidden_paths` instead.
- These folders are skipped without being entered, so even a large `node_modules` does not slow the tools down. Removing `node_modules` from the list makes outlines and searches of JS/TS projects much slower.
- Ignored folders are not a security measure: `safe-filesystem-mcp` and `git-diff-mcp` can still access them. Use `forbidden_paths` for files the AI must not access.

Both tools additionally skip files matched by the `.gitignore` file (see `gitignore_path`). `search-tool-mcp` also skips binary files.

`safe-filesystem-mcp` has no ignored directories, because it only accesses the paths the AI explicitly asks for. To block it from folders such as `.git` or `.venv`, add them to its `forbidden_paths`.

`git-diff-mcp` has no ignored directories either. Git's own `.gitignore` rules decide which untracked files it lists.

### Output limits

Local models have small context windows, so every tool response is limited to `max_output_chars` characters (40 000 by default, about 10 000 tokens). When a limit is applied, the response contains a message starting with `Output limited:` that says what was left out and exactly how to get the rest, for example which `start_line` to read next or which folder to outline with `path`. In JSON responses, the message is in the `output_limited` field.

| Tool | Limit | How the rest can be seen |
|---|---|---|
| `read_file_with_metadata` | 1000 lines or `max_output_chars` per read; lines over 2000 characters are shortened; files over 50 MB are not read | Next part with `start_line`; long lines can still be edited using a unique part of the shown text |
| `edit_file` | Shows up to 40 changed lines after the edit | `read_file_with_metadata` from the given line |
| `edit_file`, `write_file`, `create_file` | The outline after a write is limited to 2500 characters, or `max_output_chars / 8` if smaller: then only top-level sections are shown, or the list is cut short | `get_outline` for the file |
| `list_directory` | Up to 250 entries, fewer for small budgets; folders are listed first | `search_files_by_pattern` with a suggested pattern |
| `get_outline` | `max_output_chars`: the detail is reduced step by step instead of cutting the outline; files over 1 MB are not outlined; folders with over 1000 files are listed by subfolder only | Suggested subfolders to outline with `path`, with their file counts, or one file with `path` |
| `search_text_in_files` | 100 matches or `max_output_chars`; lines over 300 characters are shortened | A suggested `path` with the most matches, `file_pattern`, fewer `context_lines` |
| `search_files_by_pattern` | 100 matches | A more specific pattern |
| `get_file_diff`, `get_file_history`, `get_git_status` | `max_output_chars` | Read the file instead, a smaller `limit`, or a `path` |
| `get_all_changes_diff` | `max_output_chars`; up to 200 changed and 200 untracked files are listed | `get_file_diff` for the files listed in `changed_files`, or a `path` |
| `run_predefined_command` | `max_output_chars`, split between stdout and stderr; the middle of long output is cut, as errors are usually at the end | A narrower command, such as tests for a single file |
| `read_all_context_files` | `max_output_chars`; read-only files always come first, then context files that do not fit are shown as their headings with line spans, or left out | `read_context_file` with `section` or `start_line` for the listed files |
| `read_context_file` | `max_output_chars` per read | Next part with `start_line`; keep context and read-only files short |
| `list_context_files` | `max_output_chars`: only top-level headings, or only the file names, are shown | `read_context_file` |
| `web_search` | `max_output_chars`, shared equally by the results | `web_fetch` for a whole page |
| `web_fetch` | `max_output_chars` per part; up to 50 links, listed in the first part only | Next part with `start_char` |

The example [`_instructions/app-instructions.md`](_instructions/app-instructions.md) tells the AI to follow these messages instead of repeating the same call, and never to assume that a limited response is the whole result. Include a similar instruction in your own project instructions.