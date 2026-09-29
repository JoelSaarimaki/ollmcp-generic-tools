# --- conftest.py ---
"""
Shared fixtures for the MCP server tests.

Every test gets its own temporary project and tools config file. Servers are loaded fresh for
each test, with mcp_common removed from sys.modules first, so that every server reads the test's
config, as a real server start would. The working directory is a different folder than the project,
as the tools must resolve paths against allowed_dir, not the working directory.
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
SERVERS_DIR = REPO_DIR / "mcp-servers"
sys.path.insert(0, str(SERVERS_DIR))

SERVER_FILES = {
    "filesystem": "safe-filesystem-mcp.py",
    "map": "generate-map-mcp.py",
    "search": "search-tool-mcp.py",
    "git": "git-diff-mcp.py",
    "commands": "commands-mcp.py",
    "context": "context-record-mcp.py",
    "web": "web-search-mcp.py",
}

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows-specific behaviour")

class Project:
    """
    A temporary project folder with a tools config file, and helpers for loading the servers for it.
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
        """Writes the tools config file with allowed_dir '.' and the given settings."""
        self.config_path.write_text(json.dumps({"allowed_dir": ".", **settings}), encoding="utf-8")
        return self.config_path

    def _use_config(self):
        if not self.config_path.exists():
            self.configure()
        self._monkeypatch.setenv("MCP_TOOLS_CONFIG", str(self.config_path))
        sys.modules.pop("mcp_common", None)

    def load(self, server: str):
        """Loads a server module with the project's config."""
        self._use_config()
        spec = importlib.util.spec_from_file_location(f"test_server_{server}", SERVERS_DIR / SERVER_FILES[server])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def common(self):
        """Loads mcp_common with the project's config."""
        self._use_config()
        return importlib.import_module("mcp_common")

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "-c", "core.autocrlf=false", *args],
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=True)

    def init_git(self):
        """Makes the project a git repository with everything committed."""
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")

@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.chdir(tmp_path)
    return Project(tmp_path / "project", monkeypatch)

@pytest.fixture
def make_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Creates projects at a given path below the temporary folder, e.g. inside a folder named 'out'."""
    monkeypatch.chdir(tmp_path)
    return lambda rel: Project(tmp_path / rel, monkeypatch)

def start_server(server: str, config_path: Path | None) -> subprocess.CompletedProcess:
    """
    Imports a server in a separate process, as a real start would, and returns the finished process.
    A config error makes the process fail with the error message in stderr.
    """
    code = ("import sys, importlib.util; sys.path.insert(0, sys.argv[1]); "
            "spec = importlib.util.spec_from_file_location('server', sys.argv[2]); "
            "spec.loader.exec_module(importlib.util.module_from_spec(spec))")
    env = dict(os.environ)
    env.pop("MCP_TOOLS_CONFIG", None)
    if config_path is not None:
        env["MCP_TOOLS_CONFIG"] = str(config_path)
    return subprocess.run([sys.executable, "-c", code, str(SERVERS_DIR), str(SERVERS_DIR / SERVER_FILES[server])],
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
