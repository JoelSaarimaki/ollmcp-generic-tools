# Local AI test results

Results of running the tools with local models through Ollama, qwen3-coder 30b until 2026-10-20 and since then also gemma4 26b, which is now the recommended model (see [qwen3-coder 30b and gemma4 26b compared](#qwen3-coder-30b-and-gemma4-26b-compared-2026-10-23)): how the model behaves with the tool definitions, the system prompt and the project instructions. These are not the tests of the toolset itself, which are in `tests/` and described in [DEVELOPMENT.md](DEVELOPMENT.md#how-changes-have-been-verified): those show that the tools behave as designed, these show what a model does with them.

The design decisions that followed from the results are in [DEVELOPMENT.md](DEVELOPMENT.md), and what users should do in the [README](README.md#starting-a-session).

## Setup

- Model `qwen3-coder:30b`, Ollama 0.34.1, ollmcp 0.35.0, on the developer's Windows machine. From 2026-10-23 also `gemma4:26b`, with Ollama 0.40.9; both models then ran fully on the CPU, as the machine's 8 GB GPU is too small for either.
- The requests were replayed with scripts, not typed in ollmcp, so that the same request could be repeated with other settings. The scripts started the tools' server over stdio, took its tool definitions with `list_tools()` and turned them into the request payload with ollmcp's own `build_tool_payload` (`mcp_client_for_ollama.tools.manager`), which gives the `dev_<tool>` names the model sees in ollmcp.
- Requests were sent with `ollama.chat(model="qwen3-coder:30b", messages=..., tools=..., options={"num_ctx": 32000, "seed": n})`, or through any-llm's `AnyLLM.create("ollama").acompletion(..., stream=True)`, as ollmcp does. A parsed tool call is in `message.tool_calls`; a broken one shows up as `<function=` in `message.content`.
- Where the model's tool calls were executed, the scripts called the real server, in a temporary project set up with `python -m ollmcp_tools init`, and gave the model the responses, up to 20 steps per run.
- Prompt sizes come from `prompt_eval_count`, with a unique system message per request so that Ollama's prompt cache does not hide tokens. The size of the tool definitions is the difference to the same request without tools.
- Runs with the same seed repeat exactly when nothing else changes. Tool responses contain timestamps, so runs that execute tools vary even with the same seed; results are therefore given as counts over several seeds.
- The scripts are in `local_ai_tests/` (see its README), which is gitignored: it is kept in the developer's own copy, not in every clone. The results of 2026-10-19 and the first results of 2026-10-20 were measured with earlier versions of the same scripts; each section names the command that repeats it. Since 2026-10-23 the scripts use gemma4:26b unless `--model` is given, so add `--model qwen3-coder:30b` to repeat the earlier measurements.

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

A request without tools took 53–62 prompt tokens. These are qwen3-coder's tokens: with gemma4 26b, measured on 2026-10-23, the 19 tools took 2 745 prompt tokens and the 17 without `web` 2 532, as its tokenizer needs fewer tokens for the same text. Repeat with `python local_ai_tests/measure_tokens.py`, adding `--model gemma4:26b` for gemma4.

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

## Session start with gemma4 (2026-10-23)

The measurements of [Does the system prompt get the instructions read?](#does-the-system-prompt-get-the-instructions-read) repeated with gemma4 26b, at its default temperature of 1.0, with five seeds for each first message and four for each task:

| Setup | First call reads `instructions.md` |
|---|---|
| The current system prompt, first message "Read the project instructions." or "Read the project instructions in .ollmcp-tools/instructions.md." | 10/10 |
| No system prompt, same first messages | 5/10 (the others: `get_outline` 5) |
| The current system prompt, the three tasks as the first message | 2/12 (the others: `get_outline` 6, the searches 4) |

gemma4 behaves like qwen3-coder: the README's advice, the system prompt and "Read the project instructions." as the first message, holds for it too. None of the 32 first calls was broken.

Repeat with `python local_ai_tests/first_call.py --model gemma4:26b --temperature 1.0`, with `--prompt` and `--request` as above.

## qwen3-coder 30b and gemma4 26b compared (2026-10-23)

Which of the two models suits the tools better: whether it keeps answering, planning and implementing apart, reads the code it needs before it acts, makes correct tool calls, and gets the result right.

**Setup.** Ollama 0.40.9, both models fully on the CPU (the 8 GB GPU is too small for either). Each model ran as in ollmcp: its own default temperature and thinking on (gemma4 returned no thinking text, and `think=True` behaved like ollmcp's `"medium"`). Every run started as the README recommends: the system prompt, "Read the project instructions." with the instructions read, then the request. The project was a small shop in three modules (cart, discount codes, tax) with a real bug: `discount_for()` compares with `>` instead of `>=`, so an order of exactly 100.00 € gets no SAVE10 discount. 7 scenarios, 3 seeds each, at most 25 steps. When a model ended its turn by announcing a step ("First, I'll search for…") without calling a tool, the script answered "Continue.", as a user would; this is counted as a stall. Code results were checked afterwards with hidden pytest tests.

| Scenario (expected mode) | gemma4 26b | qwen3-coder 30b |
|---|---|---|
| Why is SAVE10 not applied at 100.00 €? (answering) | 2/3 correct, 1 run answered only `<\|tool_response>` | 3/3 correct |
| How is the total calculated, tax before or after the discount? (answering, 3 files) | 3/3 correct, but read all 3 files in only 1 run | 3/3 correct, all 3 files read in every run |
| "What should we change?" for the bug (planning or answering) | 3/3 answered, no changes | 1/3 right, **2/3 wrote a plan and then implemented it unasked** |
| Plan fixed-amount codes (planning) | 3/3 no changes, **0/3 saved the plan**: it was given only in the reply | 3/3 saved the plan, no changes |
| Change the plan made earlier (planning) | **3/3 updated the plan** with `edit_file`, no changes, but no "Mode:" line | 0/3 right: 1 changed code, **2 replied "I updated the plan" without any tool call** |
| Implement the approved plan (implementing) | 2/3 pass the hidden tests; 1 run generated 25 800 tokens of empty output for 78 minutes | 2/3 pass; 1 run spent 25 steps with a wrong test, left `temp_debug.py` |
| "Fix the bug…" (implementing) | **0/3 implemented**: wrote a plan and asked for permission each time | 3/3 fixed, tested and reviewed the diff |

Over all 21 runs each:

| | gemma4 26b | qwen3-coder 30b |
|---|---|---|
| Code changed when the mode forbade it | 0 runs | 3 runs |
| Tool calls per run | 3.1 | 9.1 |
| Tool errors per run | 0.0 | 1.1 (mostly a guessed `run_python_tests` before listing the commands) |
| Ran the tests / reviewed the diff (of 6 implementing runs) | 2 / 1 | 6 / 3 |
| Stalls ("Continue." needed) | 4 | 0 |
| Broken output | `<\|tool_response>` as the whole answer once, `<channel\|>` leaked once | a trailing `<tool_call>` after a finished answer twice |

Findings:
- **Neither model read the approved plan before implementing it, or marked its steps done.**
- **gemma4 understands what is asked, but stops short.** It never changed code without permission, handled the plan change best, and its plans foresaw the negative total of a fixed discount. But it treats "Fix the bug" as a request for a plan, gives new plans in the reply instead of saving them in `.ollmcp-tools/context`, often reads one section where three files are needed, and stops after announcing a step.
- **qwen3-coder uses the tools well, but does not respect the modes.** It reads every relevant file, verifies its changes with the tests and the diff, and gets correct code. But in planning mode it went on to implement, and it claimed to have updated a plan it never touched, which is harder to notice than gemma4's failures.
- **gemma4's failures are visible and cheap to correct** (a "yes, implement it" or "save the plan"), apart from the rare template-token answers and the one runaway generation. qwen3-coder's are silent: unrequested code changes, and claims of work not done.
- 3 seeds per scenario is a small sample: the counts show tendencies, not rates.

**Conclusion:** gemma4 26b is the recommended model (see the README's [Choosing the model](README.md#choosing-the-model)), and the instructions template was changed for its mistakes (see [Instructions for gemma4](#instructions-for-gemma4-2026-10-23)). The comparison was run with the template before those changes.

Repeat with `python local_ai_tests/compare_models.py --seeds 1,2,3`; the transcripts of every run are saved with a `summary.md`.

## Instructions for gemma4 (2026-10-23)

After the comparison, the instructions template was changed for gemma4's mistakes and measured with `compare_models.py --models gemma4:26b --instructions <draft>`, three seeds per scenario, in the scenarios each draft could affect. Counts of runs out of 3:

| Scenario: what is counted | Template before | Draft 1 | Draft 2 | Draft 3 (adopted) | Draft 4 |
|---|---|---|---|---|---|
| Why is SAVE10 not applied: correct answer | 2 | 2 | | **3** | |
| How is the total calculated: all 3 files read | 1 | 0 | 0 | 0 | |
| "What should we change?": no code changed | 3 | 3 | 3 | 3 | |
| Plan fixed-amount codes: plan saved in a file | 0 | 0 | 1 | **3** | 3 |
| Change the plan: plan updated, no code changed, "Mode:" line | 0 | 3 | 2 | **3** | |
| Implement the approved plan: hidden tests pass | 2 | 3 | | **3** | |
| "Fix the bug…": fixed, hidden tests pass | 0 | 2 | 0 | 1 | 1 |

The drafts, each a change of the template:
- **Draft 1**: implementing says "Such a request is the permission: do the change, do not ask for it again", starts with "Call **dev_get_outline** with `path='.ollmcp-tools/context'`" and goes on implementing after writing a short plan "without asking"; planning says "Your reply only summarizes the plan: the plan itself must be in the file"; answering says to also read the functions in other files that the read code calls; and a rule says "Do not end your reply by saying what you will do next: call the tool instead."
- **Draft 2**: draft 1 without the reading rule, with "In planning mode, saving the plan in `.ollmcp-tools/context` is part of the task and needs no permission" in the rule against changing files, and step 3 as a concrete call: "call **dev_write_file** with a path in `.ollmcp-tools/context` … and the plan as `content` … Do this before you reply."
- **Draft 3**: draft 1 without the reading rule, with step 3 as in draft 2, the planning mode defined as "You examine the code and save a plan in a file in `.ollmcp-tools/context` with **dev_write_file**", and implementing with the examples ("fix…", "add…", "change…").
- **Draft 4**: draft 3 with "A fix or a small change needs no plan: make it right away" in the first implementing step.

Findings:
- **What a mode produces belongs in the mode's definition.** gemma4 saved a new plan only when the planning mode itself named the file and the tool (3 of 3 in drafts 3 and 4); the same words in the planning steps alone (drafts 1 and 2) saved 1 of 6.
- **gemma4 chooses the mode before it reads the steps.** Asked "Fix the bug where…", it mostly started with "Mode: planning", wrote a plan and asked for permission; a sentence in the implementing steps (draft 4) changed nothing. Draft 1 fixed it in 2 of 3 runs, but that difference is within the variation of three runs. When it asked, it implemented the plan correctly after the approval: the approved plan passed the hidden tests in 3 of 3 runs with drafts 1 and 3.
- **The reading rule did not help**: no draft got all three files read for the question about the total. Draft 3's answers to it named fewer of the expected points (5 of 9 keywords over three runs, 7 of 9 before).
- **Draft 2 made direct fixes worse** (0 of 3, and two runs made no tool call at all), so its sentence on permission was left out.
- With draft 3, gemma4 tries `dev_write_file` on an existing plan first, gets `file_exists` and then edits the plan, the recovery qwen3-coder showed in [Changing an existing plan](#changing-an-existing-plan).
- Leaked template tokens: with draft 3, `<channel|>` appeared in 3 of 21 runs, each time in an answer whose tool call still worked, and no answer was only a template token.

Draft 3 is the template since 2026-10-23, at about 2 000 prompt tokens with gemma4 (1 800 before). Repeat with `python local_ai_tests/compare_models.py --models gemma4:26b --seeds 1,2,3`, giving a draft with `--instructions`.
