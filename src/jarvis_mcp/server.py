"""Jarvis MCP server.

A FastMCP server exposing desktop control tools to Claude. Tools are added
incrementally; this skeleton verifies the server starts and registers with
Claude Desktop before any tools exist.
"""

import pyperclip
from fastmcp import FastMCP
import subprocess
import pyautogui
from io import BytesIO
from fastmcp.utilities.types import Image
import asyncio
import tempfile
import os
import edge_tts
from playsound3 import playsound




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
def write_clipboard(text: str) -> str:
    """Write text to the system clipboard, replacing current contents.

    Use this when the user wants text made available for pasting elsewhere,
    or when returning a large block of text that would be tedious to copy
    from the chat. Overwrites whatever is currently on the clipboard.

    Parameters
    ----------
    text : str
        The text to place on the clipboard.

    Returns
    -------
    str
        Confirmation that the clipboard was updated.
    """
    pyperclip.copy(text)
    return f"Wrote {len(text)} chars to clipboard"

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


@mcp.tool
def screenshot() -> Image:
    """Capture the primary monitor and return it as a PNG image.

    Use this when you need to see what's currently on the user's screen — 
    to verify a window opened, read text that isn't on the clipboard, 
    locate UI elements before clicking, or confirm the result of a 
    previous action.

    Captures the full primary display only. Multi-monitor setups will not
    capture secondary screens. There is no region selection yet; you get
    the entire screen every time.

    Returns
    -------
    Image
        A PNG screenshot of the primary monitor.
    """
    img = pyautogui.screenshot()
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Image(data=buf.getvalue(), format="png")


@mcp.tool
def speak(text: str, voice: str = "en-US-GuyNeural") -> str:
    """Speak text aloud through the system's default audio output.

        Only call this tool when the user has explicitly asked for a spoken
        response in the current message, or has established earlier in the
        conversation that they want spoken replies. Do NOT call this tool by
        default, do NOT call it to "announce" or "confirm" actions, and do NOT
        call it just because the response is short. When in doubt, do not speak.

        Uses Microsoft Edge's neural TTS voices (requires internet). Blocks
        until speech finishes. Speech cannot be interrupted once started — keep
        `text` short (one or two sentences) unless the user has asked for a
        longer spoken response.

        Parameters
        ----------
        text : str
            The text to speak aloud.
        voice : str
            Edge TTS voice ID. Defaults to "en-US-GuyNeural" (male, American).
            Other good options: "en-US-AriaNeural" (female, American),
            "en-GB-RyanNeural" (male, British), "en-US-JennyNeural" (female,
            American, conversational).

        Returns
        -------
        str
        Confirmation that speech completed.
    """
    async def _generate(path: str) -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(path)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        path = f.name

    try:
        asyncio.run(_generate(path))
        playsound(path)
    finally:
        os.unlink(path)

    return f"Spoke: {text}"


def main() -> None:
    """Entry point for the `jarvis-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()

