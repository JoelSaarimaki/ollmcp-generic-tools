# --- instructions-mcp.py ---
import os
import traceback
from mcp.server.mcpserver import MCPServer

# --- Constants & Config ---
mcp = MCPServer("Instructions-Server")

# The environment variable defining the path to the project-specific instructions markdown file.
INSTRUCTIONS_FILE_PATH = os.getenv("PROJECT_INSTRUCTIONS_FILE")

# --- Public MCP Tools ---
@mcp.tool()
def get_project_instructions() -> str:
    """
    Returns the content of the project-specific instructions markdown file.
    Use this tool at the start of a session to understand the project context and initial instructions.

    Returns:
        str: The content of the instructions file or an error message.
    """
    if not INSTRUCTIONS_FILE_PATH:
        return "Error: The 'PROJECT_INSTRUCTIONS_FILE' environment variable is not set."

    try:
        if not os.path.exists(INSTRUCTIONS_FILE_PATH):
            return f"Error: The instructions file '{INSTRUCTIONS_FILE_PATH}' does not exist."
        
        with open(INSTRUCTIONS_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if not content.strip():
            return "Warning: The instructions file is empty."
            
        return content
    except Exception as e:
        return f"Error reading instructions file:\n{e}\n{traceback.format_exc()}"

if __name__ == "__main__":
    mcp.run()
