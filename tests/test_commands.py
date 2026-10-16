# --- test_commands.py ---
"""
Tests for commands-mcp: running registered commands safely.
"""
import json
import sys

import pytest

from conftest import start_server, windows_only

PYTHON = f'"{sys.executable}"'

def registry(**commands) -> str:
    return json.dumps({name: {"description": "test", **info} for name, info in commands.items()})

@pytest.fixture
def commands(project):
    project.write("commands.json", registry(
        argv={"template": f'{PYTHON} -c "import sys; print(sys.argv[1:])" {{arg}}'},
        embedded={"template": f'{PYTHON} -c "import sys; print(sys.argv[1:])" --name={{arg}}'},
        fail={"template": f'{PYTHON} -c "import sys; sys.exit(3)"'},
        slow={"template": f'{PYTHON} -c "import time; print(1, flush=True); time.sleep(10)"', "timeout": 1},
        prompt={"template": f'{PYTHON} -c "input()"'},
        noisy={"template": f'{PYTHON} -c "print(\'x\' * 30000 + \'END\')"'},
        where={"template": f'{PYTHON} -c "import os; print(os.getcwd())"'},
        missing={"template": "no-such-program-xyz"},
        braces={"template": f'{PYTHON} -c "print({{1: 2}}, {{}})"'},  # braces that are not placeholders are kept
    ))
    project.configure(commands_config="commands.json", max_output_chars=4000)
    return project.load("commands")

def run(commands, *args) -> dict:
    return json.loads(commands.run_predefined_command(*args))

def test_argument_is_one_literal_argument(commands):
    assert run(commands, "argv", "harmless & echo INJECTED")["stdout"].strip() == "['harmless & echo INJECTED']"
    assert run(commands, "argv", 'a "b" c')["stdout"].strip() == """['a "b" c']"""
    assert run(commands, "embedded", "-x")["stdout"].strip() == "['--name=-x']"  # inside a token, '-' is harmless

@pytest.mark.parametrize("argument, message", [
    ("--output=evil", "must not start with '-'"),
    ("a\nb", "line breaks"),
])
def test_unsafe_arguments_are_rejected(commands, argument, message):
    result = run(commands, "argv", argument)
    assert result["error"] == "invalid_argument" and message in result["message"]

def test_missing_argument_and_unknown_command(commands):
    assert run(commands, "argv")["error"] == "missing_argument"
    assert run(commands, "nope")["error"] == "command_not_found"
    assert run(commands, "missing")["error"] == "program_not_found"

def test_exit_code_decides_success(commands):
    result = run(commands, "fail")
    assert result["success"] is False and result["returncode"] == 3

def test_timeout_stops_the_command_and_keeps_partial_output(commands):
    result = run(commands, "slow")
    assert result["error"] == "timeout" and result["stdout"].strip() == "1"

def test_commands_get_no_input(commands):
    assert run(commands, "prompt")["returncode"] == 1  # input() gets end-of-file instead of hanging

def test_long_output_keeps_start_and_end(commands):
    result = run(commands, "noisy")
    assert result["stdout"].strip().endswith("END") and "cut from the middle" in result["stdout"]
    assert "run a narrower command" in result["output_limited"]

def test_braces_that_are_not_placeholders_are_kept(commands):
    assert run(commands, "braces")["stdout"].strip() == "{1: 2} {}"

def test_commands_run_in_allowed_dir(commands, project):
    assert run(commands, "where")["stdout"].strip() == str(project.root.resolve())

def test_list_available_commands(commands):
    listed = {c["name"]: c for c in json.loads(commands.list_available_commands())["commands"]}
    assert listed["argv"]["requires_argument"] and not listed["fail"]["requires_argument"]
    assert listed["slow"]["timeout_seconds"] == 1

@pytest.mark.parametrize("registry_text, message", [
    (None, "command registry not found"),
    ("{ broken", "Expecting property name"),
    ('{"x": {"template": "python"}}', "must have a 'template' and a 'description'"),
    ('{"x": {"template": "python", "description": "d", "timeout": 0}}', "positive number of seconds"),
    ('{"x": {"template": "poetry add {args}", "description": "d"}}', "unknown placeholder '{args}'"),
    ('{"x": {"template": "ruff --select={ARG} .", "description": "d"}}', "unknown placeholder '{ARG}'"),
])
def test_broken_registry_stops_the_server(project, registry_text, message):
    if registry_text is not None:
        project.write("commands.json", registry_text)
    result = start_server("commands", project.configure(commands_config="commands.json"))
    assert result.returncode != 0 and message in result.stderr

# --- Suggested commands ---

@pytest.fixture
def suggesting(project):
    project.write("_commands/registry.json", registry(argv={"template": f"{PYTHON} -c \"print(1)\""}))
    project.configure(commands_config="_commands/registry.json", command_suggestions="_commands/suggested.json")
    return project

