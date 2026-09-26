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
- `GITIGNORE_PATH`: Path to the `.gitignore` file to use for excluding files.

#### instructions-mcp
- `PROJECT_INSTRUCTIONS_FILE`: Path to the project-specific instructions Markdown file.

#### safe-filesystem-mcp
- `ALLOWED_DIR`: The directory within which file operations are permitted. (Defaults to current working directory)

#### search-tool-mcp
- `INPUT_DIR`: The directory to perform searches in. (Defaults to current working directory)

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

### git-diff-mcp

Provides tools to inspect git history and differences for files within a repository.

- **get_file_diff** Retrieves the differences for a specified file.
- **get_file_history** Retrieves the commit history and associated diffs for a specified file.
- **is_git_repository** Checks if the current working directory or a specified directory is a Git repository.

### instructions-mcp

Retrieves project-specific instructions from a designated Markdown file.

- **get_project_instructions** Returns the content of the project-specific instructions markdown file.

### safe-filesystem-mcp

Provides safe and robust file system operations, including metadata retrieval and atomic writes.

- **safe_write_file** Safely updates an existing file with hash validation.
- **create_file** Creates a new file with the provided content.
- **read_file_with_metadata** Reads a file and returns its content along with metadata (sha256, encoding, etc.).
- **read_image_as_base64** Reads an image file and returns its content as a base64 encoded string.
- **get_file_stats** Retrieves metadata about a file without reading its content.
- **list_directory** Lists all files and directories within the specified path.
- **create_directory** Creates a new directory at the specified path.
- **move_file** Moves or renames a file or directory.
- **delete_file** Deletes a file or a directory.

### search-tool-mcp

Performs text or regex searches across files in a specified directory.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase.
- **search_files_by_pattern** Searches for files and directories that match a glob-style pattern.
