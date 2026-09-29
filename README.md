# ollmcp-generic-tools

Generic custom MCP servers for Ollmcp for Python and JS/TS development purposes.

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

### Configuration

To use these MCP servers, you need to configure a `.mcp.json` file in your project root. This file tells `ollmcp` which servers to start and how to access them.

**Important Notes on File Paths:**
- The MCP servers themselves (the code) do **not** need to be located within your project folder.
- However, the file paths specified in `.mcp.json` must correctly point to the location of the server scripts and any data directories used by the tools.
- The special directories used by the tools (`_context`, `_instructions`, and `_commands`) can either be located inside your project folder or anywhere else on your system, as long as you provide the correct absolute or relative paths in your configuration.

### Environment Variables

Most of the MCP servers use environment variables for configuration. These should be defined in your `.mcp.json` under the `env` key for each server.

#### Shared by generate-map-mcp, search-tool-mcp, safe-filesystem-mcp and git-diff-mcp
- `ALLOWED_DIR`: The project directory the tools can access. Relative paths given to the tools are resolved against it, so a path returned by one tool (e.g. `src/app.py` from a search) works as-is in the others. (Defaults to current working directory)
- `FORBIDDEN_PATHS`: List of folders and/or files the tools must never access, even within `ALLOWED_DIR`. See [Forbidden paths](#forbidden-paths). (Optional)

Use the same values for all four servers, so that the files the AI can find with the map and search tools are exactly the files it can read, edit and inspect with git. To focus the AI on part of the project, such as `src`, it can limit maps and searches with their `path` argument instead.

#### commands-mcp
- `COMMANDS_CONFIG`: Path to the JSON configuration file containing the command registry.

#### context-record-mcp
- `CONTEXT_FOLDER_PATH`: Path to the folder containing the context Markdown files.

#### generate-map-mcp
- `ALLOWED_DIR`, `FORBIDDEN_PATHS`: See [above](#shared-by-generate-map-mcp-search-tool-mcp-safe-filesystem-mcp-and-git-diff-mcp).
- `GITIGNORE_PATH`: Path to the `.gitignore` file to use for excluding files. (Optional, defaults to `ALLOWED_DIR/.gitignore`)

#### git-diff-mcp
- `ALLOWED_DIR`, `FORBIDDEN_PATHS`: See [above](#shared-by-generate-map-mcp-search-tool-mcp-safe-filesystem-mcp-and-git-diff-mcp).

#### instructions-mcp
- `PROJECT_INSTRUCTIONS_FILE`: Path to the project-specific instructions Markdown file.

#### safe-filesystem-mcp
- `ALLOWED_DIR`, `FORBIDDEN_PATHS`: See [above](#shared-by-generate-map-mcp-search-tool-mcp-safe-filesystem-mcp-and-git-diff-mcp).

#### search-tool-mcp
- `ALLOWED_DIR`, `FORBIDDEN_PATHS`: See [above](#shared-by-generate-map-mcp-search-tool-mcp-safe-filesystem-mcp-and-git-diff-mcp).
- `GITIGNORE_PATH`: Path to the `.gitignore` file to use for excluding files. (Optional, defaults to `ALLOWED_DIR/.gitignore`)

### Running

Once configured, start `ollmcp` in your project root directory:

```bash
ollmcp
```

## MCP Servers

### commands-mcp

This is a tool for allowing AI Agent access to very specific console commands and giving it context for when it could or should use those commands.

- **list_available_commands** Returns a list of all allowed console commands and their descriptions.
- **run_predefined_command** Executes a specific console command from the allowed registry.

Commands are defined in the `COMMANDS_CONFIG` JSON file:

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

To keep the AI from running anything other than the defined commands:
- Commands run without a shell, so shell features such as pipes (`|`), chaining (`&&`), redirection (`>`) and shell built-ins (`echo`, `dir`, `cd`) are not available in templates. Put such logic in a script and call the script from the template.
- Arguments that start with `-`, or contain line breaks, are rejected so the AI cannot add unintended options.
- On Windows, if the program is a batch file (`.bat` / `.cmd`, e.g. `npm`), arguments containing `& | < > ^ % ! " ( )` are rejected.
- Commands cannot read input, so interactive prompts end immediately instead of hanging.
- A command succeeds only if it exits with code 0, and output longer than 20 000 characters per stream is truncated in the middle.

### context-record-mcp

This tool manages a context folder containing Markdown files, allowing for listing, reading, writing, appending, and removing context files. File names must be plain `.md` names such as `notes.md`: names with folders (`../x.md`, `sub/x.md`) or other extensions are rejected, so the tools cannot reach files outside the context folder.

- **list_context_files** Lists all Markdown files in the context folder.
- **read_context_file** Reads the content of a specific Markdown file in the context folder.
- **write_context_file** Creates a new Markdown file or overwrites an existing one in the context folder.
- **append_to_context_file** Appends content to a specific Markdown file in the context folder.
- **read_all_context_files** Reads the content of all Markdown files in the context folder at once.
- **remove_context_file** Removes a specific Markdown file from the context folder.

### generate-map-mcp

Generates a comprehensive structure map and summary of a codebase, supporting Python (via AST) and JS/TS (via regex).

- **generate_codebase_map** Generates a structuremap of the codebase, optionally limited to a subfolder (`path`). Lists for each file:
  - Python: functions, classes and methods, docstring summaries, and calls to the project's own functions and classes.
  - JS/TS: local imports, functions, React components and hooks, classes and methods, interfaces, types, enums, exported constants, default and named exports, re-exports and JSDoc summaries. Code in comments is ignored.
- **generate_file_map** Generates the file tree map of the codebase without detailed summaries, optionally limited to a subfolder (`path`).
- **get_codebase_map_config** Returns the input directory, ignored directories, forbidden paths, `.gitignore` patterns and included file types, to explain why a file may be missing from the maps.

### git-diff-mcp

Provides tools to inspect git history and differences for files within a repository.

- **get_file_diff** Retrieves the differences for a specified file.
- **get_all_changes_diff** Retrieves the differences for all changed files at once, with a list of changed and new untracked files. Useful for reviewing all changes before committing.
- **get_file_history** Retrieves the commit history and associated diffs for a specified file.
- **get_git_status** Returns the results of `git status`.
- **is_git_repository** Checks if the allowed directory or a specified directory is a Git repository.
- **get_git_config** Returns the allowed directory, its repository root and forbidden paths, to explain why a path may be denied or missing from the output.

### instructions-mcp

Retrieves project-specific instructions from a designated Markdown file.

- **get_project_instructions** Returns the content of the project-specific instructions markdown file.

### safe-filesystem-mcp

Provides safe and robust file system operations, including metadata retrieval and atomic writes.

- **write_file** Safely replaces the whole content of an existing file with hash validation. Rejects placeholder comments such as `// ... existing code ...` and warns if the file shrinks to less than half.
- **edit_file** Changes part of an existing file by replacing an exact piece of text, with hash validation. Keeps the file's line endings and BOM, and shows the changed lines and the new SHA-256 after the edit.
- **create_file** Creates a new file with the provided content.
- **read_file_with_metadata** Reads a text file and returns its exact content along with metadata (sha256, line count, encoding, etc.). Can read a range of lines (`start_line`, `end_line`) with optional line numbers, and reads long files in parts of up to 1000 lines.

The file reading and editing tools are designed to let local AI models read and edit files accurately:
- File content is returned as plain text between `----- BEGIN CONTENT -----` and `----- END CONTENT -----` markers, not inside JSON, so quotes, backslashes and line breaks appear exactly as in the file and can be copied into `edit_file` as-is.
- `edit_file` tolerates common copying mistakes: line number prefixes copied from `read_file_with_metadata` are removed, and trailing whitespace does not need to match. If the text is not found, the error shows the closest matching lines to copy.
- Placeholder comments such as `// ... existing code ...` are rejected, as they would otherwise replace real code.
- **read_image** Reads a PNG, JPEG, GIF or WebP image (up to 10 MB) and returns it as an image the AI can see, along with its metadata. Requires a vision-capable model; with other models, ollmcp skips the image and shows a warning.
- **get_file_stats** Retrieves metadata about a file without reading its content.
- **list_directory** Lists all files and directories within the specified path.
- **create_directory** Creates a new directory at the specified path.
- **move_file** Moves or renames a file or directory.
- **delete_file** Deletes a file or a directory.
- **get_filesystem_config** Returns the allowed directory and forbidden paths, to explain why a path may be denied or not found.

### search-tool-mcp

Performs text or regex searches across files in a specified directory.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase. Can be limited to a folder or file (`path`) and to matching file names (`file_pattern`, e.g. `*.py`), and can show lines around each match (`context_lines`). Very long lines are shortened around the match.
- **search_files_by_pattern** Searches for files and directories that match a glob-style pattern, such as `src/**/*.test.ts`.
- **get_search_config** Returns the input directory, ignored directories, forbidden paths, `.gitignore` patterns and search limits, to explain why a file or match may be missing from the searches.

## Details and patterns

### Forbidden paths

`generate-map-mcp`, `search-tool-mcp`, `safe-filesystem-mcp` and `git-diff-mcp` accept a `FORBIDDEN_PATHS` list of sub-folders and individual files that are excluded from all tool operations. A forbidden folder also forbids everything inside it. Leave the value empty (or omit it) to forbid nothing.

- Relative paths are resolved against `ALLOWED_DIR`. Absolute paths are also accepted.
- Separate paths with commas. Whitespace around each path is ignored:
  ```json
  "FORBIDDEN_PATHS": "folder/sub_folder/code-file.py, another_folder"
  "FORBIDDEN_PATHS": ""
  ```
- In `safe-filesystem-mcp`, forbidden paths are hidden from `list_directory`, and deleting or moving a folder that contains a forbidden path is denied.
- In `git-diff-mcp`, forbidden paths are denied and left out of status and diff output. Otherwise a file's history would reveal its contents.
- There is no need to add the [ignored directories](#ignored-directories) to `FORBIDDEN_PATHS` for `generate-map-mcp` or `search-tool-mcp`, as they are already skipped by default.

Limitations:
- `commands-mcp` does not use `FORBIDDEN_PATHS`. Do not register commands that can print arbitrary files (e.g., `git show` or `cat {arg}`) if some files must stay hidden. See also [Protected files](#protected-files).
- In `git-diff-mcp`, a file that was moved into a forbidden folder can still be seen in the history of its old, allowed path. Commit messages are not filtered either.

### Protected files

Files named `.mcp.json` contain the MCP server configuration: which servers run, their allowed directories, forbidden paths and command registries. If the AI could change them, it could remove its own restrictions, which would take effect the next time the servers start. Therefore `.mcp.json` files are always protected, at any depth and regardless of `FORBIDDEN_PATHS` (defined as `PROTECTED_FILE_NAMES` in the scripts):

- `safe-filesystem-mcp` denies reading, writing, editing, moving and deleting them, and creating a file or renaming a file to that name. Folders containing one cannot be moved or deleted either.
- `generate-map-mcp` and `search-tool-mcp` leave them out of maps and search results.
- `git-diff-mcp` leaves them out of diffs, status and history.
- `context-record-mcp` only accepts plain `.md` file names directly inside its context folder, so it cannot reach them either.

The protection also covers other spellings of the name that Windows treats as the same file, such as a different letter case, a trailing dot or space, a `::$DATA` suffix or an 8.3 short name.

**The tools cannot fully protect files from `commands-mcp`.** Commands such as tests, scripts or `npm run` execute project code, which the AI can write and which can read or change any file. For a real project, keep the MCP configuration outside the project folder, where no tool can reach it, and start `ollmcp` with its path:

```bash
ollmcp --servers-json C:\path\outside\project\.mcp.json
```

For the same reason, keep the `mcp-servers` scripts and the `COMMANDS_CONFIG` registry outside the project folder, or add them to `FORBIDDEN_PATHS`.

### Ignored directories

`generate-map-mcp` and `search-tool-mcp` always skip folders with the following names, at any depth within `ALLOWED_DIR` (defined as `IGNORED_DIRS` in the scripts):

`node_modules`, `.git`, `__pycache__`, `dist`, `build`, `.next`, `.venv`, `venv`, `env`, `.pytest_cache`, `.idea`, `.vscode`, `target`, `out`, `.mypy_cache`, `.ruff_cache`

These folders are skipped without being entered, so even a large `node_modules` does not slow the tools down.

Both tools additionally skip files matched by the `.gitignore` file (see `GITIGNORE_PATH`). `search-tool-mcp` also skips binary files.

`safe-filesystem-mcp` has no ignored directories, because it only accesses the paths the AI explicitly asks for. To block it from folders such as `.git` or `.venv`, add them to its `FORBIDDEN_PATHS`.

`git-diff-mcp` has no ignored directories either. Git's own `.gitignore` rules decide which untracked files it lists.