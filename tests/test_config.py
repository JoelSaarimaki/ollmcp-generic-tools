# --- test_config.py ---
"""
Tests for loading and validating the tools config file (common.py), including the enabled toolsets and tools.
"""
import json

import pytest

from conftest import start_server

@pytest.mark.parametrize("config_text, message", [
    ("{ project_dir: . }", "Expecting property name"),
    ("[]", "must contain a JSON object"),
    ('{"forbidden_paths": []}', "'project_dir' is required"),
    ('{"project_dir": null}', "'project_dir' is required"),
    ('{"project_dir": "missing-folder"}', "'missing-folder' is relative to the folder of the config file"),
    ('{"project_dir": ".", "commands_config": "c.json"}', "'commands_config' is no longer a setting of the tools config file: it is now called 'command_registry'"),
    ('{"project_dir": ".", "forbiden_paths": []}', "unknown settings"),
    ('{"project_dir": ".", "forbidden_paths": "secret"}', "must be a list of path strings"),
    ('{"project_dir": ".", "max_output_chars": "4000"}', "must be a whole number"),
    ('{"project_dir": ".", "gitignore_path": 5}', "must be a path string"),
    ('{"project_dir": ".", "ignored_dirs": "node_modules"}', "must be a list of folder names"),
    ('{"project_dir": ".", "ignored_dirs": ["node_modules", " "]}', "must be a list of folder names"),
    ('{"project_dir": ".", "ignored_dirs": ["docs/build"]}', "not paths"),
    ('{"project_dir": ".", "backup_folder": "."}', "'backup_folder' must not contain 'project_dir'"),
    ('{"project_dir": ".", "command_registry": "c.json", "command_suggestions": "c.json"}', "'command_suggestions' must be a file of its own"),
    ('{"project_dir": ".", "command_suggestions": "tools-config.json"}', "'command_suggestions' must be a file of its own"),
    ('{"project_dir": ".", "command_suggestions": "sub/.mcp.json"}', "'command_suggestions' must be a file of its own"),
])
def test_invalid_config_stops_the_server(project, config_text, message):
    project.config_path.write_text(config_text, encoding="utf-8")
    result = start_server(project.config_path)
    assert result.returncode != 0
    assert message in result.stderr

def test_missing_environment_variable_stops_the_server(project):
    result = start_server(None)
    assert result.returncode != 0
    assert "MCP_TOOLS_CONFIG" in result.stderr

def test_missing_config_file_stops_the_server(project):
    result = start_server(project.root / "missing.json")
    assert result.returncode != 0
    assert "config file not found" in result.stderr

def test_every_toolset_starts_with_a_minimal_config(project):
    result = start_server(project.configure())
    assert result.returncode == 0, result.stderr

@pytest.mark.parametrize("settings, message", [
    ({"disabled_toolsets": "web"}, "'disabled_toolsets' must be a list of names"),
    ({"disabled_toolsets": ["webs"]}, "'disabled_toolsets' contains unknown names ['webs']"),
    ({"disabled_tools": ["get_diff"]}, "'disabled_tools' contains unknown names ['get_diff']"),
    ({"disabled_tools": ["create_file"]}, "'disabled_tools' contains unknown names ['create_file']"),  # a removed tool
    ({"disabled_toolsets": ["filesystem", "outline", "search", "git", "commands"], "disabled_tools": ["web_search", "web_fetch"]}, "disable every tool"),
])
def test_invalid_toolset_settings_stop_the_server(project, settings, message):
    result = start_server(project.configure(**settings))
    assert result.returncode != 0 and message in result.stderr

def test_disabled_toolsets_are_not_loaded(project):
    """A disabled toolset's own settings, such as a missing command registry, are not checked."""
    config = project.configure(command_registry="missing.json")
    assert "command registry not found" in start_server(config).stderr
    assert start_server(project.configure(command_registry="missing.json", disabled_toolsets=["commands"])).returncode == 0
    all_commands = ["list_available_commands", "run_predefined_command", "suggest_command"]
    assert start_server(project.configure(command_registry="missing.json", disabled_tools=all_commands)).returncode == 0
    common = project.common()
    assert "commands" not in common.ENABLED_TOOLSETS and common.DISABLED_TOOLS == set(all_commands)

