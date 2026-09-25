# ollmcp-generic-tools

Generic custom MCP servers for Ollmcp for Python and JS/TX development purposes.

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

### instructions-mcp

Retrieves project-specific instructions from a designated Markdown file.

- **get_project_instructions** Returns the content of the project-specific instructions markdown file.

### search-tool-mcp

Performs text or regex searches across files in a specified directory.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase.


The `.mcp.json` file includes an example of a servers JSON file that ollmcp can read to gain access to the MCP servers and their tools. Place it in the project root folder, adjust the filepaths and start ollmcp in the same folder using `ollmcp`.