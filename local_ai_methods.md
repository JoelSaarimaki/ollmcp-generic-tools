# Methods for local AI models

The methods these tools use to make a small local model, run with Ollama through ollmcp, work well on a software project. Each method states the problem it addresses and what the tools do about it. The details of how each one is built are in [DEVELOPMENT.md](DEVELOPMENT.md), and the measurements with qwen3-coder 30b and gemma4 26b, the recommended model, in [local_ai_test_results.md](local_ai_test_results.md).

## The starting point: what a small local model is like

- **Small context window**, 8k–32k tokens: every tool definition and every response competes for the same space.
- **Weak at exact copying and escaping**: it copies `\n` and `\"` escapes and line number prefixes literally, drops trailing whitespace, and writes placeholders such as `// ... existing code ...` instead of real code.
- **Follows the text it is given**: instructions in tool descriptions, responses and errors are acted on fairly well, so they can steer it.
- **Fragile tool calling**: a call is sometimes written as plain text and never reaches the tools, more often when the tool definitions are large, or the answer is only a token of the model's chat template.
- **Goes straight to the task**: a system prompt that asks for a first step is mostly skipped when the first message is already a task.

## 1. Fewer and smaller tool definitions

Every tool definition is sent with every request, so it costs context all the time, and larger definitions made broken tool calls more frequent.

- **Merge tools with simple arguments, drop duplicates.** `create_file` became `write_file` without a hash, `get_git_status` became `get_git_diff` without arguments, and `list_directory` was covered by the outline and the file name search. 27 tools became 19 over time; the last step took the definitions from 4 109 to 3 327 prompt tokens.
- **But do not merge tools that would need either-or arguments.** `web_search` and `web_fetch` stay separate: a small model fills in "either `query` or `url`" less reliably than two clear tools.
- **Replace a whole toolset with a setting.** The four tools for plan and instruction files were replaced by the file tools and two settings: `allowed_paths` (a plans folder inside a forbidden folder) and `read_only_files`.
- **Do not offer tools that cannot work.** The web tools are left out while no API key is set, instead of returning an error every time.
- **Let each project leave out what it does not need** with `disabled_toolsets` and `disabled_tools`.
- **Short descriptions**: what the tool does and what the model must get right, one line per argument, no types (they are in the schema). Guidance that an error gives when needed is not repeated in the description.
- **Trim the schemas** sent to the model: no generated titles, no `anyOf: [type, null]` for optional arguments.
- **Remove the docstring indentation**, which was sent to the model as it is and cost about 90 tokens.
- **Keep "Defaults to …" in the descriptions**: qwen3-coder's prompt template does not show schema defaults, so the text is the model's only source for them.
- **One server key, `dev`, and tool names that say what they work on** (`get_git_diff`, `get_tools_config`): there is only one way to write each name.
- **A size test**: the test suite fails if the definitions of all tools grow past a set number of characters, so every addition has to earn its place.

## 2. Responses that fit and say how to continue

- **A budget for every response** (`max_output_chars`, 40 000 characters by default) and tool-specific limits on top, e.g. 1000 lines per read and 100 search matches.
- **A limited response always says so and gives the exact next call**: `Output limited:` with the `start_line` to read next, the subfolders to outline with their file counts, or the folder with the most search matches. The model is never left guessing whether it saw everything.
- **Reduce detail instead of cutting.** An outline that does not fit drops summaries, then nested sections, then all sections, and finally lists only subfolders with file counts: every level still covers the whole folder.
- **Keep the useful end of long output.** Command output keeps the start and the end, where errors usually are.

## 3. Outline first, then read only what is needed

- **An outline of the project or a file** with the line span of every class, function, component and heading, and short summaries, from Python's `ast`, tree-sitter for JS/TS, and Markdown headings.
- **Read a section by its name** (`section='App.run'`), because small models copy line numbers wrongly and line numbers go stale after an edit.
- **Accept what the model copies**: a name matches with or without its class, in any letter case, or as a whole outline line copied as the name.
- **Full relative paths everywhere**, in the same form in every tool, so that a path from one response works as-is in the next call.
- **An outline after every write**, with the sections that were added and an explicit warning about sections that are gone, a Python file that no longer parses, or a new JS/TS syntax error. A small model does not notice a missing entry by itself.

## 4. Text the model can copy correctly

- **File content as plain text in a code block, not inside JSON**, so quotes, backslashes and line breaks appear exactly as in the file. JSON escaping made models copy `\n` and `\"` into their edits.
- **Readable non-ASCII characters** (`ä`, not `ä`) in JSON responses.
- **Tolerant editing**: `edit_file` removes copied line number prefixes and code block fences, ignores trailing whitespace, and when nothing matches, shows the lines that match with different indentation so the model can copy them exactly.
- **Placeholders are refused**: `// ... existing code ...` and similar comments would replace real code, so writes containing them are rejected. A whole-file write that shrinks a file to less than half is warned about.
- **Hash-checked edits that chain**: every edit needs the SHA-256 from the last read, and the edit response gives the new one, so the model can make the next edit without reading the file again.
- **Responses formatted for the console too**: header lines as a list and content in code blocks, so they read well both as Markdown and as plain text.

## 5. Errors that say what happened and what to do next

