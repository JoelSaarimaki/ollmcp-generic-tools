# --- test_init.py ---
"""
Tests for 'python -m ollmcp_tools init': setting up a project from the templates in the package.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PACKAGE, REPO_DIR, requires_git, start_server

PROJECT_FILES = [".mcp.json", ".ollmcp-tools/tools-config.json", ".ollmcp-tools/commands.json",
                 ".ollmcp-tools/instructions.md", ".ollmcp-tools/.gitignore"]

def init(folder: Path) -> subprocess.CompletedProcess:
    """Runs init in the folder in a separate process, without MCP_TOOLS_CONFIG, as a user would."""
    env = {key: value for key, value in os.environ.items() if key != "MCP_TOOLS_CONFIG"}
    env["PYTHONPATH"] = str(REPO_DIR)
    env["PYTHONIOENCODING"] = "utf-8"  # The report contains paths, e.g. with 'ä' in the user name
    return subprocess.run([sys.executable, "-m", PACKAGE, "init"], cwd=folder, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")

@pytest.fixture
def new_project(project):
    """An empty project folder: the project fixture's tools config is not used by init."""
    return project.root

def test_init_creates_the_project_files(new_project):
    result = init(new_project)
    assert result.returncode == 0, result.stderr
    for rel in PROJECT_FILES:
        assert (new_project / rel).is_file() and f"Created: {rel}" in result.stdout
    assert (new_project / ".ollmcp-tools/context").is_dir()
    user_config = Path.home() / ".ollmcp-tools" / "user-config.json"
    assert json.loads(user_config.read_text(encoding="utf-8")) == {"ollama_api_key": None}

def test_the_created_project_starts_with_every_toolset(new_project):
    init(new_project)
    result = start_server(new_project / ".ollmcp-tools/tools-config.json")
    assert result.returncode == 0, result.stderr

def test_the_created_config_points_to_the_project_and_hides_its_own_folder(new_project, project):
    init(new_project)
    project.config_path = new_project / ".ollmcp-tools/tools-config.json"
    common = project.common()
    assert common.ALLOWED_DIR == new_project.resolve()
    assert common.FORBIDDEN_PATHS == [new_project.resolve() / ".ollmcp-tools"]
    assert common.REGISTRY_PATH == new_project.resolve() / ".ollmcp-tools/commands.json"
    assert common.READ_ONLY_FILES == [new_project.resolve() / ".ollmcp-tools/instructions.md"]
    mcp = json.loads((new_project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dev"]
    assert mcp["env"]["MCP_TOOLS_CONFIG"] == ".ollmcp-tools/tools-config.json"

def test_the_checklist_explains_allowed_dir(new_project):
    stdout = init(new_project).stdout
    assert "'allowed_dir' is \"..\"" in stdout and str(new_project.resolve()) in stdout
    assert "user-config.json" in stdout and "dev.read_context_file" in stdout

def test_init_never_overwrites_files(new_project):
    init(new_project)
    (new_project / ".ollmcp-tools/commands.json").write_text("{}", encoding="utf-8")
    (new_project / ".ollmcp-tools/context").rmdir()  # as after cloning, since the folder is not committed
    result = init(new_project)
    assert (new_project / ".ollmcp-tools/commands.json").read_text(encoding="utf-8") == "{}"
    assert "Already exists, not changed: .ollmcp-tools/commands.json" in result.stdout
    assert "Created: .ollmcp-tools/context/" in result.stdout

@requires_git
def test_the_gitignore_leaves_out_each_persons_files(new_project):
    init(new_project)
    subprocess.run(["git", "init", "-q"], cwd=new_project, check=True)
    (new_project / ".ollmcp-tools/context/plan.md").write_text("# Plan\n", encoding="utf-8")
    (new_project / ".ollmcp-tools/suggested-commands.json").write_text("{}", encoding="utf-8")
    status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=new_project,
                            capture_output=True, text=True, check=True).stdout
    assert ".ollmcp-tools/tools-config.json" in status and ".ollmcp-tools/commands.json" in status
    assert "context" not in status and "suggested-commands" not in status

def test_unknown_command_shows_the_usage(new_project):
    result = subprocess.run([sys.executable, "-m", PACKAGE, "setup"], cwd=new_project,
                            env={**os.environ, "PYTHONPATH": str(REPO_DIR)}, capture_output=True, text=True)
    assert result.returncode == 2 and "Usage: python -m ollmcp_tools [init]" in result.stderr

def test_init_and_common_use_the_same_user_config_file(project):
    project.configure()
    init_module = importlib.import_module(f"{PACKAGE}.init")
    assert init_module.USER_CONFIG_PATH == project.common().USER_CONFIG_PATH
