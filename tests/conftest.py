# --- conftest.py ---
"""
Shared fixtures for the MCP server tests.

Every test gets its own temporary project and tools config file. Toolsets are loaded fresh for
each test, with the package's modules removed from sys.modules first, so that every toolset reads
the test's config, as a real server start would. The working directory is a different folder than the project,
as the tools must resolve paths against project_dir, not the working directory.
"""
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))  # The package is imported from the repository, even if it is not installed
PACKAGE = "ollmcp_tools"
# The parsers read no config, so they are kept loaded between tests
KEPT_MODULES = {PACKAGE, f"{PACKAGE}.parsing"}

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-specific behaviour")

class Project:
    """
    A temporary project folder with a tools config file, and helpers for loading the toolsets for it.
    """
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "tools-config.json"
        self._monkeypatch = monkeypatch

    def write(self, rel: str, content: str | bytes = "", newline: str = "\n") -> Path:
        """Writes a file into the project, creating its folders. Text is written with the given line ending."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            with open(path, "w", encoding="utf-8", newline=newline) as f:
                f.write(content)
        return path

    def configure(self, **settings) -> Path:
        """Writes the tools config file with project_dir '.' and the given settings."""
        self.config_path.write_text(json.dumps({"project_dir": ".", **settings}), encoding="utf-8")
        return self.config_path

    def user_config(self, **settings) -> Path:
        """Writes the user config file, in the temporary home folder, with the given settings."""
        path = Path.home() / ".ollmcp-tools" / "user-config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings), encoding="utf-8")
        return path

    def _use_config(self):
        if not self.config_path.exists():
            self.configure()
        self._monkeypatch.setenv("MCP_TOOLS_CONFIG", str(self.config_path))
        for name in [name for name in sys.modules if name.startswith(f"{PACKAGE}.") and name not in KEPT_MODULES]:
            del sys.modules[name]

    def load(self, toolset: str):
        """Loads a toolset module, such as 'filesystem', with the project's config."""
        self._use_config()
        return importlib.import_module(f"{PACKAGE}.{toolset}")

    def common(self):
        """Loads the common module with the project's config."""
        self._use_config()
        return importlib.import_module(f"{PACKAGE}.common")

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "-c", "core.autocrlf=false", *args],
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)

    def init_git(self):
        """Makes the project a git repository with everything committed."""
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

def _use_temporary_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Points the home folder to the temporary folder, so that the user config file and the default backup folder of restore_file are there."""
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.chdir(tmp_path)
    _use_temporary_home(tmp_path, monkeypatch)
    return Project(tmp_path / "project", monkeypatch)

@pytest.fixture
def make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Creates projects at a given path below the temporary folder, e.g. inside a folder named 'out'."""
    monkeypatch.chdir(tmp_path)
    _use_temporary_home(tmp_path, monkeypatch)
    return lambda rel: Project(tmp_path / rel, monkeypatch)

def start_server(config_path: Path | None) -> subprocess.CompletedProcess:
    """
    Creates the server with its enabled toolsets in a separate process, as a real start would, without
    running it, and returns the finished process. A config error makes the process fail with the error message in stderr.
    """
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from ollmcp_tools.__main__ import create_server; create_server()")
    env = dict(os.environ)
    env.pop("MCP_TOOLS_CONFIG", None)
    if config_path is not None:
        env["MCP_TOOLS_CONFIG"] = str(config_path)
    return subprocess.run([sys.executable, "-c", code, str(REPO_DIR)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")

def content_of(response: str) -> str:
    """Returns the content of the first fenced code block of a response, e.g. of read_file_with_metadata or edit_file."""
    match = re.search(r"^(`{3,})[\w+-]*\n(.*?)\n\1$", response, re.S | re.M)
    assert match, f"No code block in response:\n{response}"
    return match.group(2)

def sha_of(response: str) -> str:
    """Returns the SHA-256 shown in a read_file_with_metadata or edit_file response."""
    match = re.search(r"SHA-256: ([0-9a-f]{64})", response)
    assert match, f"No SHA-256 in response:\n{response}"
    return match.group(1)
