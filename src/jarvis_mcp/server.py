"""Jarvis MCP server.

A FastMCP server exposing desktop control tools to Claude. Tools are added
incrementally; this skeleton verifies the server starts and registers with
Claude Desktop before any tools exist.
"""

import pyperclip
from fastmcp import FastMCP
import subprocess
import pyautogui


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

@mcp.tool
def control_music(action: str, query: str | None = None) -> str:
    """Control music playback using Windows global media keys.

        Sends keyboard media-key events that any compliant media player on
        Windows will respond to, including Spotify. The target app does not
        need to be focused — media keys work globally. The app does need to
        be running for play/pause/skip/previous to have any effect.

        `search_and_play` is declared but not yet implemented in this MVP.
        Calling it returns an explanatory message instead of failing.

        Parameters
        ----------
        action : str
            One of: "play", "pause", "skip", "previous", "search_and_play".
            Note that "play" and "pause" both send the same toggle key, since
            Windows media controls expose a single play/pause toggle rather
            than separate commands.
        query : str | None
            Search query, used only by "search_and_play". Ignored for all
            other actions. Defaults to None.

        Returns
        -------
        str
        Confirmation of the action taken, or an error message describing
        why the action could not be performed.
    """
    if action in ("play", "pause"):
        pyautogui.press("playpause")
        return "Toggled play/pause"
    elif action == "skip":
        pyautogui.press("nexttrack")
        return "Skipped to next track"
    elif action == "previous":
        pyautogui.press("prevtrack")
        return "Went to previous track"
    elif action == "search_and_play":
        return "search_and_play is not yet implemented in this MVP"
    else:
        return (
            f"Unknown action: {action!r}. "
            "Valid actions: play, pause, skip, previous, search_and_play."
        )


def main() -> None:
    """Entry point for the `jarvis-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()