@pytest.mark.parametrize("user_config_text, message", [
    ("{ broken", "Expecting property name"),
    ("[]", "must contain a JSON object"),
    ('{"ollama_key": "x"}', "unknown settings ['ollama_key']"),
])
def test_invalid_user_config_stops_the_server(project, user_config_text, message):
    project.user_config().write_text(user_config_text, encoding="utf-8")
    result = start_server(project.configure())
    assert result.returncode != 0 and "user-config.json" in result.stderr and message in result.stderr

def test_null_settings_use_the_defaults(project):
    optional = ["forbidden_paths", "allowed_paths", "ignored_dirs", "gitignore_path", "max_output_chars", "command_registry", "read_only_files"]
    project.configure(**{key: None for key in optional})
    common = project.common()
    assert common.IGNORED_DIRS == common.DEFAULT_IGNORED_DIRS
    assert common.MAX_OUTPUT_CHARS == common.DEFAULT_MAX_OUTPUT_CHARS
    assert common.FORBIDDEN_PATHS == [] and common.ALLOWED_PATHS == [] and common.READ_ONLY_FILES == []
    assert common.REGISTRY_PATH is None
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
        "project_dir": "../project",
        "forbidden_paths": ["secret"],
        "allowed_paths": ["secret/plans"],
        "command_registry": "commands.json",
        "read_only_files": ["instructions.md"],
    }), encoding="utf-8")
    (config_dir / "instructions.md").write_text("# Instructions\n", encoding="utf-8")
    common = project.common()
    root = project.root.resolve()
    assert common.PROJECT_DIR == root
    assert common.FORBIDDEN_PATHS == [root / "secret"] and common.ALLOWED_PATHS == [root / "secret/plans"]  # relative to project_dir
    assert common.REGISTRY_PATH == config_dir.resolve() / "commands.json"
    assert common.READ_ONLY_FILES == [config_dir.resolve() / "instructions.md"]

@pytest.mark.parametrize("settings, message", [
    ({"allowed_paths": ["src"]}, "'allowed_paths' must be inside a folder of 'forbidden_paths'"),
    ({"forbidden_paths": ["secret"], "allowed_paths": ["secret"]}, "'allowed_paths' must be inside a folder of 'forbidden_paths'"),
    ({"forbidden_paths": ["secret"], "allowed_paths": ["../elsewhere"]}, "'allowed_paths' must be inside 'project_dir'"),
    ({"forbidden_paths": ["secret"], "allowed_paths": ["secret/.mcp.json"]}, "cannot allow a protected file"),
    ({"read_only_files": ["missing.md"]}, "'read_only_files' names a file that does not exist"),
    ({"read_only_files": ["tools-config.json"]}, "'read_only_files' cannot contain a protected file"),
    ({"context_folder": "context"}, "unknown settings ['context_folder']"),
])
def test_invalid_allowed_and_read_only_paths_stop_the_server(project, settings, message):
    result = start_server(project.configure(**settings))
    assert result.returncode != 0 and message in result.stderr

def test_forbidden_path_entries_are_trimmed(project):
    project.configure(forbidden_paths=["  secret.txt  ", ""])
    assert project.common().FORBIDDEN_PATHS == [project.root.resolve() / "secret.txt"]

def test_get_config_reports_the_shared_settings(project):
    project.write("src/app.py", "x = 1\n")
    project.configure(forbidden_paths=["secret"], max_output_chars=5000)
    config = json.loads(project.load("filesystem").get_tools_config())
    assert config["success"]
    assert config["config_file"].endswith("tools-config.json")
    assert config["shared"]["forbidden_paths"] == ["secret"]
    assert config["shared"]["max_output_chars"] == 5000
    assert config["shared"]["protected_file_names"] == [".mcp.json"]
    assert config["shared"]["protected_files"] == [config["config_file"], config["web_search"]["user_config_file"]]
    assert config["outline_and_search"]["ignored_dirs_source"] == "default"

def test_get_config_reports_custom_ignored_dirs(project):
    project.configure(ignored_dirs=["coverage"])
    config = json.loads(project.load("filesystem").get_tools_config())
    assert config["outline_and_search"]["ignored_dirs"] == ["coverage"]
    assert config["outline_and_search"]["ignored_dirs_source"] == "tools config"
