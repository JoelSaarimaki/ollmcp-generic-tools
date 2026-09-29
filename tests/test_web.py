# --- test_web.py ---
"""
Tests for web-search-mcp.py. The Ollama client is replaced with a fake one, so no requests are sent.
"""
import json
from types import SimpleNamespace

import pytest

from conftest import content_of, start_server

class FakeClient:
    """Records the calls and returns the given search results and page."""
    def __init__(self, results=(), page=None, error=None):
        self.results, self.page, self.error = list(results), page, error
        self.calls = []

    def web_search(self, query, max_results):
        self.calls.append(("search", query, max_results))
        if self.error:
            raise self.error
        return SimpleNamespace(results=self.results[:max_results])

    def web_fetch(self, url):
        self.calls.append(("fetch", url))
        if self.error:
            raise self.error
        return self.page

def result(title="Title", url="https://example.com", content="Content"):
    return SimpleNamespace(title=title, url=url, content=content)

def page(content="Hello ä", title="Page", links=()):
    return SimpleNamespace(title=title, content=content, links=list(links))

@pytest.fixture
def web(project):
    project.configure(ollama_api_key="test-key")
    return project.load("web")

def test_api_key_is_read_from_the_tools_config(project):
    project.configure(ollama_api_key="  test-key  ")
    server = project.load("web")
    assert server.OLLAMA_API_KEY == "test-key"
    assert server.CLIENT._client.headers["authorization"] == "Bearer test-key"

def test_api_key_is_not_read_from_the_environment(project, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "environment-key")
    project.configure()
    server = project.load("web")
    assert server.CLIENT is None
    assert json.loads(server.web_search("python"))["error"] == "not_configured"
    assert server.web_fetch("https://example.com").startswith("Error (not_configured):")

@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_api_key_is_not_set(project, value):
    project.configure(ollama_api_key=value)
    assert project.common().OLLAMA_API_KEY is None

def test_api_key_must_be_a_string(project):
    project.configure(ollama_api_key=123)
    result = start_server("web", project.config_path)
    assert result.returncode != 0
    assert "'ollama_api_key' must be a string" in result.stderr

def test_get_config_reports_the_key_without_showing_it(project):
    project.configure(ollama_api_key="secret-key")
    response = project.load("filesystem").get_config()
    assert json.loads(response)["web_search"]["ollama_api_key_set"] is True
    assert "secret-key" not in response

def test_web_search_returns_the_results(web):
    web.CLIENT = FakeClient([result("Ä title", "https://a.example", "First"), result(url="https://b.example")])
    response = web.web_search("python", max_results=5)
    data = json.loads(response)
    assert data["success"] and data["output_limited"] is None
    assert [r["url"] for r in data["results"]] == ["https://a.example", "https://b.example"]
    assert "Ä title" in response  # not escaped
    assert web.CLIENT.calls == [("search", "python", 5)]

@pytest.mark.parametrize("requested, sent", [(0, 1), (-5, 1), (50, 10)])
def test_web_search_keeps_max_results_within_the_api_limits(web, requested, sent):
    web.CLIENT = FakeClient([result()])
    web.web_search("python", max_results=requested)
    assert web.CLIENT.calls == [("search", "python", sent)]

def test_web_search_rejects_an_empty_query(web):
    web.CLIENT = FakeClient()
    assert json.loads(web.web_search("  "))["error"] == "invalid_query"
    assert web.CLIENT.calls == []

def test_web_search_reports_no_results(web):
    web.CLIENT = FakeClient([])
    data = json.loads(web.web_search("nothing"))
    assert data["success"] and data["results"] == [] and "No results" in data["message"]

def test_web_search_limits_long_results(project):
    project.configure(ollama_api_key="key", max_output_chars=4000)
    web = project.load("web")
    web.CLIENT = FakeClient([result(content="line of text\n" * 1000) for _ in range(3)])
    response = web.web_search("python")
    data = json.loads(response)
    assert len(response) <= 4000
    assert data["output_limited"].startswith("Output limited:") and "web_fetch" in data["output_limited"]

def test_web_search_reports_errors(web):
    web.CLIENT = FakeClient(error=RuntimeError("unauthorized"))
    data = json.loads(web.web_search("python"))
    assert data["error"] == "search_error" and "unauthorized" in data["message"]

def test_web_fetch_returns_the_page(web):
    web.CLIENT = FakeClient(page=page('code "quoted"\nline 2', links=["https://example.com/a"]))
    response = web.web_fetch(" https://example.com ")
    assert content_of(response) == 'code "quoted"\nline 2'  # as-is, not JSON-escaped
    assert "Title: Page" in response and "- https://example.com/a" in response
    assert "Output limited" not in response
    assert web.CLIENT.calls == [("fetch", "https://example.com")]

@pytest.mark.parametrize("url", ["example.com", "file:///C:/secret.txt", "ftp://example.com", "https://"])
def test_web_fetch_rejects_invalid_urls(web, url):
    web.CLIENT = FakeClient(page=page())
    assert web.web_fetch(url).startswith("Error (invalid_url):")
    assert web.CLIENT.calls == []

def test_web_fetch_limits_the_links(web):
    web.CLIENT = FakeClient(page=page(links=[f"https://example.com/{i}" for i in range(80)]))
    response = web.web_fetch("https://example.com")
    assert "Links (50 of 80):" in response and "https://example.com/49\n" in response and "https://example.com/50\n" not in response

def test_web_fetch_reads_long_pages_in_parts(project):
    project.configure(ollama_api_key="key", max_output_chars=3000)
    web = project.load("web")
    text = "".join(f"line {i}\n" for i in range(1000))
    web.CLIENT = FakeClient(page=page(text, links=["https://example.com/a"]))

    parts, start = [], 0
    while True:
        response = web.web_fetch("https://example.com", start_char=start)
        assert len(response) <= 3000
        assert ("https://example.com/a" in response) == (start == 0)  # links only in the first part
        parts.append(content_of(response))
        if "Output limited" not in response:
            break
        start = int(response.split("start_char=")[1].split(".")[0])
    assert "".join(part if part.endswith("\n") else part + "\n" for part in parts) == text

def test_web_fetch_rejects_start_char_beyond_the_content(web):
    web.CLIENT = FakeClient(page=page("short"))
    assert web.web_fetch("https://example.com", start_char=100).startswith("Error (invalid_start_char):")

def test_web_fetch_reports_errors(web):
    web.CLIENT = FakeClient(error=RuntimeError("timed out"))
    response = web.web_fetch("https://example.com")
    assert response.startswith("Error (fetch_error):") and "timed out" in response