- **Every error names the next step**, with the tool and its arguments filled in where they are known, e.g. `read_file_with_metadata(path='src/app.py', section=...)`.
- **A failed change says that nothing changed**: `Nothing was changed.`, and for a failed edit `expected_sha256 is still valid`, so the model corrects its text and retries instead of re-reading or rebuilding the file. Where it is not certain, the error says the change may have been partly made and how to check it. A refused command says `The command was not run.`
- **Recovery through errors works**: when asked to extend an existing plan, the model first tried to overwrite it, got "already exists … give the SHA-256 … or change only part of it with edit_file", and then edited the plan, in 7 of 8 runs.
- **Forgive common argument mistakes**: an argument sent as `null` uses its default, and a search for text that is not a valid regex, such as `foo(`, is searched for as literal text, with a note.
- **Refuse the mistakes a merged tool makes possible**: `write_file` with a hash for a missing file (usually a mistyped path) and without a hash for an existing file are both refused.

## 6. Finding code without the exact words

- **A relevance search** for when the model knows what it wants but not the exact text or file: the words and names of a sentence, code or error message are scored with BM25, so a rare name weighs much more than a common word.
- **Names are split into their parts** (`getUserName`: `get`, `user`, `name`), so camelCase and snake_case match each other.
- **The keywords that were not found are listed**, which often points to a misspelled or invented name.
- **The two text searches point to each other**: every use of a name comes from the exact search, the best places from the relevance search.

## 7. Undo instead of rebuilding from memory

- **Every change of the file tools can be undone with `restore_file`**, newest first, also across moves and deletions. Rebuilding a broken file from memory is what just went wrong, so the instructions tell the model to restore instead.
- **Deleting moves to a backup folder**, and the response says how to undo it.
- **No change without a backup**: if the backup fails, the change is not made.

## 8. Guard rails for a model that makes mistakes

- **One project folder** that every path must stay in, **forbidden paths** hidden from all tools, **allowed paths** inside forbidden folders for the AI's own files, and **read-only files** for the instructions.
- **Protected files**: the MCP configuration, the tools config and the command registry can never be read or changed, so the model cannot remove its own restrictions.
- **Only registered commands run**, without a shell, with the model's argument always as one argument and options starting with `-` refused.
- **The model can only suggest new commands**: the server writes the suggestion to a file in valid JSON, and the user reviews and adds it.
- **No git write tools**: a person reviews and commits the changes.

## 9. Commands that suit a small model

- **Run one test file or one test** before the whole suite: faster, and shorter output.
- **Rerun only the tests that failed last time.**
- **Look up the real API** of an installed library (`pydoc`) or package (`npm view`) instead of guessing signatures.
- **Run Python tools as `python -m <tool>`**, so they are found in the same environment as the project's packages.

## 10. Instructions and the session

- **Choose a mode first**: answering, planning or implementing, said at the start of the reply. With a single implementation workflow, the model started implementing when asked only for a plan.
- **Give each mode examples of the requests that mean it**, and say what the mode produces: with "fix…", "add…", "change…" as examples, and "such a request is the permission", gemma4 implemented direct requests more often, and with "save a plan in a file with dev_write_file" in the planning mode itself, it saved new plans in 3 of 3 runs instead of 0 of 3.
- **Do not end a reply by announcing the next step**: a model that writes "First, I'll search for…" and stops leaves the user to type "continue". The instructions say to call the tool instead.
- **Concrete steps instead of general advice**: outline, read sections, edit with the hash, check the outline, run single tests, review the diff. Small models follow steps better than "understand the project first".
- **Short instructions**, about 2 000 tokens, as they are in the context all the time.
- **Plans in a folder of their own**, checked before planning and implementing, and not committed, so that another person's AI does not take them as its own.
- **A system prompt that names the first step keeps the tool calls working**: broken calls went from 3 of 24 requests to none.
- **Ask the AI to read the instructions at the start of every session**, also after `/clear`: when the first message is already a task, the system prompt alone got the instructions read in only 2 of 18 runs. With the system prompt and "Read the project instructions." as the first message, in 20 of 20; with that message alone, in 9 of 20.
- **A large enough context** (`num_ctx` 32 000): with Ollama's default of 4 096 tokens the tool definitions alone fill it and the calls break.
- **`/clear` after a broken call**: ollmcp keeps it in the history, and the model repeats it.

## Lessons from testing with a local model

- **Measure instead of guessing.** Replaying the same request against Ollama with the real tools, over several seeds, showed effects that single sessions hide. The scripts set up a fresh project, execute the model's tool calls on the real server and count the outcomes of each run.
- **Size matters**: broken tool calls became more frequent as the tool definitions grew.
- **A concrete tool call in the instructions is followed**; a rule about how to do something is followed less reliably, and can make things worse.
- **More rules are not always better**: five rewrites of the planning instructions all did worse than the original, as the model hesitated and gave the plan in its reply instead of saving it.
- **Good error messages are part of the design**: a refused action with a clear next step was enough for the model to recover.
- **The model goes straight to the task**: anything that must happen first has to be asked for, not just written in a system prompt.
- **Test whole tasks, and check the result**: replaying tasks such as "plan this", "change the plan" and "fix this", and checking afterwards which files changed and whether hidden tests pass, showed what single first calls cannot, e.g. a model that claimed "I updated the plan" without touching the file.
- **Choose the model by its mistakes, not only by its strengths**: qwen3-coder used the tools better, but its mistakes were silent (code changed in planning mode, work claimed but not done); gemma4's showed in its reply and cost one message to correct.
- **Instruction changes interact**: in a draft that also said that saving a plan needs no permission, gemma4 planned instead of fixing in every run, so each change was measured in the scenarios it could affect, also the ones it was not meant for.
