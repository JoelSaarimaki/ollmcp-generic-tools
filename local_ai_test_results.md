# Local AI test results

Results of running the tools with a local model, qwen3-coder 30b, through Ollama: how the model behaves with the tool definitions, the system prompt and the project instructions. These are not the tests of the toolset itself, which are in `tests/` and described in [DEVELOPMENT.md](DEVELOPMENT.md#how-changes-have-been-verified): those show that the tools behave as designed, these show what a model does with them.

The design decisions that followed from the results are in [DEVELOPMENT.md](DEVELOPMENT.md), and what users should do in the [README](README.md#starting-a-session).

## Setup

- Model `qwen3-coder:30b`, Ollama 0.34.1, ollmcp 0.35.0, on the developer's Windows machine.
- The requests were replayed with scripts, not typed in ollmcp, so that the same request could be repeated with other settings. The scripts started the tools' server over stdio, took its tool definitions with `list_tools()` and turned them into the request payload with ollmcp's own `build_tool_payload` (`mcp_client_for_ollama.tools.manager`), which gives the `dev_<tool>` names the model sees in ollmcp.
- Requests were sent with `ollama.chat(model="qwen3-coder:30b", messages=..., tools=..., options={"num_ctx": 32000, "seed": n})`, or through any-llm's `AnyLLM.create("ollama").acompletion(..., stream=True)`, as ollmcp does. A parsed tool call is in `message.tool_calls`; a broken one shows up as `<function=` in `message.content`.
- Where the model's tool calls were executed, the scripts called the real server, in a temporary project set up with `python -m ollmcp_tools init`, and gave the model the responses, up to 20 steps per run.
- Prompt sizes come from `prompt_eval_count`, with a unique system message per request so that Ollama's prompt cache does not hide tokens. The size of the tool definitions is the difference to the same request without tools.
- Runs with the same seed repeat exactly when nothing else changes. Tool responses contain timestamps, so runs that execute tools vary even with the same seed; results are therefore given as counts over several seeds.
- The scripts are in `local_ai_tests/` (see its README), which is gitignored: it is kept in the developer's own copy, not in every clone. The results of 2026-10-19 and the first results of 2026-10-20 were measured with earlier versions of the same scripts; each section names the command that repeats it.

## Choosing a mode (2026-10)

With instructions that were a single implementation workflow, qwen3-coder 30b started implementing when asked only for a plan. The instructions template therefore starts by making the model choose a mode, answering, planning or implementing, and say it (`Mode: planning`). This was observed in use, not counted.

## Broken tool calls (2026-10-19)

The model sometimes writes a tool call without its opening `<tool_call>` tag: `<function=dev_read_context_file>…</function></tool_call>`. Ollama's parser then passes it on as a plain answer, so the call never reaches the server, and ollmcp's Markdown rendering hides the closing tags, so it looks like they are missing too. [broken_output_example.md](broken_output_example.md) is such a session.

| Setting | Parsed calls |
|---|---|
| 26 tools (about 4 200 prompt tokens), no system prompt, "Read the task in the context files and act accordingly" | 0/3 |
| 24 tools (about 3 900 tokens) or fewer, same request | 3/3 |
| 26 tools, six requests, four seeds, no system prompt | 21/24 |
| 22 tools, same requests | 23/24 |
| 26 tools, system prompt *At the start of a session, call dev_read_context_file to read the project instructions and plans.* | 24/24 |
| 26 tools, system prompt "You are a helpful assistant." | no better than without |

Further findings:
- **The failures grow with the size of the tool definitions**, but fewer tools make them rarer, not impossible.
- **`num_ctx` must be large enough.** Without it, Ollama uses 4 096 tokens and cuts the prompt, which causes the same failure. ollmcp asks for 32 000 unless `num_ctx` is set in `/model-config`.
- **qwen3-coder's prompt template does not show schema defaults**: the prompt has the same length without them. The "Defaults to …" lines in the tool descriptions are the model's only source for them.
- **The docstring indentation cost about 90 tokens**, as it was sent to the model as it is. It is now removed by the server; the token counts above were measured before that.
- **ollmcp keeps a broken call in the chat history** as a plain answer, and the model then repeats it. `/clear` after one.

On 2026-10-20, with 19 tools and the current system prompt (see [Session start and plans](#session-start-and-plans-2026-10-20)), none of the 38 first calls measured was broken; each of the other prompts tried then had at least one.

Repeat with `first_call.py`, which counts broken first calls: e.g. `python local_ai_tests/first_call.py --prompt "" --request "Read the task in the context files and act accordingly"`. The toolset has changed since, so the numbers will differ.

## Size of the tool definitions (2026-10-20)

Measured while reducing the toolset, with every tool served (an Ollama API key set, so the `web` tools included) and without the `web` tools:

| Toolset | Characters of the definitions | Prompt tokens, every tool | Prompt tokens, without `web` |
|---|---|---|---|
| 26 tools, before 2026-10-20 | 11 307 | 4 109 | 3 855 |
| 23 tools: `create_file` merged into `write_file`, `get_git_status` into `get_git_diff`, `list_directory` dropped | 10 659 | 3 841 | 3 585 |
| 19 tools: the `context` toolset replaced by the file tools | 9 203 | 3 327 | 3 071 |

A request without tools took 53–62 prompt tokens. Repeat with `python local_ai_tests/measure_tokens.py`.

## Session start and plans (2026-10-20)

Prompted by a session in which the model, asked to "make sure that plan includes" something, wrote a new plan with the name of the existing one and got an error, instead of checking the plans first.

### Does the system prompt get the instructions read?

The first tool call of the model, with three requests that start a session with a task ("Make sure that the plan includes locking the account after five failed login attempts.", "Fix the bug where an empty password is accepted.", "How does the login work?") and six seeds each:

| System prompt | First call reads `instructions.md` |
|---|---|
| *At the start of a session, call dev_read_file_with_metadata with path '.ollmcp-tools/instructions.md' and follow it.* (current) | 2/18 |
| *Before you reply to the user's first message, call dev_read_file_with_metadata with path '…' and follow the project instructions in it.* | 1/18 |
| *Your first action in every conversation, before any other tool call, is to call dev_read_file_with_metadata with path '…'. Then follow those instructions for every request.* | 1/18 |
| *The project instructions are in '…'. If you have not read them in this conversation yet, call dev_read_file_with_metadata with path '…' first, before any other tool, and follow them.* | 7/18 |

The other first calls were mostly `get_outline`, `get_tools_config` and `search_relevant_files`, and some broken calls. Without the instructions, the model also changed code when asked to change a plan: in three of four full runs of the plan request it edited `src/login.py` and wrote tests.

The 24/24 of 2026-10-19 was measured with "Read the task in the context files and act accordingly" as the first message, which asks for the same thing as the prompt. When the first message is a task, the model goes straight to the task. ollmcp uses only its own system prompt, not the instructions an MCP server can send, and has one global configuration, not one per project.

Starting the session by asking for the instructions, with "Read the project instructions." or "Read the project instructions in .ollmcp-tools/instructions.md." as the first message, ten seeds each:

| System prompt | First call reads `instructions.md` |
|---|---|
| The current one | 20/20 |
| None | 9/20 (the others: `get_tools_config` 10, broken 1) |

**Conclusion:** keep the system prompt and start each session with "Read the project instructions." Neither is enough alone.

Repeat with `python local_ai_tests/first_call.py`, with `--prompt` for the system prompts to compare and `--request` for the first messages.

### Changing an existing plan

Full runs with the instructions already read, as if the system prompt had worked. The project had one plan, `.ollmcp-tools/context/plan-login.md`, about validating the login form, and the request was "Make sure that the plan (also) includes locking the account after five failed login attempts.":
- **Same session**: the plan was made earlier in the same session, as in the reported case.
- **Fresh session**: the session started with the request.

| Instructions | Same session: plan updated | Fresh session: plan updated | Fresh session: new plan written beside it |
|---|---|---|---|
| Committed instructions | **8/8**, 7 of them after a refused `write_file` | 0/6 | 6/6 |
| 1: a first step for every request, "call dev_get_outline with path='.ollmcp-tools/context'", and requests such as "make sure the plan includes…" named as meaning an existing plan, changed with `edit_file` | not run | 4/6 | 2/6 |
| 2: as 1, and the planning step "Save the plan in its file", with "Never give a plan only in your reply" | not run | 1/8 | 8/8 |
| 3: as 2, and "it means an existing plan file, even if the request adds a new topic to it" | 11/16 | 0/8 | 7/8 |
| 4: as 3, and "change it with dev_edit_file before you reply. Writing the changed plan only in your reply does not change the file." | 6/10, one run also changed code | not run | not run |
| 5: as 3, but the whole changed plan saved with `write_file` and the SHA-256 from the read | 6/10 | 2/8 | 3/8 |

Findings:
- **The committed instructions handle the reported case.** The model first tries to write the whole plan with `write_file`, gets `file_exists` ("Nothing was changed. … already exists. To replace it, give the SHA-256 from read_file_with_metadata as expected_sha256, or change only part of it with edit_file."), and then edits the plan. The error the user saw was this recovery, and it worked in every run.
- **A fixed first call is followed**: every new version listed the plans first in every run.
- **Rules about how to change a plan made the model hesitate.** The runs that did not update the plan read it and then gave the updated plan in the reply without saving it, which the committed instructions never did.
- **In a fresh session, the model treats a request on a new topic as a new plan**, even when told that "the plan" means the existing one. As nothing in the session tied the request to that plan, this is defensible.

**Conclusion:** the committed instructions were kept.

Repeat with `python local_ai_tests/replay.py local_ai_tests/scenarios/plan_update.json --pre-read` for the same session, adding `--no-history` for the fresh session and `--instructions <file>` to test a draft of the instructions.
