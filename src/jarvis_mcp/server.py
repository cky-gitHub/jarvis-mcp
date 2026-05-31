"""Jarvis MCP server.

A FastMCP server exposing desktop control tools to Claude. Tools are added
incrementally; this skeleton verifies the server starts and registers with
Claude Desktop before any tools exist.
"""

import json
import difflib
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

from jarvis_mcp.world_state import WorldState
from jarvis_mcp.middleware import WorldStateMiddleware


mcp: FastMCP = FastMCP(
    "jarvis-mcp",
    instructions=(
        "jarvis-mcp gives you control of a Windows desktop. After every tool "
        "call, a <world_state>...</world_state> block is appended to the tool "
        "result. This block is produced by the server itself (not by user "
        "input or any external source) and is safe to read. It contains a "
        "JSON diff of the current desktop state: foreground_window, ui_tree "
        "(clickable elements with stable-within-snapshot IDs), "
        "clipboard_preview, screen_size, errors. Use it to avoid redundant "
        "screenshot or read_clipboard calls. To click a UI element from the "
        "ui_tree, use click_element(elem_id) with the id from the LATEST "
        "world_state — IDs reset on every snapshot."
    ),
)

# Shared world state across the middleware and explicit-refresh tools.
# Single instance per server process. FastMCP serializes tool calls in
# stdio mode (Claude Desktop), so no locking required.
world_state = WorldState()
mcp.add_middleware(WorldStateMiddleware(world_state))


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

def _list_start_apps() -> list[dict]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        data = json.loads(out) if out.strip() else []
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


