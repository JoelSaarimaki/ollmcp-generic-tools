# Local AI test results

Results of running the tools with local models through Ollama, qwen3-coder 30b and, since 2026-10-23, also gemma4 26b, the recommended model until 2026-11-02 (see [qwen3-coder 30b and gemma4 26b compared](#qwen3-coder-30b-and-gemma4-26b-compared-2026-10-23)), on 2026-10-31 gpt-oss 120b (see [gpt-oss 120b compared with gemma4](#gpt-oss-120b-compared-with-gemma4-2026-10-31)), and on 2026-11-02 flint 40b, now the recommended model, glm-4.7-flash and qwen3.8 27b (see [flint 40b, glm-4.7-flash and qwen3.8 compared with gemma4 on the GPU](#flint-40b-glm-47-flash-and-qwen38-compared-with-gemma4-on-the-gpu-2026-11-02)): how the model behaves with the tool definitions, the system prompt and the project instructions. These are not the tests of the toolset itself, which are in `tests/` and described in [DEVELOPMENT.md](DEVELOPMENT.md#how-changes-have-been-verified): those show that the tools behave as designed, these show what a model does with them.

The design decisions that followed from the results are in [DEVELOPMENT.md](DEVELOPMENT.md), and what users should do in the [README](README.md#starting-a-session).

## Setup

- Model `qwen3-coder:30b`, Ollama 0.34.1, ollmcp 0.35.0, on the developer's Windows machine. On 2026-10-23 both `qwen3-coder:30b` and `gemma4:26b` were run with Ollama 0.40.9, fully on the CPU, as the machine's 8 GB GPU is too small for either. On 2026-10-31 `gpt-oss:120b` ran the same way, partly from the page file, as the machine has 64 GB of RAM. Ollama's GPU backend turned out to have been missing from 2026-10-23 (see [The same model on llama-server with mmap](#the-same-model-on-llama-server-with-mmap-2026-11-01)); from 2026-11-02, with Ollama 0.40.13, part of each model ran on the GPU.
- The requests were replayed with scripts, not typed in ollmcp, so that the same request could be repeated with other settings. The scripts started the tools' server over stdio, took its tool definitions with `list_tools()` and turned them into the request payload with ollmcp's own `build_tool_payload` (`mcp_client_for_ollama.tools.manager`), which gives the `dev_<tool>` names the model sees in ollmcp.
- Requests were sent with `ollama.chat(model="qwen3-coder:30b", messages=..., tools=..., options={"num_ctx": 32000, "seed": n})`, or through any-llm's `AnyLLM.create("ollama").acompletion(..., stream=True)`, as ollmcp does. Since 2026-10-23 `think=True` is added for models that can think, such as gemma4, as ollmcp turns thinking on for them. A parsed tool call is in `message.tool_calls`; a broken one shows up as `<function=` in `message.content`.
- Where the model's tool calls were executed, the scripts called the real server, in a temporary project set up with `python -m ollmcp_tools init`, and gave the model the responses, up to 20 steps per run (25 in the comparisons of 2026-10-23).
- Prompt sizes come from `prompt_eval_count`, with a unique system message per request so that Ollama's prompt cache does not hide tokens. The size of the tool definitions is the difference to the same request without tools.
- Runs with the same seed repeat exactly when nothing else changes. Tool responses contain timestamps, so runs that execute tools vary even with the same seed; results are therefore given as counts over several seeds.
- The scripts are in `local_ai_tests/` (see its README), which is gitignored: it is kept in the developer's own copy, not in every clone. The results of 2026-10-19 and the first results of 2026-10-20 were measured with earlier versions of the same scripts; each section names the command that repeats it. The scripts use flint:40b unless `--model` is given (gemma4:26b from 2026-10-23, qwen3-coder:30b before), so add `--model` to repeat the earlier measurements.

## Choosing a mode (2026-10)

With instructions that were a single implementation workflow, qwen3-coder 30b started implementing when asked only for a plan. The instructions template therefore starts by making the model choose a mode, answering, planning or implementing, and say it (`Mode: planning`). This was observed in use, not counted. Saying the mode was dropped on 2026-10-27, as gemma4 keeps the modes apart without it (see [Choosing the mode without saying it](#choosing-the-mode-without-saying-it-2026-10-27)).

## Broken tool calls (2026-10-19)

The model sometimes writes a tool call without its opening `<tool_call>` tag: `<function=dev_read_context_file>…</function></tool_call>`. Ollama's parser then passes it on as a plain answer, so the call never reaches the server, and ollmcp's Markdown rendering hides the closing tags, so it looks like they are missing too. Such a session is kept in the developer's own copy, in `local-only/broken_output_example.md`, which is not committed.

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
| Implement the approved plan (implementing) | 2/3 pass the hidden tests; 1 run, told "Continue." after a stall, generated 25 800 tokens of empty output for 78 minutes | 2/3 pass; 1 run spent 25 steps with a wrong test, left `temp_debug.py` |
| "Fix the bug…" (implementing) | **0/3 implemented**: wrote a plan and asked for permission each time | 3/3 fixed, tested and reviewed the diff |

Over all 21 runs each:

| | gemma4 26b | qwen3-coder 30b |
|---|---|---|
| Code changed when the mode forbade it | 0 runs | 3 runs |
| Tool calls per run | 3.1 | 9.1 |
| Tool errors per run | 0.0 | 1.1 (mostly a guessed `run_python_tests` before listing the commands) |
| Ran the tests / reviewed the diff (of 6 implementing runs) | 2 / 1 | 6 / 3 |
| Stalls ("Continue." needed) | 5 | 0 |
| Broken output | `<\|tool_response>` as the whole answer once, `<channel\|>` leaked once | a trailing `<tool_call>` after a finished answer twice |

Findings:
- **Neither model read the approved plan before implementing it, or marked its steps done.**
- **gemma4 understands what is asked, but stops short.** It never changed code without permission, handled the plan change best, and its plans foresaw the negative total of a fixed discount. But it treats "Fix the bug" as a request for a plan, gives new plans in the reply instead of saving them in `.ollmcp-tools/context`, often reads one section where three files are needed, and stops after announcing a step.
- **qwen3-coder uses the tools well, but does not respect the modes.** It reads every relevant file, verifies its changes with the tests and the diff, and gets correct code. But in planning mode it went on to implement, and it claimed to have updated a plan it never touched, which is harder to notice than gemma4's failures.
- **gemma4's failures are visible and cheap to correct** (a "yes, implement it" or "save the plan"), apart from the rare template-token answers and the one runaway generation. qwen3-coder's are silent: unrequested code changes, and claims of work not done.
- 3 seeds per scenario is a small sample: the counts show tendencies, not rates.

**Conclusion:** gemma4 26b became the recommended model (flint 40b since 2026-11-02, see the README's [Choosing the model](README.md#choosing-the-model)), and the instructions template was changed for its mistakes (see [Instructions for gemma4](#instructions-for-gemma4-2026-10-23)). The comparison was run with the template before those changes.

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

## Choosing the mode without saying it (2026-10-27)

Whether gemma4 needs to start its reply with `Mode: …` to keep the modes apart, or whether choosing the mode is enough. The suspicion was that saying the mode satisfies the model more than acting in it does: in the comparison above, gemma4 updated the plan correctly without any "Mode:" line, and asked "Fix the bug where…" it mostly said "Mode: planning" and then stayed there.

**Setup.** As in [Instructions for gemma4](#instructions-for-gemma4-2026-10-23), with gemma4 26b on the CPU, comparing the template (draft 3) with **draft 5**: the same template with "and start your reply with `Mode: answering`, `Mode: planning` or `Mode: implementing`" removed, so it only says "Choose the mode from the user's request before you call any other tool." In draft 5's runs, the scenarios' earlier assistant replies were given without their "Mode: planning" line, so that the history did not teach the line. Draft 3's runs are those of the previous section. Seeds 1 to 3 for every scenario, and 4 and 5 for "What should we change?" and "Fix the bug…" with draft 5.

The mode is now checked from what the model did, not from what it said: code changed is implementing, only a plan written is planning, nothing written is answering. Counts of runs:

| Scenario: what is counted | Draft 3 (template before) | Draft 5 (no "Mode:" line) |
|---|---|---|
| Why is SAVE10 not applied: acted in the answering mode, correct answer | 3/3, 3/3 | 3/3, 3/3 |
| How is the total calculated: acted in the answering mode / all 3 files read | 3/3 / 0/3 | 3/3 / 0/3 |
| "What should we change?": no code changed | 3/3 | **5/5** |
| Plan fixed-amount codes: plan saved in a file, no code changed | 3/3 | 3/3 |
| Change the plan: plan updated, no code changed | 3/3 | 3/3 |
| Implement the approved plan: hidden tests pass | 3/3 | 3/3 |
| "Fix the bug…": fixed, hidden tests pass | 1/3 | **3/4** |
| All: acted in the right mode | 19/21 | 23/24 |
| All: code changed when the mode forbade it | 0 | **0** |
| All: stalls ("Continue." needed) | 3 | **0** |
| All: leaked template tokens (`<channel\|>`) | 3 | 0 |

Findings:
- **gemma4 keeps the modes apart without saying the mode.** It never changed code when asked a question or for a plan, saved and updated plans as before, and implemented approved plans correctly.
- **What gemma4 said and what it did differed.** Rescored by its actions, one of draft 3's "Fix the bug…" runs had said "Mode: planning" and then fixed the bug. The line is therefore not a reliable sign of the mode, and the checks no longer count it.
- **Direct fixes did not get worse, and probably better**: fixed in 3 of 4 runs, against 1 of 3. The one miss was the familiar one: a plan in the reply and a question whether to implement it, with no file changed. Four runs are too few to call it a real improvement.
- **No stalls and no leaked tokens without the line.** With it, a reply began with prose ("Mode: planning\n\nI will now examine…"), the pattern of a reply that ends after announcing a step; without it, the replies started with the tool call. This fits, but three stalls against none is a small count.
- The runs were interrupted twice when the machine ran low on memory, and continued from the saved runs; the extra seeds 4 and 5 were not run with draft 3.

Draft 5 is the template since 2026-10-27. `compare_models.py` reports both the mode the model acted in ("acted in the right mode") and, if the instructions ask for it, the mode it said; it leaves the "Mode:" line out of the scenarios' earlier replies when the instructions do not ask for it. Repeat with `python local_ai_tests/compare_models.py --models gemma4:26b --seeds 1,2,3`, with `--instructions` for a draft.

## gpt-oss 120b compared with gemma4 (2026-10-31)

Whether a larger model avoids the mistakes of gemma4 26b and qwen3-coder 30b: gpt-oss 120b, a mixture-of-experts model with 5.1B active parameters, about 65 GB in Ollama.

**Setup.** As in [qwen3-coder 30b and gemma4 26b compared](#qwen3-coder-30b-and-gemma4-26b-compared-2026-10-23), with the instructions template of 2026-10-27 (draft 5), Ollama 0.40.9 and thinking on. The model ran fully on the CPU: Ollama put none of it on the 8 GB GPU, and it did not fit in the machine's 64 GB of RAM next to the other applications, so about 15 GB of it was in the page file throughout. The 7 scenarios ran with seeds 1 to 3, and "What should we change?" and "Plan fixed-amount codes" also with seeds 4 and 5, for both models. gemma4's seeds 1 to 3 (and 4 and 5 of "What should we change?") are the draft 5 runs of [Choosing the mode without saying it](#choosing-the-mode-without-saying-it-2026-10-27).

Two things were fixed during the comparison:
- **The script sent "Continue." to a reply that asked for approval.** gpt-oss ended a plan given in its reply with "If you approve this plan, I will proceed to implement the changes.", which the stall check took for an announced step, as it did not end with "?". The model took "Continue." as the approval and implemented the plan. The check now treats such a reply ("if you approve", "if you'd like", …) as asking the user, and the run was repeated. None of the 14 other "Continue." messages in the saved runs of all models was such a reply.
- **gpt-oss asked for top-level variables as sections** (`section="CODES"`, `"VAT_PERCENT"`, `"docstring"`), which were not section names: the outline names such a block `docstring, assignments: CODES`. 11 of its 13 tool errors in the first 21 runs were these; it always recovered with `get_outline`. A block of top-level Python statements is now also found by a name it assigns or by one of its parts. The seeds 4 and 5 and the repeated run were made with this change, and gpt-oss read `section="CODES"` successfully in two of them.

| Scenario: what is counted | gpt-oss 120b | gemma4 26b |
|---|---|---|
| Why is SAVE10 not applied: acted in the answering mode, correct answer | 3/3, 3/3 | 3/3, 3/3 |
| How is the total calculated: acted in the answering mode / all 3 files read | 3/3 / **3/3** | 3/3 / 0/3 |
| "What should we change?": no code changed | 4/5 | **7/7** |
| Plan fixed-amount codes: plan saved in a file, no code changed | 5/5 | 5/5 |
| Change the plan: plan updated, no code changed | 3/3 | 3/3 |
| Implement the approved plan: hidden tests pass / plan read first | 3/3 / 2/3 | 3/3 / **3/3** |
| "Fix the bug…": fixed, hidden tests pass | **3/3** | 3/4 |
| Implementing runs that ran the tests | 6/6 | 6/6 |
| Implementing runs that reviewed the diff | 0/6 | **5/6** |
| Claims of work not done | 0 | 0 |
| Stalls / broken output | 0 / 0 | 0 / 0 |
| Seconds per run, all scenarios with seeds 1 to 3 | 505 | 397 |

Findings:
- **gpt-oss reads more widely when answering.** It read all three files for the question about the total in every run, which gemma4 never did, and fixed the bug directly in every run. When implementing, both models ran the tests every time, but only gemma4 reviewed the diff, and gemma4 read the approved plan first in every run, gpt-oss in 2 of 3.
- **It changed code once when asked only "What should we change?"**: it read the code, changed `>` to `>=`, and said so in its reply. This is qwen3-coder's mistake, in 1 of 5 runs instead of 2 of 3, and it is visible in the reply. gemma4 never changed code when the mode forbade it.
- **It never claimed work it had not done**, unlike qwen3-coder, and it had no stalls, leaked template tokens or broken calls.
- **Like gemma4, it never marked the steps of an implemented plan done.**
- **It was about 30 % slower than gemma4 over seeds 1 to 3, with 15 GB of it in the page file**, and 1.5 to 3.7 times slower in the extra seeds (250 to 870 s per run against 170 to 240 s). The paging also made the machine hard to use for other work: other applications had to be closed. With llama.cpp's `llama-server` mapping the model file (mmap) and the attention layers on the GPU, it would page less, but that was not measured.
- 3 to 5 seeds per scenario is a small sample: the one run with an unrequested change is the only difference in the modes.

**Conclusion:** gpt-oss 120b is a credible alternative to gemma4, not an improvement: it reads more widely when answering, but reviews its changes less, needs about 65 GB of memory instead of 18 GB, and once changed code unasked. gemma4 26b stays the recommended model.

Repeat with `python local_ai_tests/compare_models.py --models gpt-oss:120b --seeds 1,2,3`, and `--scenarios ambiguous_change,plan_new --seeds 4,5` for the extra seeds.

### The same model on llama-server with mmap (2026-11-01)

Whether llama.cpp's `llama-server`, which can map the model file into memory (mmap) instead of loading it, runs gpt-oss 120b faster or with less disruption than Ollama.

**Setup.** The `llama-server.exe` that comes with Ollama, with Ollama's model file, CPU only, a 32 000-token context and temperature 1.0, and the section lookup of the previous section. Three settings were needed:
- **`--chat-template-file`** with gpt-oss's own template (`chat_template.jinja` from OpenAI's model repository): Ollama's model file has no template for llama-server, as Ollama renders the prompt itself, and without it the tools were not in the prompt at all.
- **`--no-repack`**: by default the CPU backend copies the weights into its own memory for faster arithmetic, which took 58 GB of private memory, as much as Ollama. Without it, the server had 1.6 GB of private memory and the weights stayed pages of the file, which Windows can drop without writing them to the page file.
- `--load-mode mmap`, the default made explicit.

The GPU could not be used: Ollama's CUDA backend (`lib/ollama/cuda_v12`) had been missing since an interrupted update on 2026-10-23, which also means that Ollama ran every model on the CPU since then for that reason, not only because the models do not fit in 8 GB. The harness sends the requests to the server's OpenAI-compatible API with `compare_models.py --llama-server http://127.0.0.1:8080`. The 7 scenarios ran with seeds 1 to 3.

| | llama-server, mmap | Ollama (2026-10-31) |
|---|---|---|
| Private memory of the server | 1.6 GB | 63 GB |
| Seconds per run | 507 | 505 |
| Acted in the right mode | 20/21 | 20/21 |
| "What should we change?": code changed | 1 of 3 | 1 of 3 |
| Hidden tests pass (implementing) | 6/6 | 6/6 |
| Read the approved plan first | 3/3 | 2/3 |
| "Fix the bug…": ran the tests | 1 of 3 | 3 of 3 |
| Tool errors per run | 0.0 | 0.6 |

Findings:
- **mmap changed how the memory is used, not how much.** The used parts of the model still filled the RAM as the runs went on, and Claude Code's low-memory check stopped a run started from it after two scenarios; the rest ran in the user's own terminal. The difference is that Windows can take the pages back without writing them to the page file.
- **No speed difference**: 507 against 505 seconds per run, about 10 tokens per second of generation once the model's pages were in memory. On the CPU, the speed is limited by the memory bandwidth either way.
- **The model behaved the same**: one unasked fix for "What should we change?" in each runtime, correct code in every implementing run, no claims of work not done. Two of the three direct fixes were not tested before the reply, which did not claim they were. The prompt was rendered by a different template than Ollama's, so small differences are not attributable to the runtime.
- **No tool errors**: the section names gpt-oss guesses (`section='CODES'`) now work.

**Conclusion:** on this machine without the GPU, llama-server with mmap gives no speed and no quality gain over Ollama; its only advantage is that the memory is easier to take back for other applications. Whether the GPU, for the attention layers with `-ngl 99 --n-cpu-moe 36`, makes a difference can be measured now that Ollama's CUDA backend is restored (2026-11-02); it has not been yet.

Repeat by starting the server with `llama-server.exe -m <model file> -c 32000 -np 1 --jinja --chat-template-file local_ai_tests/templates/gpt-oss-120b.jinja --temp 1.0 --load-mode mmap --no-repack`, then `python local_ai_tests/compare_models.py --llama-server http://127.0.0.1:8080 --models gpt-oss-120b-llama-server-cpu --seeds 1,2,3`.

## flint 40b, glm-4.7-flash and qwen3.8 compared with gemma4 on the GPU (2026-11-02)

Whether one of three newer models avoids gemma4 26b's mistakes: flint 40b (Recursive, a mixture-of-experts model with 2.5B active parameters, NVFP4, 22 GB), glm-4.7-flash (30B with 3B active, q8_0, 31 GB) and qwen3.8 27b (dense, Q4_K_M, 17 GB).

**Setup.** As in [gpt-oss 120b compared with gemma4](#gpt-oss-120b-compared-with-gemma4-2026-10-31), with the draft 5 template, thinking on and each model's own default temperature, but on Ollama 0.40.13, reinstalled with its CUDA backend, so the 8 GB GPU was used again: gemma4 ran 80 % on the GPU with a short prompt, glm-4.7-flash 18 % and qwen3.8 25 % with the 32 000-token context. gemma4 was run again for the baseline, as its earlier runs were on the CPU only. The 7 scenarios ran with seeds 1 to 3. qwen3.8 was stopped after 18 runs ("Fix the bug…" not run), as it was too slow to be an option.

| Scenario: what is counted | flint 40b | glm-4.7-flash | gemma4 26b | qwen3.8 27b |
|---|---|---|---|---|
| Acted in the right mode | **21/21** | 20/21 | 19/21 | 17/18 |
| "What should we change?": no code changed | 3/3 | 3/3 | 3/3 | 3/3 |
| Plan fixed-amount codes / change the plan: no code changed | 6/6 | 6/6 | 6/6 | 6/6 |
| Implement the approved plan: hidden tests pass | 3/3 | **1/3** | 3/3 | 3/3 |
| "Fix the bug…": fixed, hidden tests pass | **3/3** | 2/3 | 1/3 | – |
| Read the needed files first | **21/21** | 21/21 | 17/21 | 17/18 |
| How is the total calculated: all 3 files read | 3/3 | 3/3 | 0/3 | 3/3 |
| Implementing runs that ran the tests / reviewed the diff | 6/6 / 6/6 | 5/6 / 4/6 | 4/6 / 4/6 | 3/3 / 3/3 |
| Explore calls before a change | 5.9 | 3.9 | 2.7 | 4.2 |
| Project tests pass at the end | 21/21 | 20/21 | 21/21 | 18/18 |
| Stalls / broken calls / writes the mode forbids | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Seconds per run | 111 | 150 | **52** | 389 |

Findings:
- **The GPU made gemma4 about four times faster**: 52 seconds per run against about 200 on the CPU (draft 5). Its behaviour was as before, with the same weak spot: given "Fix the bug…", it answered or wrote a plan in 2 of 3 runs instead of fixing.
- **flint 40b made none of the counted mistakes.** It fixed directly when asked to, read the needed files and the plans before acting, ran the tests and reviewed the diff in every implementing run, and passed every hidden test. 7 of its 9 tool errors were the lint, format and type-check commands of the test project, which fail there because ruff and mypy are not installed; the other two were `edit_file` calls without a path. It explores twice as much as gemma4 and is about twice as slow, but stays at about 2 minutes per run.
- **glm-4.7-flash kept the modes apart but implemented badly.** In one run it gave no discount at all on a small order instead of clamping it, changed its own failing test to match the code, and replied that the discount was clamped: a claim of work not done, which the hidden tests caught. In another it kept editing `cart.py` with failing tests until the 25-step limit and left the project's tests failing. With q8_0 it is mostly on the CPU and slower than flint.
- **qwen3.8 27b behaved well but is too slow**: a dense model mostly on the CPU, 3 to 17 minutes per planning or implementing run. Once it answered a request to change the plan without reading it.
- 3 seeds per scenario is a small sample; flint's lead is in the scenarios where gemma4 is known to be weak.

**Conclusion:** flint 40b is the strongest model measured so far on this machine, and the only one without a counted mistake, at about twice gemma4's time per run and 22 GB instead of 18 GB. It is the recommended model since 2026-11-02 (see the README's [Choosing the model](README.md#choosing-the-model)); gemma4 26b stays the faster alternative. glm-4.7-flash and qwen3.8 are not options.

Repeat with `python local_ai_tests/compare_models.py --models flint:40b --seeds 1,2,3` (and `gemma4:26b`, `glm-4.7-flash:q8_0`, `qwen3.8:27b`).

### Session start and tool definitions with flint

The measurements of [Session start with gemma4](#session-start-with-gemma4-2026-10-23) repeated with flint 40b at its default temperature of 1.0:

| Setup | First call reads `instructions.md` |
|---|---|
| The current system prompt, first message "Read the project instructions." or "Read the project instructions in .ollmcp-tools/instructions.md." | 10/10 |
| No system prompt, same first messages | 5/10 (the others: `get_outline` 4, `get_tools_config` 1) |
| The current system prompt, the three tasks as the first message | 4/12 (the others: `get_tools_config` 4, `get_outline` 4) |

None of the 32 first calls was broken. The README's advice holds for flint as well. Its tool definitions take 2 763 prompt tokens with all 19 tools and 2 548 without the `web` tools, about as many as with gemma4.

Repeat with `python local_ai_tests/first_call.py --model flint:40b --temperature 1.0`, with `--prompt` and `--request` as above, and `python local_ai_tests/measure_tokens.py --model flint:40b`.
