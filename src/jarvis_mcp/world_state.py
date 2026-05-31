"""World state snapshotting for jarvis-mcp.

Captures the live state of the Windows desktop relevant to LLM-driven
control: foreground window, clickable UI elements in that window,
clipboard preview, screen size.

Design notes
------------
- Snapshots are computed on demand, not pushed. Callers (the middleware)
  invoke `snapshot()` after each tool call.
- Element IDs are stable within a single snapshot only. The latest snapshot
  replaces the previous one in `last_snapshot`.
- `snapshot_diff()` returns only fields that changed vs the last snapshot,
  to keep token cost low when the user stays in the same window across
  multiple tool calls.
- pywinauto walks can fail or hang on non-cooperative apps (some Electron,
  some games, some UWP). Failures populate an `error` field; the rest of
  the snapshot still returns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

import pyautogui
import pyperclip
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError

# --- constants ---
MAX_ELEMENTS: int = 60     # elements RETURNED per snapshot (token budget)
MAX_SCAN: int = 400        # hard ceiling on the tree walk; bounds pathological pages


# Accessibility control types worth exposing to the LLM as clickable.
# Everything else (Pane, Group, Custom, Text, etc.) is structural noise.
CLICKABLE_TYPES: frozenset[str] = frozenset({
    "Button",
    "Edit",
    "Hyperlink",
    "MenuItem",
    "ListItem",
    "CheckBox",
    "RadioButton",
    "TabItem",
    "ComboBox",
    "TreeItem",
})

# Hard cap on elements per snapshot. Some windows (browsers with many tabs,
# IDEs with file trees) can expose 500+ elements; that's a token blowout
# and Claude can't reason over that many candidates usefully anyway.
MAX_ELEMENTS: int = 60

# Clipboard preview length. Full clipboard can be megabytes (copied files,
# huge text). We send a preview so Claude knows roughly what's there.
CLIPBOARD_PREVIEW_CHARS: int = 200


@dataclass(frozen=True)
class UIElement:
    """A single clickable element in the foreground window."""
    id: str               # e.g. "elem_3" — stable within snapshot only
    name: str             # element label / accessible name
    type: str             # control type (Button, Edit, ...)
    bbox: tuple[int, int, int, int]  # (x, y, width, height) in screen pixels
    rid: tuple = ()       # UIA runtime_id — internal plumbing, not serialized


@dataclass
class Snapshot:
    """A point-in-time view of the desktop world."""
    timestamp: float
    screen_size: tuple[int, int]
    foreground_window: dict[str, Any]   # {"title": str, "app": str} or {"error": str}
    ui_tree: list[UIElement]
    clipboard_preview: str
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "screen_size": list(self.screen_size),
            "foreground_window": self.foreground_window,
            "ui_tree": [{k: v for k, v in asdict(e).items() if k != "rid"} for e in self.ui_tree],
            "clipboard_preview": self.clipboard_preview,
            "errors": self.errors,
        }


class WorldState:
    """Holds the last snapshot and produces fresh snapshots and diffs.

    One instance per server process. Not thread-safe; FastMCP serializes
    tool calls in stdio mode so this is fine. If we ever run async/SSE
    with parallel tools, this needs a lock.
    """

    def __init__(self) -> None:
        self.last_snapshot: Snapshot | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """Capture current world state. Always succeeds — individual
        fields may carry errors but the Snapshot object always returns."""
        errors: list[str] = []

        screen_size = pyautogui.size()

        try:
            foreground_window, ui_tree = self._capture_foreground()
        except Exception as e:  # noqa: BLE001 — pywinauto raises a zoo
            foreground_window = {"error": f"{type(e).__name__}: {e}"}
            ui_tree = []
            errors.append(f"foreground_capture: {type(e).__name__}: {e}")

        try:
            clipboard_preview = self._capture_clipboard()
        except Exception as e:  # noqa: BLE001
            clipboard_preview = ""
            errors.append(f"clipboard: {type(e).__name__}: {e}")

        snap = Snapshot(
            timestamp=time.time(),
            screen_size=(screen_size.width, screen_size.height),
            foreground_window=foreground_window,
            ui_tree=ui_tree,
            clipboard_preview=clipboard_preview,
            errors=errors,
        )
        self.last_snapshot = snap
        return snap

    def wait_for_focus_change(
        self,
        timeout: float = 2.0,
        poll_interval: float = 0.05,
    ) -> bool:
        """Block until the foreground window title changes, or timeout.

        Used by tools that move focus (open_app, click_element on an
        element that brings a new window forward). We poll the foreground
        window's title — cheap, ~1-3ms per check — and return as soon as
        it differs from the last snapshot's title.

        If no previous snapshot exists, returns immediately (nothing to
        wait for). If the title never changes, returns False after the
        timeout. Callers should snapshot regardless of return value —
        False just means the focus change we expected didn't materialize
        (app failed to launch, click did nothing, etc.).

        Parameters
        ----------
        timeout : float
            Maximum seconds to wait. Default 2.0 — long enough for most
            app launches, short enough not to noticeably stall the LLM.
        poll_interval : float
            Seconds between checks. Default 50ms.

        Returns
        -------
        bool
            True if focus changed within the timeout, False otherwise.
        """
        prev = self.last_snapshot
        if prev is None:
            return True

        prev_title = prev.foreground_window.get("title", "")
        deadline = time.time() + timeout
        desktop = Desktop(backend="uia")

        while time.time() < deadline:
            try:
                current = desktop.window(active_only=True).window_text()
                if current != prev_title:
                    return True
            except Exception:  # noqa: BLE001
                # Pywinauto hiccup — treat as no-change, keep polling.
                pass
            time.sleep(poll_interval)

        return False

    def wait_until_stable(self, timeout: float = 2.5, poll: float = 0.15,
                          stable_polls: int = 2) -> bool:
        """Block until the foreground window title is unchanged across
        `stable_polls` consecutive polls, or `timeout` elapses. Cheap
        (title-only). Returns fast when already stable. NOTE: continuously
        animating pages (autoplay video) never stabilise -> waits full timeout."""
        import time
        desktop = Desktop(backend="uia")
        last = object()          # sentinel, never equal on first pass
        stable = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                title = desktop.window(active_only=True).window_text()
            except Exception:
                title = None
            if title is not None and title == last:
                stable += 1
                if stable >= stable_polls:
                    return True
            else:
                stable = 0
            last = title
            time.sleep(poll)
        return False

    def snapshot_diff(self) -> dict[str, Any]:
        """Capture a new snapshot and return only fields that changed
        vs the previous snapshot. Always includes timestamp.

        First call (no previous snapshot) returns the full snapshot.
        """
        prev = self.last_snapshot
        new = self.snapshot()  # updates self.last_snapshot

        if prev is None:
            return new.to_dict()

        diff: dict[str, Any] = {"timestamp": new.timestamp}

        if prev.screen_size != new.screen_size:
            diff["screen_size"] = list(new.screen_size)

        if prev.foreground_window != new.foreground_window:
            diff["foreground_window"] = new.foreground_window
            # Window changed → ui_tree is almost certainly different too;
            # include it. Even if elements happen to match, IDs reset.
            diff["ui_tree"] = [{k: v for k, v in asdict(e).items() if k != "rid"} for e in new.ui_tree]
        elif self._ui_tree_changed(prev.ui_tree, new.ui_tree):
            diff["ui_tree"] = [{k: v for k, v in asdict(e).items() if k != "rid"} for e in new.ui_tree]
        # else: same window, same ui_tree — omit entirely.

        if prev.clipboard_preview != new.clipboard_preview:
            diff["clipboard_preview"] = new.clipboard_preview

        if new.errors:
            diff["errors"] = new.errors

        return diff

    def lookup_element(self, elem_id: str) -> UIElement | None:
        """Find a UIElement by ID in the most recent snapshot. Returns
        None if no snapshot has been taken or the ID is unknown."""
        if self.last_snapshot is None:
            return None
        for el in self.last_snapshot.ui_tree:
            if el.id == elem_id:
                return el
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _capture_foreground(self) -> tuple[dict[str, Any], list[UIElement]]:
        desktop = Desktop(backend="uia")
        try:
            win = desktop.window(active_only=True)
            title = win.window_text()
            class_name = win.class_name() or ""
        except ElementNotFoundError:
            return {"error": "no active window"}, []

        meta: dict[str, Any] = {"title": title, "app": class_name}

        # Phase 1 — collect clickable candidates in tree order (named AND nameless),
        # up to a hard scan ceiling. Stash centre point + runtime_id so we can
        # hit-test for occlusion later if the named set overflows the budget.
        candidates: list[dict[str, Any]] = []
        for desc in win.descendants():
            if len(candidates) >= MAX_SCAN:
                break
            try:
                ctype = desc.element_info.control_type or ""
                if ctype not in CLICKABLE_TYPES:
                    continue
                rect = desc.rectangle()
                if rect.width() <= 0 or rect.height() <= 0:
                    continue
                candidates.append({
                    "ctype": ctype,
                    "name": (desc.window_text() or "").strip(),
                    "bbox": (rect.left, rect.top, rect.width(), rect.height()),
                    "center": (rect.left + rect.width() // 2,
                               rect.top + rect.height() // 2),
                    "rid": tuple(desc.element_info.runtime_id or ()),
                })
            except Exception:
                continue

        # Phase 2 — selection.
        named = [c for c in candidates if c["name"]]
        if len(named) <= MAX_ELEMENTS:
            # Common case, no budget pressure: named clickable elements only
            # (original behaviour — keeps ordinary pages noise-free).
            chosen = named
        else:
            # Overflow: usually a modal/overlay competing for slots. UIA exposes no
            # z-order, so recover it by hit-testing each candidate's centre and
            # dropping anything not topmost there (i.e. covered by an overlay).
            # Then named-first, backfilling with nameless topmost elements — likely
            # the overlay's own controls (e.g. an unlabelled Play button).
            visible = [c for c in candidates if self._is_topmost(desktop, c)]
            vis_named = [c for c in visible if c["name"]]
            vis_nameless = [c for c in visible if not c["name"]]
            chosen = (vis_named + vis_nameless)[:MAX_ELEMENTS]

        elements: list[UIElement] = []
        for i, c in enumerate(chosen):
            label = c["name"][:80] if c["name"] else \
                f"<{c['ctype']} @{c['center'][0]},{c['center'][1]}>"
            elements.append(UIElement(
                id=f"elem_{i}",
                name=label,
                type=c["ctype"],
                bbox=c["bbox"],
                rid=c["rid"],
            ))

        return meta, elements

    def _point_hits(self, desktop: Desktop, center, rid: tuple) -> bool:
        if not rid:
            return True
        cx, cy = center
        try:
            top = desktop.from_point(cx, cy)
            node = top.wrapper_object() if hasattr(top, "wrapper_object") else top
        except Exception:
            return True
        for _ in range(5):
            if node is None:
                break
            try:
                if tuple(node.element_info.runtime_id or ()) == rid:
                    return True
                node = node.parent()
            except Exception:
                break
        return False

    def _is_topmost(self, desktop: Desktop, cand: dict) -> bool:
        return self._point_hits(desktop, cand["center"], cand["rid"])

    def element_still_clickable(self, el) -> bool:
        """True if a click at the element's last-known centre still lands on it
        (same runtime_id) — i.e. it hasn't moved or been covered since capture."""
        if not getattr(el, "rid", None):
            return True
        x, y, w, h = el.bbox
        return self._point_hits(Desktop(backend="uia"),
                                (x + w // 2, y + h // 2), tuple(el.rid))

    def _capture_clipboard(self) -> str:
        """Read clipboard, return a truncated preview."""
        raw = pyperclip.paste() or ""
        if len(raw) <= CLIPBOARD_PREVIEW_CHARS:
            return raw
        return raw[:CLIPBOARD_PREVIEW_CHARS] + f"... [+{len(raw) - CLIPBOARD_PREVIEW_CHARS} chars]"

    @staticmethod
    def _ui_tree_changed(a: list[UIElement], b: list[UIElement]) -> bool:
        """Cheap structural comparison. Same length + same (name, type, bbox)
        tuples in order → unchanged. Anything else → changed."""
        if len(a) != len(b):
            return True
        for x, y in zip(a, b):
            if (x.name, x.type, x.bbox) != (y.name, y.type, y.bbox):
                return True
        return False