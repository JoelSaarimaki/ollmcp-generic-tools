# --- web-search-mcp.py ---
import json
import traceback
from urllib.parse import urlparse

from ollama import Client
from mcp.server.mcpserver import MCPServer
from mcp_common import (
    MAX_OUTPUT_CHARS,
    OLLAMA_API_KEY,
    compact_tool_schemas
)

# --- Constants & Config ---
mcp = MCPServer("Web-Search-Server")

OLLAMA_HOST = "https://ollama.com"  # Web search and fetch always use Ollama's hosted API
REQUEST_TIMEOUT_SECONDS = 30
MIN_RESULTS, MAX_RESULTS = 1, 10  # Limits of Ollama's web search API
MAX_LISTED_LINKS = 50  # Links listed at most by web_fetch
JSON_ESCAPE_RATIO = 0.9  # Share of the budget used for text inside JSON, as escaping line breaks and quotes adds characters
CONTENT_START = "----- BEGIN CONTENT -----"
CONTENT_END = "----- END CONTENT -----"

# The key is given explicitly, so that the ollama library does not read OLLAMA_API_KEY from the environment
CLIENT = Client(host=OLLAMA_HOST, headers={"authorization": f"Bearer {OLLAMA_API_KEY}"}, timeout=REQUEST_TIMEOUT_SECONDS) if OLLAMA_API_KEY else None
NOT_CONFIGURED = "Web search is not configured: 'ollama_api_key' is not set in the tools config file. Ask the user to add an Ollama API key. Use get_config to see the configuration."

# --- Internal Helpers ---

def _json_error(error: str, message: str, with_traceback: bool = False) -> str:
    """
    Returns a JSON error response.
    """
    response = {"success": False, "error": error, "message": message}
    if with_traceback:
        response["traceback"] = traceback.format_exc()
    return json.dumps(response, indent=2, ensure_ascii=False)

def _text_error(error: str, message: str) -> str:
    """
    Returns a plain-text error response, used by web_fetch, whose responses contain page content.
    """
    return f"Error ({error}): {message}"

def _cut(text: str, start: int, limit: int) -> tuple[str, int]:
    """
    Returns the part of text starting at start that fits in limit characters, cut at a line break if possible,
    and the position where the part ends.
    """
    end = start + limit
    if end >= len(text):
        return text[start:], len(text)
    cut = text.rfind("\n", start, end)
    end = cut + 1 if cut > start else end
    return text[start:end], end

# --- Public MCP Tools ---

@mcp.tool()
def web_search(query: str, max_results: int = 3) -> str:
    """
    Searches the web and returns the title, URL and content of the best matches.
    Read a whole page with web_fetch.

    Args:
        query: The search query.
        max_results: 1 to 10. Defaults to 3.
    """
    if CLIENT is None:
        return _json_error("not_configured", NOT_CONFIGURED)
    if not query.strip():
        return _json_error("invalid_query", "The query must not be empty.")

    try:
        max_results = min(MAX_RESULTS, max(MIN_RESULTS, int(max_results)))
        results = [{"title": r.title, "url": r.url, "content": r.content or ""}
                   for r in CLIENT.web_search(query=query, max_results=max_results).results]

        # Share the budget equally between the results, after the titles and URLs
        overhead = min(2000, MAX_OUTPUT_CHARS // 4) + sum(len(r["title"] or "") + len(r["url"] or "") + 60 for r in results)
        per_result = max(200, int((MAX_OUTPUT_CHARS - overhead) * JSON_ESCAPE_RATIO) // max(1, len(results)))
        shortened = 0
        for r in results:
            if len(r["content"]) > per_result:
                r["content"] = _cut(r["content"], 0, per_result)[0] + "\n... (cut short)"
                shortened += 1

        return json.dumps({
            "success": True,
            "query": query,
            "output_limited": f"Output limited: the content of {shortened} of {len(results)} results was cut short. Read a whole page with web_fetch." if shortened else None,
            "results": results,
            "message": "" if results else "No results found. Try a different or shorter query."
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return _json_error("search_error", f"Web search failed: {e}", with_traceback=True)

@mcp.tool()
def web_fetch(url: str, start_char: int = 0) -> str:
    """
    Fetches a web page: its title, links and text between '----- BEGIN CONTENT -----' and
    '----- END CONTENT -----'. Long pages are read in parts.

    Args:
        url: The full URL, starting with http:// or https://.
        start_char: Where to continue reading. Defaults to 0.
    """
    if CLIENT is None:
        return _text_error("not_configured", NOT_CONFIGURED)
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _text_error("invalid_url", f"'{url}' is not a full web address. Give a URL starting with http:// or https://.")

    try:
        start_char = max(0, int(start_char))
        page = CLIENT.web_fetch(url=url.strip())
        content = page.content or ""
        if start_char > 0 and start_char >= len(content):
            return _text_error("invalid_start_char", f"start_char {start_char} is beyond the end of the content, which has {len(content)} characters.")

        header = [f"URL: {url.strip()}", f"Title: {page.title or '(none)'}"]
        links = list(page.links or []) if start_char == 0 else []
        if links:
            listed = links[:MAX_LISTED_LINKS]
            header.append(f"Links ({len(listed)} of {len(links)}):" if len(links) > len(listed) else f"Links ({len(links)}):")
            header.extend(f"- {link}" for link in listed)

        budget = max(1000, MAX_OUTPUT_CHARS - sum(len(line) + 1 for line in header) - min(1000, MAX_OUTPUT_CHARS // 8))
        shown, end = _cut(content, start_char, budget)
        if start_char > 0 or end < len(content):
            header.insert(2, f"Content: characters {start_char}-{end} of {len(content)}" + (" (partial)" if end < len(content) else ""))
        if end < len(content):
            header.insert(3, f"Output limited: the page is too long to show at once. Read the next part with start_char={end}.")

        return "\n".join(header) + f"\n{CONTENT_START}\n{shown}\n{CONTENT_END}"
    except Exception as e:
        return _text_error("fetch_error", f"Fetching the page failed: {e}\n{traceback.format_exc()}")

compact_tool_schemas(mcp)

if __name__ == "__main__":
    mcp.run()
