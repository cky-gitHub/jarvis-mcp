"""Jarvis MCP server.

A FastMCP server exposing desktop control tools to Claude. Tools are added
incrementally; this skeleton verifies the server starts and registers with
Claude Desktop before any tools exist.
"""

import pyperclip
from fastmcp import FastMCP
import subprocess


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


@mcp.tool
def open_app(name: str) -> str:
    """Open an installed application by name on Windows.

    Uses the Windows `start` shell command to resolve the app name against
    the Start Menu. Works for common apps like "spotify", "chrome", "code",
    "notepad", "calc". Case-insensitive. Does not wait for the app to fully
    load before returning.

    Parameters
    ----------
    name : str
        The app name as it appears in the Start Menu (e.g., "spotify",
        "chrome", "notepad"). Do not include `.exe` or a full path.

    Returns
    -------
    str
        Confirmation message that the launch command was issued. Note this
        does not guarantee the app actually opened — only that Windows
        accepted the command.
    """
    subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
    return f"Issued launch command for: {name}"

import pyautogui

@mcp.tool
def type_text(text: str) -> str:

    """Type text into the currently focused window, as if from the keyboard.

        The text is typed into whatever window currently has keyboard focus.
        The caller is responsible for focusing the correct window before
        calling this tool — there is no way to undo or cancel typing once
        started.

        Only ASCII characters are supported. Non-ASCII characters (accents,
        emoji, non-English scripts) will be silently skipped.

        Parameters
        ----------
        text : str
            The text to type. Newlines (`\\n`) are typed as Enter keypresses.

        Returns
        -------
        str
            Confirmation message echoing the typed text.
    """

    pyautogui.typewrite(text, interval=0.01)
    return f"Issued typing command for: {text}"

def main() -> None:
    """Entry point for the `jarvis-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()