def suggest(commands, *args) -> dict:
    return json.loads(commands.suggest_command(*args))

def test_suggestion_is_saved_for_the_user_and_not_registered(suggesting):
    registry_before = (suggesting.root / "_commands/registry.json").read_text()
    commands = suggesting.load("commands")
    result = suggest(commands, "e2e", f"{PYTHON} -m pytest e2e {{arg}}", " Runs one end-to-end test file. ", 300)
    assert result["success"] and result["requires_argument"] and result["warnings"] == []
    assert "copy it into the command registry" in result["message"] and "registry.json" in result["message"]
    saved = json.loads((suggesting.root / "_commands/suggested.json").read_text(encoding="utf-8"))
    expected = {"template": f"{PYTHON} -m pytest e2e {{arg}}", "description": "Runs one end-to-end test file.", "timeout": 300}
    assert saved == {"e2e": expected}
    assert json.loads("{" + result["entry"] + "}") == saved  # the entry can be pasted into the registry as-is
    assert (suggesting.root / "_commands/registry.json").read_text() == registry_before
    assert run(commands, "e2e")["error"] == "command_not_found"

def test_suggestions_are_added_to_the_file_and_replace_by_name(suggesting):
    commands = suggesting.load("commands")
    assert not suggest(commands, "one", "npm run one", "d")["replaced_earlier_suggestion"]
    suggest(commands, "two", "npm run two", "d")
    assert suggest(commands, "one", "npm run one --x", "d")["replaced_earlier_suggestion"]
    saved = json.loads((suggesting.root / "_commands/suggested.json").read_text(encoding="utf-8"))
    assert list(saved) == ["one", "two"] and saved["one"]["template"] == "npm run one --x"

def test_suggestion_keeps_quotes_and_backslashes_exact(suggesting):
    template = '"C:\\Program Files\\Tool\\tool.exe" check --name="a b" {arg}'
    suggest(suggesting.load("commands"), "tool", template, "d")
    assert json.loads((suggesting.root / "_commands/suggested.json").read_text(encoding="utf-8"))["tool"]["template"] == template

@pytest.mark.parametrize("args, error, message", [
    (("bad name", "npm test", "d"), "invalid_name", "must start with a letter"),
    (("argv", "npm test", "d"), "already_registered", "already a registered command"),
    (("x", "poetry add {args}", "d"), "invalid_command", "unknown placeholder '{args}'"),
    (("x", "npm test", "d", 0), "invalid_command", "positive number of seconds"),
    (("x", "npm test", "  "), "invalid_command", "description must say"),
    (("x", "   ", "d"), "invalid_template", "template is empty"),
    (("x", '"unclosed', "d"), "invalid_template", "Check the quotes"),
    (("x", "{arg} install", "d"), "invalid_template", "program cannot be {arg}"),
])
def test_invalid_suggestions_are_rejected_and_not_saved(suggesting, args, error, message):
    result = suggest(suggesting.load("commands"), *args)
    assert result["error"] == error and message in result["message"]
    assert not (suggesting.root / "_commands/suggested.json").exists()

def test_risky_suggestions_are_saved_with_warnings(suggesting):
    warnings = " ".join(suggest(suggesting.load("commands"), "x", 'bash -c "echo {arg}"', "d")["warnings"])
    assert "is a shell" in warnings and "run any code" in warnings
    assert "not found on PATH" in suggest(suggesting.load("commands"), "y", "no-such-program-xyz", "d")["warnings"][0]

def test_a_broken_suggestions_file_is_not_overwritten(suggesting):
    suggesting.write("_commands/suggested.json", "{ broken")
    result = suggest(suggesting.load("commands"), "x", "npm test", "d")
    assert result["error"] == "suggestion_error" and "not valid JSON" in result["message"]
    assert (suggesting.root / "_commands/suggested.json").read_text() == "{ broken"

def test_suggestion_without_a_suggestions_file_is_only_returned(project):
    project.configure()
    result = suggest(project.load("commands"), "x", "npm test", "d")
    assert result["success"] and result["suggestions_file"] is None
    assert "is shown below" in result["message"] and "commands_config" in result["message"]

@windows_only
def test_batch_file_arguments_cannot_contain_cmd_characters(project):
    project.write("echo.cmd", "@echo off\r\necho got: %1\r\n", newline="")
    project.write("commands.json", registry(batch={"template": str(project.root / "echo.cmd") + " {arg}"}))
    project.configure(commands_config="commands.json")
    commands = project.load("commands")
    assert run(commands, "batch", "hello")["stdout"].strip() == "got: hello"
    result = run(commands, "batch", "x & echo INJECTED")
    assert result["error"] == "invalid_argument" and "batch file" in result["message"]
