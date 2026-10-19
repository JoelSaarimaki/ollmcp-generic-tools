# ollmcp-generic-tools

Generic MCP tools for [ollmcp](https://github.com/jonigl/mcp-client-for-ollama), for developing Python and JS/TS projects with a local AI model run by Ollama. They are grouped into toolsets (files, outlines, search, git, commands, context files and web search), which one MCP server serves, and each toolset or single tool can be switched off in the tools config.

The tools are designed for small local models with small context windows: every response is limited in size and says how to get the rest, the AI reads an outline first and then only the sections it needs, edits are checked against the file's hash and tolerate common copying mistakes, every file change can be undone, and the tools stay within one project folder that you choose.

For the design principles behind the tools and the planned improvements, see [DEVELOPMENT.md](DEVELOPMENT.md).

## Setup

### Prerequisites

1.  **Install Ollama**:
    Download and install Ollama from [ollama.com](https://ollama.com/). Once installed, you can run a model (for example qwen3-coder:30b), using:
    ```bash
    ollama run qwen3-coder:30b
    ```

2.  **Install ollmcp and the tools**:
    The tools are developed and tested with Python 3.14 and need at least Python 3.10. Clone this repository, and in its folder run:
    ```bash
    python -m pip install -r requirements.txt
    ```
    [`requirements.txt`](requirements.txt) installs the `ollmcp` client and this repository as the Python package `ollmcp_tools`, with the packages it uses (listed in [`pyproject.toml`](pyproject.toml)): `mcp`, `tree-sitter` with its JavaScript and TypeScript grammars for the outlines of JS/TS files, and `ollama` for the web toolset. The tree-sitter packages are pinned to one minor version each, as the grammars must match the tree-sitter version.

    The package is installed in editable mode, so it is used straight from the repository: after a `git pull`, every project uses the new version without reinstalling. Install it once, and it works in all your projects. To install only the tools into another Python environment, run `python -m pip install -e <path to this repository>` with that environment's Python.

### Setting up a project

In the root folder of a project, run:

```bash
python -m ollmcp_tools init
```

It creates `.mcp.json` and the `.ollmcp-tools` folder from the templates in [`ollmcp_tools/templates`](ollmcp_tools/templates), and prints what to do next. Files that already exist are never overwritten, so running it again is safe.

```
my-project/
    .mcp.json                    # tells ollmcp to start the tools, the same in every project
    .ollmcp-tools/
        tools-config.json        # all settings of the tools for this project
        commands.json            # the commands the AI can run
        instructions.md          # your instructions for the AI, which it cannot change
        .gitignore               # leaves out context/ and suggested-commands.json
        context/                 # the AI's plans and notes for the current task
        suggested-commands.json  # created when the AI suggests a command
```

`.mcp.json` contains no paths of the tools, only the tools config file of the project:

```json
{
    "mcpServers": {
        "dev": {
            "command": "python",
            "args": ["-m", "ollmcp_tools"],
            "env": {
                "MCP_TOOLS_CONFIG": ".ollmcp-tools/tools-config.json"
            }
        }
    }
}
```

- `python` is the Python found on PATH when `ollmcp` starts, so the package must be installed into it. The project's commands, such as `python -m pytest`, run with the same Python. If you start `ollmcp` in a project's activated virtual environment, so that the commands use the project's packages, install the tools into that environment too: `python -m pip install -e <path to this repository>`.
- A relative `MCP_TOOLS_CONFIG` path is resolved against the folder where `ollmcp` is started, so start it in the project's root folder.
- ollmcp names the tools `<server key>.<tool>`, e.g. `dev.get_outline`, and since version 0.35 sends them to the model with `_` instead of `.`, e.g. `dev_get_outline`, for Ollama too. The instructions template uses the `.` names, which the model understands, so change them there if you use another key.

**Why `allowed_dir` is `".."`:** the paths in the tools config are relative to the config file's own folder, `.ollmcp-tools`, so `".."` is the project folder above it. `init` prints the folder it resolves to, and if `allowed_dir` points to a folder that does not exist, the error says what it was resolved against.

After `init`, follow its checklist:
1. Check `allowed_dir`, and add the folders and files the AI must never access to `forbidden_paths`.
2. Keep the commands in `commands.json` that fit the project, and change or delete the rest. The template has commands for Python projects that use pip and a `requirements.txt`, and for npm projects.
3. Adapt `instructions.md` to the project, and remove the tools you disable (see [Choosing the toolsets](#choosing-the-toolsets)).
4. Add your Ollama API key to the [user config file](#user-config-file) if you want web search. Without a key the `web` tools are not offered.
5. Set the system prompt in ollmcp (see [Set a system prompt](#set-a-system-prompt)). It also keeps the tool calls working.

#### What to commit

Commit `.mcp.json` and the `.ollmcp-tools` folder: the settings, commands and instructions are the same for everyone who works on the project, and `forbidden_paths` protects the same files for all of them. The `.gitignore` in the folder leaves out each person's own files:
- `context/`, as the plans are for one person's current task: someone else's AI would otherwise take them as its own plans.
- `suggested-commands.json`, as the suggestions are for the person who reviews them.

If you use the tools alone and do not want to share them, e.g. with colleagues who do not use them, you can instead add `.mcp.json` and `.ollmcp-tools/` to the project's own `.gitignore`. The tools still read them, as `.gitignore` only hides files from the outline, search and git tools, and the folder is forbidden anyway.

The API key is not in the project at all, but in each person's [user config file](#user-config-file). Someone who clones the project runs `python -m ollmcp_tools init` once: it leaves the committed files as they are and creates the context folder and the user config file.

#### User config file

`~/.ollmcp-tools/user-config.json` (e.g. `C:\Users\<name>\.ollmcp-tools\user-config.json`) holds your own settings for all your projects. `init` creates it if it does not exist yet. The same folder holds the backups of `restore_file`, in `backups/` (see [Backups](#backups)):

```json
{
    "ollama_api_key": null
}
```

| Setting | Description |
|---|---|
| `ollama_api_key` | The Ollama API key used by the `web` toolset. Create one at [ollama.com/settings/keys](https://ollama.com/settings/keys). The key is never shown to the AI, as the file is [protected](#protected-files). (Optional, without it the `web` tools are not offered to the AI) |

The server refuses to start if the file is not valid JSON or contains an unknown setting. An `ollama_api_key` in a project's tools config stops the server with an error saying to move it here, so that it is not committed with the project.

### Tools config file

The `.ollmcp-tools/tools-config.json` that `init` creates, here with a forbidden `secrets` folder and the `web` toolset and `write_file` tool disabled:

```json
{
    "allowed_dir": "..",
    "forbidden_paths": [".ollmcp-tools", "secrets"],
    "ignored_dirs": null,
    "gitignore_path": null,
    "max_output_chars": 40000,
    "command_registry": "commands.json",
    "command_suggestions": "suggested-commands.json",
    "context_folder": "context",
    "read_only_files": ["instructions.md"],
    "backup_folder": null,
    "disabled_toolsets": ["web"],
    "disabled_tools": ["write_file"]
}
```

All settings except `allowed_dir` are optional. An optional setting that is left out or set to `null` uses its default, so `null` can be used to show that a setting exists without using it. Note that an empty list is not the same as `null`: `"ignored_dirs": []` skips no folders at all, while `"ignored_dirs": null` skips the default folders.

Relative paths are resolved against the folder of the config file, except `forbidden_paths`, which are resolved against `allowed_dir`: `".."` is the project folder, and `".ollmcp-tools"` in `forbidden_paths` is the tools' own folder in it. The server refuses to start if the file is missing, is not valid JSON, lacks `allowed_dir`, points `allowed_dir` to a folder that does not exist, has a `backup_folder` that contains `allowed_dir`, has a `command_suggestions` file that is the config file or the command registry, names an unknown toolset or tool, disables every tool, or contains an unknown setting, so a typo cannot silently weaken the configuration. A setting that was renamed or moved, such as the earlier `commands_config` and `ollama_api_key`, is named in the error with what to do instead. The config file itself is always [protected](#protected-files).

| Setting | Description |
|---|---|
| `allowed_dir` | The project directory the tools can access. Relative paths given to the tools are resolved against it, so a path returned by one tool (e.g. `src/app.py` from a search) works as-is in the others. To focus the AI on part of the project, such as `src`, it can limit outlines and searches with their `path` argument. (Required) |
| `forbidden_paths` | List of folders and/or files the tools must never access, even within `allowed_dir`. `init` adds `.ollmcp-tools`, so that the file tools cannot change the instructions or read the plans other than through the `context` toolset. See [Forbidden paths](#forbidden-paths). (Optional) |
| `max_output_chars` | The largest size of a tool response in characters, about 4 characters per token. Lower it for models with small context windows, e.g. `12000` for an 8k-token context. See [Output limits](#output-limits). (Optional, defaults to `40000`, at least `2000`) |
| `gitignore_path` | The `.gitignore` file used by the outline and search tools. (Optional, defaults to `.gitignore` in `allowed_dir`) |
| `command_registry` | The command registry of the `commands` toolset. It is always [protected](#protected-files). See [commands](#commands). (Optional) |
| `command_suggestions` | The file where `suggest_command` saves the commands the AI suggests, for you to review. It must not be the `command_registry`. See [Suggested commands](#suggested-commands). (Optional, without it suggestions are only shown in the chat) |
| `context_folder` | The folder of the context Markdown files of the `context` toolset. (Optional) |
| `read_only_files` | List of files maintained by you, such as project instructions, that the AI can read with the `context` toolset but not change or remove. They can be located anywhere, and are read by their file name. (Optional) |
| `backup_folder` | The folder where `restore_file`'s backups are kept. It must not contain `allowed_dir`. See [Backups](#backups). (Optional, defaults to a folder for the project in `~/.ollmcp-tools/backups`) |
| `ignored_dirs` | List of folder names the outline and search tools skip at any depth. Replaces the defaults completely. See [Ignored directories](#ignored-directories). (Optional, defaults to `node_modules`, `.git`, `build` and other common build and cache folders) |
| `disabled_toolsets` | List of toolsets that are not loaded, e.g. `["web", "git"]`. See [Choosing the toolsets](#choosing-the-toolsets). (Optional, defaults to none, so every toolset is loaded) |
| `disabled_tools` | List of single tools that are not offered to the AI, e.g. `["write_file", "web_fetch"]`. See [Choosing the toolsets](#choosing-the-toolsets). (Optional, defaults to none) |

The AI can see the effective configuration with the `get_tools_config` tool of the `filesystem` toolset.

### Choosing the toolsets

| Toolset | Tools |
|---|---|
| [`filesystem`](#filesystem) | `read_file_with_metadata`, `edit_file`, `write_file`, `create_file`, `read_image`, `list_directory`, `move_file`, `delete_file`, `restore_file`, `get_tools_config` |
| [`outline`](#outline) | `get_outline` |
| [`search`](#search) | `search_text_in_files`, `search_files_by_pattern`, `search_relevant_files` |
| [`git`](#git) | `get_git_diff`, `get_git_history`, `get_git_status` |
| [`commands`](#commands) | `list_available_commands`, `run_predefined_command`, `suggest_command` |
| [`context`](#context) | `list_context_files`, `read_context_file`, `write_context_file`, `remove_context_file` |
| [`web`](#web) | `web_search`, `web_fetch` |

Every toolset is loaded unless it is listed in `disabled_toolsets`, and every tool of a loaded toolset is offered unless it is listed in `disabled_tools`. Change the settings and restart `ollmcp` to apply them.

- Every tool definition is sent to the model with each request, so leaving out the tools a project does not need saves context, e.g. `git` in a folder without git. The `web` tools are left out automatically while no Ollama API key is set, and `get_tools_config` tells the AI why (`not_served`).
- A disabled toolset is not loaded at all, so its settings are not checked either: a project without a command registry can disable `commands` instead of creating an empty one. A toolset whose tools are all in `disabled_tools` is not loaded either.
- The toolsets work best together: their responses point to tools of other toolsets, e.g. `get_outline` to `read_file_with_metadata`, and the other tools to `get_tools_config` when a path is denied. Disable a toolset when the project does not need it, not to make the AI use another one.
- The [instructions template](ollmcp_tools/templates/instructions.md) names tools of every toolset. Remove the disabled ones from your own instructions, and `dev.web_search` if you have no API key, so that the AI does not try to call tools it does not have.
- A toolset added in a later version of the tools is loaded unless you disable it.

### Running

Once set up, start `ollmcp` in the project's root folder:

```bash
ollmcp
```

#### Set a system prompt

Set this system prompt in ollmcp and save it:

```text
At the start of a session, call dev_read_context_file to read the project instructions and plans.
```

1. In ollmcp, type `/model-config` (or `/mc`) and set the system prompt.
2. Type `/save-config` (or `/sc`) and save it as `default`, which ollmcp loads automatically when it starts.

The system prompt does two things:
- **The AI reads your project instructions** (`read_only_files`) and earlier plans (`context_folder`) at the start of a session. Without it, the AI only sees them after you ask it to call `read_context_file`.
- **It keeps tool calls working.** qwen3-coder 30b sometimes writes a tool call without its opening `<tool_call>` tag, e.g. `<function=dev_read_context_file> </tool_call>` as plain text in its answer. ollmcp then shows the call as an answer, and the tool is never called. Without a system prompt, this happened in 3 of 24 test requests with all tools enabled (0 of 3 for "Read the task in the context files and act accordingly"); with this system prompt, in none of the 24. A generic prompt such as "You are a helpful assistant." did not help. The tool name is written as the model sees it, with `_`.

If a broken call still appears:
- Type `/clear` before trying again. ollmcp keeps the broken call in the chat history as an ordinary answer, and the model then repeats it.
- Leave out the tools the project does not need (see [Choosing the toolsets](#choosing-the-toolsets)): the more tool definitions, the more often it happens.
- Leave `num_ctx` unset in `/model-config`, or keep it large: ollmcp then asks for a 32 000-token context. With Ollama's own default of 4 096 tokens, the tool definitions alone (about 4 200 tokens with every tool) fill the context, the prompt is cut, and the tool calls break.

### Testing

The tools have an automated test suite in `tests/`. [`requirements-dev.txt`](requirements-dev.txt) installs `requirements.txt` and the test runner. Run the tests from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

See [DEVELOPMENT.md](DEVELOPMENT.md#how-changes-have-been-verified) for how the tests are organized.

## Toolsets

### filesystem

Provides safe and robust file system operations, including metadata retrieval, atomic writes and undoing the changes it makes.

- **write_file** Safely replaces the whole content of an existing file with hash validation. Rejects placeholder comments such as `// ... existing code ...` and warns if the file shrinks to less than half.
- **edit_file** Changes part of an existing file by replacing an exact piece of text, with hash validation. Keeps the file's line endings and BOM, and shows the changed lines, the new SHA-256 and the file's outline after the edit.
- **create_file** Creates a new file with the provided content, and shows its outline.
- **read_file_with_metadata** Reads a text file and returns its exact content along with metadata (sha256, line count, encoding, etc.). Can read a class, function or Markdown heading by its name (`section`, e.g. `App.run` or `Install`) or a range of lines (`start_line`, `end_line`) with optional line numbers, and reads long files in parts of up to 1000 lines.
- **read_image** Reads a PNG, JPEG, GIF or WebP image (up to 10 MB) and returns it as an image the AI can see, along with its metadata. Requires a vision-capable model; with other models, ollmcp skips the image and shows a warning.
- **list_directory** Lists all files and directories within the specified path.
- **move_file** Moves or renames a file or directory, creating missing destination folders. There is no separate tool for creating folders: `create_file` and `move_file` create the folders they need.
- **delete_file** Deletes a file or a directory. It is moved into the backup folder, so `restore_file` can bring it back.
- **restore_file** Undoes the last change that `edit_file`, `write_file`, `create_file`, `move_file` or `delete_file` made to a file or folder, e.g. when an edit broke a file; calling it again undoes earlier changes. A restored edit gets back its exact bytes (line endings and BOM), a created file is removed, a moved file is moved back (by its old or new path), and a deleted file or folder comes back. The response shows the new SHA-256 and outline of a restored file. See [Backups](#backups).
- **get_tools_config** Returns the tools config (enabled toolsets, toolsets that are enabled but not served, such as `web` without an API key, and disabled tools, allowed directory, forbidden and protected paths, output limit) and what each toolset sees: the files the outline and searches skip, the git repository, the command registry, the context files and the backup folder. Helps the AI find out why a file is missing or a path is denied.

The file reading and editing tools are designed to let local AI models read and edit files accurately:
- File content is returned as plain text in a fenced code block, not inside JSON, so quotes, backslashes and line breaks appear exactly as in the file and can be copied into `edit_file` as-is.
- `edit_file` tolerates common copying mistakes: line number prefixes and code block fence lines copied from `read_file_with_metadata` are removed, and trailing whitespace does not need to match. If the text is not found, the error shows the closest matching lines to copy.
- Placeholder comments such as `// ... existing code ...` are rejected, as they would otherwise replace real code.
- After every write to a Python, JS/TS or Markdown file (`edit_file`, `write_file`, `create_file`, `restore_file`), the response shows the file's outline with line spans, lists the sections that were added, and warns about sections that are no longer in the file, about a Python file that no longer parses, and about a new JS/TS syntax error. An accidental overwrite or deletion is noticed right away, instead of relying on the AI to spot a missing entry, and can be undone with `restore_file`.

### outline

Gives the AI an outline of the project, so that it can find the code it needs and then read only that part, instead of reading whole files or a map that is cut off at the output limit. The parsers are in `ollmcp_tools/parsing.py`: Python via `ast`, JS/TS via [tree-sitter](https://tree-sitter.github.io/), and Markdown by its headings. Both parsers give exact line spans, and a JS/TS file with a syntax error is still outlined, with the error reported.

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

### search

Searches the project's files: by exact text or regex, by file name pattern, and by relevance when the exact text is unknown. The descriptions of the two text searches point to each other, so that the AI uses `search_text_in_files` to find every use of a name and `search_relevant_files` to find where something is.

- **search_text_in_files** Search for text or regex patterns from code files in the codebase. A query that is not a valid regex, such as `foo(`, is searched for as literal text, and the response says so. Can be limited to a folder or file (`path`) and to matching file names (`file_pattern`, e.g. `*.py`), and can show lines around each match (`context_lines`). Very long lines are shortened around the match.
- **search_files_by_pattern** Searches for files and directories that match a glob-style pattern, such as `src/**/*.test.ts`.
- **search_relevant_files** Finds the files most relevant to a sentence, code snippet or error message when the exact text is unknown. The words and names in the query are used as keywords, names also by their parts (`getUserName`: `get`, `user`, `name`). Keywords found in few files weigh much more than common ones, so a name used in only one other file ranks that file first. Each result shows the file's best matching section (function, class or heading) with its best lines and how to read it, and the response lists the weight of each keyword and the keywords that were not found anywhere. Can be limited with `path` and `file_pattern`; `max_results` sets the number of files (default 10, up to 30).

### git

Inspects the git history and changes of the files in a repository.

- **get_git_diff** Retrieves the differences of one file (`path` of a file), or of all changed files at once, optionally limited to a folder, with lists of the changed and new untracked files. Useful for reviewing all changes before committing.
- **get_git_history** Retrieves the commit history and associated diffs for a specified file or folder.
- **get_git_status** Returns the results of `git status`. Also tells whether a folder is in a git repository: if not, the error is `not_a_repository`.

### commands

Gives the AI access to very specific console commands, and tells it when it could or should use them.

- **list_available_commands** Returns a list of all allowed console commands and their descriptions.
- **run_predefined_command** Executes a specific console command from the allowed registry.
- **suggest_command** Suggests a missing command for you to review and add to the registry. It never changes the registry itself.

Commands are defined in the JSON file set as `command_registry` in the tools config, `.ollmcp-tools/commands.json` after `init`:

```json
{
    "run_test_file": {
        "template": "python -m pytest {arg}",
        "description": "Runs the tests of one file, or one test. Argument: the test file or test (e.g., 'tests/test_orders.py').",
        "timeout": 300
    }
}
```

- `template`: A single program and its arguments. `{arg}` is replaced with the argument given by the AI, which is always passed as one argument. It can be anywhere in the template, also inside an argument (e.g., `--select={arg}`). `{arg}` is the only placeholder: other names such as `{args}` or `{ARG}` stop the server with an error.
- `description`: Tells the AI when to use the command.
- `timeout`: Seconds before the command is stopped. (Optional, defaults to 120)

The [template](ollmcp_tools/templates/commands.json) that `init` copies is a fuller example covering dependencies (pip with a `requirements.txt`), tests (pytest), linting and formatting (ruff), type checking (mypy) and npm scripts. The Python tools are run as `python -m <tool>`, so they are found in the same Python environment as the packages. Change the commands for a project that uses another tool, such as poetry (`poetry run pytest`) or uv (`uv run pytest`). The template also includes commands that make small models work better:
- Running one test file, one test or the tests matching a name (`run_test_file`, `run_tests_matching`, `npm_test_file`, `npm_test_name`) and the tests that failed last time (`run_failed_tests`), which is faster and gives shorter output than the whole suite.
- Looking up the real API of an installed library (`python_doc`, which runs `pydoc`) or an npm package's details (`npm_package_info`), as the tools cannot read installed packages and small models often guess function signatures.

Commands run in `allowed_dir`, so commands such as `pytest` or `ruff check .` work on the project regardless of where `ollmcp` was started. A program such as `python` is the one found on PATH when `ollmcp` starts: to run the commands in a project's virtual environment, start `ollmcp` in it (see [Setting up a project](#setting-up-a-project)). While the `commands` toolset is enabled, the server refuses to start if the registry file is missing or not valid JSON, or if a command lacks its `template` or `description`, has an invalid `timeout` or has an unknown placeholder in its template.

To keep the AI from running anything other than the defined commands:
- Commands run without a shell, so shell features such as pipes (`|`), chaining (`&&`), redirection (`>`) and shell built-ins (`echo`, `dir`, `cd`) are not available in templates. Put such logic in a script and call the script from the template.
- Arguments that start with `-`, or contain line breaks, are rejected so the AI cannot add unintended options.
- On Windows, if the program is a batch file (`.bat` / `.cmd`, e.g. `npm`), arguments containing `& | < > ^ % ! " ( )` are rejected.
- Commands cannot read input, so interactive prompts end immediately instead of hanging.
- A command succeeds only if it exits with code 0, and long output is cut from the middle (see [Output limits](#output-limits)).
- The registry is [protected](#protected-files), so the AI cannot add commands itself.

#### Suggested commands

When the AI needs a command that is not in the registry, it can suggest one with `suggest_command`. Only you can add it: whatever is in the registry, the AI can run, so you should read the exact template before adding it.

The tool checks the suggestion like the registry is checked at startup (e.g. the `{arg}` placeholder and the `timeout`), and also that the name is not taken and that the template names a program. It then adds the entry to the `command_suggestions` file, in the same format as the registry, and the AI shows it to you in the chat:

```json
{
    "run_e2e_tests": {
        "template": "npm run e2e -- {arg}",
        "description": "Runs the end-to-end tests of one file. Argument: the test file (e.g., 'e2e/cart.spec.ts').",
        "timeout": 600
    }
}
```

To accept a suggestion, copy its entry into the `command_registry` and restart `ollmcp`. The entries are written by the server, not by the AI, so their quotes and backslashes are always valid JSON. A suggestion with the same name as an earlier one replaces it. Delete entries from the suggestions file when you no longer need them.

The response also lists warnings for you to check: a program that is not on PATH, a shell as the program (e.g. `powershell` or `bash`), and code options such as `-c` together with `{arg}`, which would let the AI run any code through the command.

### context

Manages a context folder containing Markdown files, allowing for listing, reading, writing, appending, and removing context files. File names must be plain `.md` names such as `notes.md`: names with folders (`../x.md`, `sub/x.md`) or other extensions are rejected, so the tools cannot reach files outside the context folder.

It also gives the AI your project instructions: list them in `read_only_files`, and they are listed and read first, marked `(read-only)`, while writing, appending to or removing them is refused. A context file with the same name as a read-only file is hidden and cannot be created, so it cannot take the instructions' place.

- **list_context_files** Lists the read-only files and all Markdown files in the context folder, with their line counts and headings with line spans.
- **read_context_file** Reads a Markdown file in the context folder, or a read-only file: the whole file, the section of one heading (`section`), or a range of lines (`start_line`, `end_line`). Long files are read in parts. Without `filename`, it reads the read-only files and all context files at once, which is meant for the start of a session; context files that do not fit are then shown as their headings with line spans, to be read by section.
- **write_context_file** Creates a new Markdown file or overwrites an existing one in the context folder, or adds to its end with `append=True`. The response shows the new headings with their line spans, and warns if headings of the old content are gone.
- **remove_context_file** Removes a specific Markdown file from the context folder.

Read-only only applies to the `context` toolset. The `filesystem` toolset could still change a read-only file inside the project, which is why `init` adds `.ollmcp-tools` to `forbidden_paths`: add the folder of any other read-only file there too. The `context` toolset does not use `forbidden_paths`, so it can still read the files.

### web

Searches the web and reads web pages using Ollama's hosted [web search API](https://docs.ollama.com/capabilities/web-search). Requires `ollama_api_key` in the [user config file](#user-config-file): without it the tools are not offered to the AI, as they could only return an error. The `ollama` Python package is installed with the tools. The queries and URLs are sent to ollama.com.

- **web_search** Searches the web and returns the title, URL and content of the best matching pages (`max_results`, 1–10, default 3).
- **web_fetch** Fetches a web page and returns its title, links and text content. The content is shown as-is in a code block, so code copied from a page is not JSON-escaped. Long pages are read in parts with `start_char`.

Web pages can contain text written to mislead the AI (prompt injection). Review the AI's changes when it has read web content.

## Details and patterns

### Forbidden paths

The `forbidden_paths` setting of the [tools config file](#tools-config-file) lists sub-folders and individual files that the `filesystem`, `outline`, `search` and `git` toolsets exclude from all tool operations. A forbidden folder also forbids everything inside it. Leave the list empty (or omit it) to forbid nothing.

- Relative paths are resolved against `allowed_dir`. Absolute paths are also accepted:
  ```json
  "forbidden_paths": ["folder/sub_folder/code-file.py", "another_folder"]
  ```
- In the `filesystem` toolset, forbidden paths are hidden from `list_directory`, and deleting or moving a folder that contains a forbidden path is denied.
- In the `git` toolset, forbidden paths are denied and left out of status and diff output. Otherwise a file's history would reveal its contents.
- There is no need to add the [ignored directories](#ignored-directories) to `forbidden_paths` for the `outline` or `search` toolsets, as they are already skipped by default.

Limitations:
- The `commands` toolset does not use `forbidden_paths`. Do not register commands that can print arbitrary files (e.g., `git show` or `cat {arg}`) if some files must stay hidden. See also [Protected files](#protected-files).
- In the `git` toolset, a file that was moved into a forbidden folder can still be seen in the history of its old, allowed path. Commit messages are not filtered either.

### Backups

Before `edit_file`, `write_file`, `create_file`, `move_file` or `delete_file` changes anything, the `filesystem` toolset records the change in a journal in the backup folder, with a copy of a changed file. A deleted file or folder is moved into the backup folder instead of being removed. If the backup fails, the change is not made. `restore_file` undoes the changes one at a time, newest first, and moves what it replaces into the backup folder too, so a restore never loses a version, even one you changed yourself in between. The response says where that version is kept.

- The backup folder is `~/.ollmcp-tools/backups/<project folder name>-<hash of its path>` by default, next to the [user config file](#user-config-file), outside the project, so backups do not show up in git or in the AI's outlines and searches. Set `backup_folder` in the [tools config file](#tools-config-file) to use another folder.
- The tools cannot access the backup folder, even if it is inside the project: it is hidden and protected like the [protected files](#protected-files). The AI uses it only through `restore_file`.
- The last 200 changes are kept; the backups of older ones are removed.
- Only the changes made by the file tools are recorded: not changes by `run_predefined_command` (e.g. a formatter), `write_context_file`, or you. Git is not used, so it also works for new and untracked files and in folders without git.
- Deleting a large folder on another drive than the backup folder copies it there, which can take a while.

### Protected files

Files named `.mcp.json` contain the MCP server configuration, which servers ollmcp runs and which tools config file they read, the [tools config file](#tools-config-file) contains the allowed directory and forbidden paths, the `command_registry` lists the commands the AI can run, and the [user config file](#user-config-file) holds your API key. If the AI could change them, it could remove its own restrictions or register any command, which would take effect the next time the tools start. Therefore `.mcp.json` files, at any depth, the tools config file in use, the user config file and the command registry are always protected, regardless of `forbidden_paths` (defined as `PROTECTED_FILE_NAMES` and `PROTECTED_PATHS` in `ollmcp_tools/common.py`). The registry is protected even if it does not exist yet, so the AI cannot create it either. The `command_suggestions` file is not protected:

- The `filesystem` toolset denies reading, writing, editing, moving and deleting them, and creating a file or renaming a file to that name. Folders containing one cannot be moved or deleted either.
- The `outline` and `search` toolsets leave them out of outlines and search results.
- The `git` toolset leaves them out of diffs, status and history.
- The `context` toolset only accepts plain `.md` file names directly inside its context folder, so it cannot reach them either.

The protection also covers other spellings of the name that Windows treats as the same file, such as a different letter case, a trailing dot or space, a `::$DATA` suffix or an 8.3 short name.

**The tools cannot fully protect files from the `commands` toolset.** Commands such as tests, scripts or `npm run` execute project code, which the AI can write and which can read or change any file, including the files in `.ollmcp-tools`. A change to the tools config or the registry only takes effect at the next start, and shows up in `git status`. For complete protection, keep the MCP configuration and the `.ollmcp-tools` folder outside the project folder, where no tool can reach them, and start `ollmcp` with its path:

```bash
ollmcp --servers-json C:\path\outside\project\.mcp.json
```

When the tools config file is outside the project, set `allowed_dir` in it to the project folder, as an absolute path or relative to the config file, and give `MCP_TOOLS_CONFIG` in `.mcp.json` as an absolute path, or relative to the folder where `ollmcp` is started. `init` does not set this up: move the folder after running it. Keep this repository outside the project folder too.

### Ignored directories

The `outline` and `search` toolsets skip folders with the names listed in the `ignored_dirs` setting of the [tools config file](#tools-config-file), at any depth within `allowed_dir`. If the setting is not given, these defaults are used (defined as `DEFAULT_IGNORED_DIRS` in `ollmcp_tools/common.py`):

```json
"ignored_dirs": ["node_modules", ".git", "__pycache__", "dist", "build", ".next", ".venv", "venv", "env", ".pytest_cache", ".idea", ".vscode", "target", "out", ".mypy_cache", ".ruff_cache"]
```

A list in the config file replaces the defaults completely: copy the list above and add or remove names to suit the project. For example, remove `build` or `env` if the project has real source code in folders with those names, or add `coverage` or `.gradle`. An empty list skips no folders by name.

- The entries are folder names, not paths: `build` skips every folder named `build`. To exclude one specific folder, such as `docs/build`, add it to `forbidden_paths` instead.
- These folders are skipped without being entered, so even a large `node_modules` does not slow the tools down. Removing `node_modules` from the list makes outlines and searches of JS/TS projects much slower.
- Ignored folders are not a security measure: the `filesystem` and `git` toolsets can still access them. Use `forbidden_paths` for files the AI must not access.

Both toolsets additionally skip files matched by the `.gitignore` file (see `gitignore_path`). The `search` toolset also skips binary files.

The `filesystem` toolset has no ignored directories, because it only accesses the paths the AI explicitly asks for. To block it from folders such as `.git` or `.venv`, add them to `forbidden_paths`.

The `git` toolset has no ignored directories either. Git's own `.gitignore` rules decide which untracked files it lists.

### How responses look in the console

ollmcp shows a JSON response formatted as JSON (with characters such as `ä` shown as `\u00e4`, although the AI gets them as they are), and any other response as Markdown if it contains enough Markdown-like patterns, otherwise as plain text. The text responses are written to read well both ways:

- Header lines (file, SHA-256, notes, warnings) are a list, so that each stays on its own line.
- File content, outlines, search matches and directory-like listings are fenced code blocks, so that they keep their lines, `#` comments do not become headings, and the code is highlighted by its file type (e.g. `python` for `.py` files). The fence is longer than any run of backticks in the content, so Markdown files with code blocks are shown whole.
- The parts are separated by blank lines.

The code block fences are not part of the content, and `edit_file` removes them if the AI copies them into `old_text`.

### Output limits

Local models have small context windows, so every tool response is limited to `max_output_chars` characters (40 000 by default, about 10 000 tokens). When a limit is applied, the response contains a message starting with `Output limited:` that says what was left out and exactly how to get the rest, for example which `start_line` to read next or which folder to outline with `path`. In JSON responses, the message is in the `output_limited` field.

| Tool | Limit | How the rest can be seen |
|---|---|---|
| `read_file_with_metadata` | 1000 lines or `max_output_chars` per read; lines over 2000 characters are shortened; files over 50 MB are not read | Next part with `start_line`; long lines can still be edited using a unique part of the shown text |
| `edit_file` | Shows up to 40 changed lines after the edit, within `max_output_chars` | `read_file_with_metadata` from the given line |
| `edit_file`, `write_file`, `create_file`, `restore_file` | The outline after a write is limited to 2500 characters, or `max_output_chars / 8` if smaller: then only top-level sections are shown, or the list is cut short | `get_outline` for the file |
| `list_directory` | Up to 250 entries, fewer for small budgets; folders are listed first | `search_files_by_pattern` with a suggested pattern |
| `get_outline` | `max_output_chars`: the detail is reduced step by step instead of cutting the outline; files over 1 MB are not outlined; folders with over 1000 files are listed by subfolder only | Suggested subfolders to outline with `path`, with their file counts, or one file with `path` |
| `search_text_in_files` | 100 matches or `max_output_chars`; lines over 300 characters are shortened | A suggested `path` with the most matches, `file_pattern`, fewer `context_lines` |
| `search_files_by_pattern` | 100 matches | A more specific pattern |
| `search_relevant_files` | `max_results` files (up to 30) or `max_output_chars`; 3 lines per file | A lower `max_results`, `path` or `file_pattern` |
| `get_git_diff` of a file, `get_git_history`, `get_git_status` | `max_output_chars` | Read the file instead, a smaller `limit`, or a `path` |
| `get_git_diff` of all changes | `max_output_chars`; up to 200 changed and 200 untracked files are listed | `get_git_diff` with the `path` of a file listed in `changed_files`, or of a folder |
| `run_predefined_command` | `max_output_chars`, split between stdout and stderr; the middle of long output is cut, as errors are usually at the end | A narrower command, such as tests for a single file |
| `read_context_file` without `filename` | `max_output_chars`; read-only files always come first, then context files that do not fit are shown as their headings with line spans, or left out | `read_context_file` with `section` or `start_line` for the listed files |
| `read_context_file` | `max_output_chars` per read | Next part with `start_line`; keep context and read-only files short |
| `list_context_files` | `max_output_chars`: only top-level headings, or only the file names, are shown | `read_context_file` |
| `web_search` | `max_output_chars`, shared equally by the results | `web_fetch` for a whole page |
| `web_fetch` | `max_output_chars` per part; up to 50 links, listed in the first part only | Next part with `start_char` |

The [instructions template](ollmcp_tools/templates/instructions.md) tells the AI to follow these messages instead of repeating the same call, and never to assume that a limited response is the whole result. Include a similar instruction in your own project instructions.