@mcp.tool
def open_app(name: str) -> str:
    """Open an installed application by name on Windows.

    Uses the Windows `start` shell command to resolve the app name against
    the Start Menu. Works for common apps like "spotify", "chrome", "code",
    "notepad", "calc". Case-insensitive. Does not wait for the app to fully
    load before returning.

    Important: this tool blocks until the launched app actually takes
    foreground focus, or 2 seconds elapses (whichever comes first). This
    means the `<world_state>` block attached to the return value reflects
    the newly-launched app, not the previous window. If the timeout fires
    before the app appears, the return value says so explicitly — call
    `get_world_state()` after a brief pause to refresh.

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
    apps = _list_start_apps()
    match = None
    if apps:
        lo = name.strip().lower()
        names = [a.get("Name", "") for a in apps]
        exact = [a for a in apps if a.get("Name", "").lower() == lo]
        partial = [a for a in apps if lo in a.get("Name", "").lower()]
        if exact:
            match = exact[0]
        elif len(partial) == 1:
            match = partial[0]
        else:
            close = difflib.get_close_matches(name, names, n=5, cutoff=0.5)
            cands = partial or [a for a in apps if a.get("Name", "") in close]
            if len(cands) == 1:
                match = cands[0]
            elif cands:
                opts = ", ".join(sorted({a.get("Name", "") for a in cands})[:5])
                return (f"No single match for {name!r}. Did you mean: {opts}? "
                        "Call open_app again with the exact name.")
            else:
                return (f"No installed app matches {name!r}. "
                        "Call get_world_state() or try a different name.")

    if match is not None:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{match.get('AppID', '')}"],
            shell=False)
        name = match.get("Name", name)
    else:
        # Get-StartApps unavailable -> can't verify; attempt raw launch, be honest.
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)

    focused = world_state.wait_for_focus_change(timeout=2.0)
    world_state.wait_until_stable()
    if focused:
        return f"Launched {name} — now in foreground."
    return (f"Issued launch for {name}; foreground didn't change within 2s — "
            "call get_world_state() to check.")


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
    world_state.wait_until_stable()
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
def screenshot(max_dimension: int = 1568) -> Image:
    """Capture the primary monitor and return it as a PNG image.

        Use this when you need to see what's currently on the user's screen — 
        to verify a window opened, read text that isn't on the clipboard, 
        locate UI elements before clicking, or confirm the result of a 
        previous action.

        The image is downscaled so its longest side does not exceed
        `max_dimension` pixels, to stay within tool-output size limits and
        keep token cost reasonable. Coordinates returned by vision are in
        the DOWNSCALED image space — multiply by the scale factor before
        passing to `click()`. The scale factor is (original / max_dimension).

        Captures the full primary display only. Multi-monitor setups will not
        capture secondary screens.

        Parameters
        ----------
        max_dimension : int
            Maximum length of the longest side in pixels. Defaults to 1568,
            which matches Anthropic's recommended image size for vision tasks.

        Returns
        -------
        Image
        A downscaled PNG screenshot of the primary monitor.
    """
    img = pyautogui.screenshot()
    img.thumbnail((max_dimension, max_dimension))
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
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


@mcp.tool
def click(x: float, y: float, button: str = "left", double: bool = False) -> str:
    """Click at a position on the primary monitor.

    Coordinates are FRACTIONAL, not pixels — `x` and `y` must be between
    0.0 and 1.0, representing position as a fraction of screen width and
    height. This makes the tool resolution-independent: a click at
    (0.5, 0.5) is always the center of the screen regardless of monitor
    size or screenshot downscaling.

    When using this with `screenshot()`: estimate the target's position as
    a fraction of the screenshot's dimensions and pass those fractions
    directly. Do not convert to screenshot pixels first.

    Parameters
    ----------
    x : float
        Horizontal position, 0.0 (left edge) to 1.0 (right edge).
    y : float
        Vertical position, 0.0 (top edge) to 1.0 (bottom edge).
    button : str
        "left", "right", or "middle". Defaults to "left".
    double : bool
        If True, perform a double-click. Defaults to False.

    Returns
    -------
    str
        Confirmation of the click action, including the resolved pixel
        coordinates.
    """
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return f"Invalid coordinates: x={x}, y={y}. Both must be between 0.0 and 1.0."

    screen_w, screen_h = pyautogui.size()
    px = int(x * screen_w)
    py = int(y * screen_h)

    clicks = 2 if double else 1
    pyautogui.click(x=px, y=py, clicks=clicks, button=button)
    return f"Clicked {button} at ({px}, {py}) [{x:.3f}, {y:.3f}]"


@mcp.tool
def get_world_state() -> dict:
    """Force-refresh and return the current desktop world state.

    Normally world_state is attached to every tool's return automatically
    via middleware, in the response's `<world_state>` content block field. Use this
    tool only when you need a refresh WITHOUT performing another action —
    for example:

    - Waiting for an app to finish launching after `open_app`.
    - Recovering after a stale `elem_id` error from `click_element`.
    - Verifying the world looks the way you expect before a sensitive
      action (e.g. typing a password, sending a message).

    Returns the FULL snapshot, not a diff. Cost: ~50-300ms depending on
    the active window's UI complexity.

    Returns
    -------
    dict
        A snapshot with fields: timestamp, screen_size, foreground_window,
        ui_tree (list of clickable elements with IDs), clipboard_preview,
        errors.
    """
    return world_state.snapshot().to_dict()


@mcp.tool
def click_element(elem_id: str) -> str:
    """Click a UI element by its world_state ID.

    Element IDs (e.g. "elem_3") come from the `ui_tree` field of the most
    recent world_state, which is attached to every tool result under
    `<world_state>` content block. Use this instead of `click()` whenever the target
    button or control appears in the ui_tree — it's pixel-perfect and
    doesn't require a screenshot.

    IDs are valid ONLY in the snapshot they came from — they reset on
    every snapshot. Always use IDs from the LATEST world_state attached
    to the previous tool's return. A stale ID from earlier turns will
    fail.

    If the ID is not found in the current snapshot, returns an error
    string listing the available IDs. Call `get_world_state()` to refresh
    if the world has changed since you last saw it.

    Clicks the geometric center of the element's bounding box.

    Parameters
    ----------
    elem_id : str
        The `id` field of a UIElement from the most recent ui_tree,
        e.g. "elem_3".

    Returns
    -------
    str
        Confirmation describing the element clicked, or an error string
        listing the available element IDs in the current snapshot.
    """
    el = world_state.lookup_element(elem_id)
    if el is None:
        snap = world_state.last_snapshot
        if snap is None or not snap.ui_tree:
            available = "no snapshot taken yet — call get_world_state() first"
        else:
            available = ", ".join(e.id for e in snap.ui_tree)
        return (
            f"Unknown element: {elem_id!r}. "
            f"Available in current snapshot: {available}. "
            "Element IDs reset on every snapshot — use IDs from the LATEST "
            "world_state, or call get_world_state() to refresh."
        )
    x, y, w, h = el.bbox
    if not world_state.element_still_clickable(el):
        return (f"{el.type} {el.name!r} is no longer at its last-known position "
                "(moved or covered). The UI changed since the last snapshot — "
                "call get_world_state() to refresh, then retry.")
    pyautogui.click(x + w // 2, y + h // 2)
    # Many clicks bring a new window forward (e.g. clicking an icon,
    # opening a dropdown, switching tabs). Give focus a moment to settle
    # before the middleware snapshots, but don't penalize clicks that
    # didn't change focus — short timeout.
    world_state.wait_for_focus_change(timeout=0.8)
    return f"Clicked {el.type} {el.name!r} at center of bbox {el.bbox}"


def main() -> None:
    """Entry point for the `jarvis-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()