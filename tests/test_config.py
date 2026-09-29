# --- test_config.py ---
"""
Tests for loading and validating the tools config file (mcp_common.py).
"""
import json

import pytest

from conftest import SERVER_FILES, start_server

@pytest.mark.parametrize("config_text, message", [
    ("{ allowed_dir: . }", "Expecting property name"),
    ("[]", "must contain a JSON object"),
    ('{"forbidden_paths": []}', "'allowed_dir' is required"),
    ('{"allowed_dir": null}', "'allowed_dir' is required"),
    ('{"allowed_dir": "missing-folder"}', "not an existing folder"),
    ('{"allowed_dir": ".", "forbiden_paths": []}', "unknown settings"),
    ('{"allowed_dir": ".", "forbidden_paths": "secret"}', "must be a list of path strings"),
    ('{"allowed_dir": ".", "max_output_chars": "4000"}', "must be a whole number"),
    ('{"allowed_dir": ".", "gitignore_path": 5}', "must be a path string"),
    ('{"allowed_dir": ".", "ignored_dirs": "node_modules"}', "must be a list of folder names"),
    ('{"allowed_dir": ".", "ignored_dirs": ["node_modules", " "]}', "must be a list of folder names"),
    ('{"allowed_dir": ".", "ignored_dirs": ["docs/build"]}', "not paths"),
])
def test_invalid_config_stops_the_server(project, config_text, message):
    project.config_path.write_text(config_text, encoding="utf-8")
    result = start_server("filesystem", project.config_path)
    assert result.returncode != 0
    assert message in result.stderr

def test_missing_environment_variable_stops_the_server(project):
    result = start_server("filesystem", None)
    assert result.returncode != 0
    assert "MCP_TOOLS_CONFIG" in result.stderr

def test_missing_config_file_stops_the_server(project):
    result = start_server("filesystem", project.root / "missing.json")
    assert result.returncode != 0
    assert "config file not found" in result.stderr

@pytest.mark.parametrize("server", SERVER_FILES)
def test_every_server_refuses_an_invalid_config(project, server):
    project.config_path.write_text('{"allowed_dir": ".", "unknown_setting": 1}', encoding="utf-8")
    assert start_server(server, project.config_path).returncode != 0

@pytest.mark.parametrize("server", SERVER_FILES)
def test_every_server_starts_with_a_minimal_config(project, server):
    result = start_server(server, project.configure())
    assert result.returncode == 0, result.stderr

def test_null_settings_use_the_defaults(project):
    optional = ["forbidden_paths", "ignored_dirs", "gitignore_path", "max_output_chars", "commands_config", "context_folder", "read_only_files"]
    project.configure(**{key: None for key in optional})
    common = project.common()
    assert common.IGNORED_DIRS == common.DEFAULT_IGNORED_DIRS
    assert common.MAX_OUTPUT_CHARS == common.DEFAULT_MAX_OUTPUT_CHARS
    assert common.FORBIDDEN_PATHS == [] and common.READ_ONLY_FILES == []
    assert common.COMMANDS_CONFIG is None and common.CONTEXT_FOLDER is None
    assert common.GITIGNORE_PATH == project.root.resolve() / ".gitignore"

def test_empty_ignored_dirs_list_skips_no_folders(project):
    project.configure(ignored_dirs=[])
    assert project.common().IGNORED_DIRS == set()

def test_ignored_dirs_list_replaces_the_defaults(project):
    project.configure(ignored_dirs=["node_modules", "coverage"])
    assert project.common().IGNORED_DIRS == {"node_modules", "coverage"}

def test_max_output_chars_has_a_minimum(project):
    project.configure(max_output_chars=100)
    assert project.common().MAX_OUTPUT_CHARS == 2000

def test_paths_are_relative_to_the_config_file_folder(make_project):
    project = make_project("project")
    config_dir = project.root.parent / "config"
    config_dir.mkdir()
    project.config_path = config_dir / "tools-config.json"
    project.config_path.write_text(json.dumps({
        "allowed_dir": "../project",
        "forbidden_paths": ["secret"],
        "commands_config": "commands.json",
        "context_folder": "../project/_context",
    }), encoding="utf-8")
    common = project.common()
    root = project.root.resolve()
    assert common.ALLOWED_DIR == root
    assert common.FORBIDDEN_PATHS == [root / "secret"]  # relative to allowed_dir
    assert common.COMMANDS_CONFIG == config_dir.resolve() / "commands.json"
    assert common.CONTEXT_FOLDER == root / "_context"

def test_forbidden_path_entries_are_trimmed(project):
    project.configure(forbidden_paths=["  secret.txt  ", ""])
    assert project.common().FORBIDDEN_PATHS == [project.root.resolve() / "secret.txt"]

def test_get_config_reports_the_shared_settings(project):
    project.write("src/app.py", "x = 1\n")
    project.configure(forbidden_paths=["secret"], max_output_chars=5000)
    config = json.loads(project.load("filesystem").get_config())
    assert config["success"]
    assert config["config_file"].endswith("tools-config.json")
    assert config["shared"]["forbidden_paths"] == ["secret"]
    assert config["shared"]["max_output_chars"] == 5000
    assert config["shared"]["protected_file_names"] == [".mcp.json"]
    assert config["outline_and_search"]["ignored_dirs_source"] == "default"

def test_get_config_reports_custom_ignored_dirs(project):
    project.configure(ignored_dirs=["coverage"])
    config = json.loads(project.load("filesystem").get_config())
    assert config["outline_and_search"]["ignored_dirs"] == ["coverage"]
    assert config["outline_and_search"]["ignored_dirs_source"] == "tools config"
