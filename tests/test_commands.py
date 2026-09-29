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
])
def test_broken_registry_stops_the_server(project, registry_text, message):
    if registry_text is not None:
        project.write("commands.json", registry_text)
    result = start_server("commands", project.configure(commands_config="commands.json"))
    assert result.returncode != 0 and message in result.stderr

@windows_only
def test_batch_file_arguments_cannot_contain_cmd_characters(project):
    project.write("echo.cmd", "@echo off\r\necho got: %1\r\n", newline="")
    project.write("commands.json", registry(batch={"template": str(project.root / "echo.cmd") + " {arg}"}))
    project.configure(commands_config="commands.json")
    commands = project.load("commands")
    assert run(commands, "batch", "hello")["stdout"].strip() == "got: hello"
    result = run(commands, "batch", "x & echo INJECTED")
    assert result["error"] == "invalid_argument" and "batch file" in result["message"]
