"""Jarvis MCP server.

A FastMCP server exposing desktop control tools to Claude. Tools are added
incrementally; this skeleton verifies the server starts and registers with
Claude Desktop before any tools exist.
"""

import pyperclip
from fastmcp import FastMCP

mcp: FastMCP = FastMCP("jarvis-mcp")


@mcp.tool
def read_clipboard() -> str:
    """Read and return the current contents of the system clipboard.

    Returns
    -------
    str
        The text currently on the clipboard. Returns an empty string if the
        clipboard is empty or contains non-text data (e.g., an image).
    """
    return pyperclip.paste()


def main() -> None:
    """Entry point for the `jarvis-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()