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

#### commands-mcp
- `COMMANDS_CONFIG`: Path to the JSON configuration file containing the command registry.

#### context-record-mcp
- `CONTEXT_FOLDER_PATH`: Path to the folder containing the context Markdown files.

#### generate-map-mcp
- `INPUT_DIR`: The directory to scan for the codebase map. (Defaults to current working directory)
- `GITIGNORE_PATH`: Path to the `.gitignore` file to use for excluding files. (Optional)
- `FORBIDDEN_PATHS`: List of folders and/or files the tool must never access, even within `INPUT_DIR`. See [Forbidden paths](#forbidden-paths). (Optional)

#### git-diff-mcp
- `ALLOWED_DIR`: The directory within which git history and changes can be inspected. Use the same value as for `safe-filesystem-mcp`. (Defaults to current working directory)
- `FORBIDDEN_PATHS`: List of folders and/or files the tool must never show, even within `ALLOWED_DIR`. Use the same value as for `safe-filesystem-mcp`. See [Forbidden paths](#forbidden-paths). (Optional)

#### instructions-mcp
- `PROJECT_INSTRUCTIONS_FILE`: Path to the project-specific instructions Markdown file.

#### safe-filesystem-mcp
- `ALLOWED_DIR`: The directory within which file operations are permitted. (Defaults to current working directory)
- `FORBIDDEN_PATHS`: List of folders and/or files the tool must never access, even within `ALLOWED_DIR`. See [Forbidden paths](#forbidden-paths). (Optional)

#### search-tool-mcp
- `INPUT_DIR`: The directory to perform searches in. (Defaults to current working directory)
- `FORBIDDEN_PATHS`: List of folders and/or files the tool must never access, even within `INPUT_DIR`. See [Forbidden paths](#forbidden-paths). (Optional)

#### Forbidden paths

`generate-map-mcp`, `search-tool-mcp`, `safe-filesystem-mcp` and `git-diff-mcp` accept a `FORBIDDEN_PATHS` list of sub-folders and individual files that are excluded from all tool operations. A forbidden folder also forbids everything inside it. Leave the value empty (or omit it) to forbid nothing.

- Relative paths are resolved against `INPUT_DIR` / `ALLOWED_DIR`. Absolute paths are also accepted.
- Separate paths with commas. Whitespace around each path is ignored:
  ```json
  "FORBIDDEN_PATHS": "folder/sub_folder/code-file.py, another_folder"
  "FORBIDDEN_PATHS": ""
  ```
- In `safe-filesystem-mcp`, forbidden paths are hidden from `list_directory`, and deleting or moving a folder that contains a forbidden path is denied.
- In `git-diff-mcp`, forbidden paths are denied and left out of status and diff output. Otherwise a file's history would reveal its contents.
- There is no need to add the [ignored directories](#ignored-directories) to `FORBIDDEN_PATHS` for `generate-map-mcp` or `search-tool-mcp`, as they are already skipped by default.

Limitations:
- `commands-mcp` does not use `FORBIDDEN_PATHS`. Do not register commands that can print arbitrary files (e.g., `git show` or `cat {arg}`) if some files must stay hidden.
- In `git-diff-mcp`, a file that was moved into a forbidden folder can still be seen in the history of its old, allowed path. Commit messages are not filtered either.

#### Ignored directories

`generate-map-mcp` and `search-tool-mcp` always skip folders with the following names, at any depth within `INPUT_DIR` (defined as `IGNORED_DIRS` in the scripts):

`node_modules`, `.git`, `__pycache__`, `dist`, `build`, `.next`, `.venv`, `venv`, `env`, `.pytest_cache`, `.idea`, `.vscode`, `target`, `out`, `.mypy_cache`, `.ruff_cache`

`generate-map-mcp` additionally skips files matched by the `.gitignore` file (see `GITIGNORE_PATH`).

`safe-filesystem-mcp` has no ignored directories, because it only accesses the paths the AI explicitly asks for. To block it from folders such as `.git` or `.venv`, add them to its `FORBIDDEN_PATHS`.

`git-diff-mcp` has no ignored directories either. Git's own `.gitignore` rules decide which untracked files it lists.

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

This tool manages a context folder containing Markdown files, allowing for listing, reading, writing, appending, and removing context files.

- **list_context_files** Lists all Markdown files in the context folder.
- **read_context_file** Reads the content of a specific Markdown file in the context folder.
- **write_context_file** Creates a new Markdown file or overwrites an existing one in the context folder.
- **append_to_context_file** Appends content to a specific Markdown file in the context folder.
- **read_all_context_files** Reads the content of all Markdown files in the context folder at once.
- **remove_context_file** Removes a specific Markdown file from the context folder.

### generate-map-mcp

Generates a comprehensive structure map and summary of a codebase, supporting Python (via AST) and JS/TS (via regex).

- **generate_codebase_map** Generates a structuremap of the codebase.
- **generate_file_map** Generates the file tree map of the codebase without detailed summaries.
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

- **write_file** Safely replaces the whole content of an existing file with hash validation.
- **edit_file** Changes part of an existing file by replacing an exact piece of text, with hash validation. Keeps the file's line endings and BOM, and reports ambiguous or missing matches with line numbers.
- **create_file** Creates a new file with the provided content.
- **read_file_with_metadata** Reads a file and returns its content along with metadata (sha256, encoding, etc.).
- **read_image** Reads a PNG, JPEG, GIF or WebP image (up to 10 MB) and returns it as an image the AI can see, along with its metadata. Requires a vision-capable model; with other models, ollmcp skips the image and shows a warning.
- **get_file_stats** Retrieves metadata about a file without reading its content.
- **list_directory** Lists all files and directories within the specified path.
- **create_directory** Creates a new directory at the specified path.
- **move_file** Moves or renames a file or directory.
- **delete_file** Deletes a file or a directory.
- **get_filesystem_config** Returns the allowed directory, forbidden paths and the working directory relative paths are resolved against, to explain why a path may be denied or not found.

### search-tool-mcp

Performs text or regex searches across files in a specified directory.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase.
- **search_files_by_pattern** Searches for files and directories that match a glob-style pattern.
- **get_search_config** Returns the input directory, ignored directories, forbidden paths and search limits, to explain why a file or match may be missing from the searches.
