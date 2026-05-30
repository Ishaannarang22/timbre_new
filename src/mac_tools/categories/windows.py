"""
windows.py — category="windows": window management via System Events (Accessibility).

All of these are SAFE risk-wise (no data loss, no sends, no power/network changes — closing a
window is a window-button click, not a Trash delete). They drive the GUI through System Events'
accessibility API, which on a fresh Mac requires the controlling process to be granted
Accessibility permission. Until that's granted, System Events returns an error / these calls
TIME OUT (osascript err -1712). That's expected and FINE: like every handler here, each one
catches the failure and returns a friendly spoken string rather than raising into the pipeline.

House style (matches src/mac_actions.py): validate input, shell out only via runner.run_osa
(caller app names reach AppleScript solely as trailing argv via `on run argv`), audit each
action, NEVER raise, return a SHORT spoken-friendly string.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa
from ..runner import valid_app_name as _valid_app_name

# App-name validation lives in runner.valid_app_name (shared with apps.py — one source of truth).
# Imported above as `_valid_app_name` so the existing call sites are unchanged.


# The frontmost app's front window via System Events. We resolve the frontmost process inside
# AppleScript (no caller input), so these no-arg verbs have nothing to inject.
_FRONT_PROC = (
    "first application process whose frontmost is true"
)


@tool(
    "minimize_front_window",
    "Minimize the front window of the active app (send it to the Dock).",
    risk=Risk.SAFE,
    category="windows",
)
def minimize_front_window() -> str:
    """Set the front window's AXMinimized attribute to true. Needs Accessibility; on failure
    (incl. -1712 timeout without permission) returns a friendly string."""
    try:
        run_osa(
            'tell application "System Events"',
            f"set value of attribute \"AXMinimized\" of front window of ({_FRONT_PROC}) to true",
            "end tell",
        )
        msg = "Minimized the front window."
        audit("minimize_front_window", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't minimize the front window."
        audit("minimize_front_window", {}, f"error: {e}")
        return msg


@tool(
    "zoom_front_window",
    "Zoom (maximize) the front window of the active app, like clicking its green zoom button.",
    risk=Risk.SAFE,
    category="windows",
)
def zoom_front_window() -> str:
    """Press the front window's zoom (green) button via its AXZoomButton. Needs Accessibility."""
    try:
        run_osa(
            'tell application "System Events"',
            f'perform action "AXPress" of (button 2 of front window of ({_FRONT_PROC}))',
            "end tell",
        )
        msg = "Zoomed the front window."
        audit("zoom_front_window", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't zoom the front window."
        audit("zoom_front_window", {}, f"error: {e}")
        return msg


# Alias requested by the contract: maximize_front_window is the same verb as zoom on macOS
# (macOS has no separate "maximize" — the green button zooms). Register both names so the LLM
# can use whichever word the user says; both run the same zoom action.
@tool(
    "maximize_front_window",
    "Maximize the front window of the active app (same as zoom — clicks the green button).",
    risk=Risk.SAFE,
    category="windows",
)
def maximize_front_window() -> str:
    """Alias of zoom_front_window (macOS green-button zoom). Never raises."""
    return zoom_front_window()


@tool(
    "close_front_window",
    "Close the front window of the active app (like clicking its red close button). The app "
    "itself keeps running.",
    risk=Risk.SAFE,
    category="windows",
)
def close_front_window() -> str:
    """Press the front window's close (red) button via AXPress on button 1. This closes only
    the window, not the app (so no app-level unsaved-work quit) — SAFE. Needs Accessibility."""
    try:
        run_osa(
            'tell application "System Events"',
            f'perform action "AXPress" of (button 1 of front window of ({_FRONT_PROC}))',
            "end tell",
        )
        msg = "Closed the front window."
        audit("close_front_window", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't close the front window."
        audit("close_front_window", {}, f"error: {e}")
        return msg


@tool(
    "fullscreen_toggle",
    "Toggle the active app's front window in or out of full screen (like Control-Command-F).",
    risk=Risk.SAFE,
    category="windows",
)
def fullscreen_toggle() -> str:
    """Flip the front window's AXFullScreen attribute. Reads the current value and inverts it;
    needs Accessibility. Never raises."""
    try:
        run_osa(
            'tell application "System Events"',
            f"set _w to front window of ({_FRONT_PROC})",
            'set _fs to value of attribute "AXFullScreen" of _w',
            'set value of attribute "AXFullScreen" of _w to (not _fs)',
            "end tell",
        )
        msg = "Toggled full screen."
        audit("fullscreen_toggle", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't toggle full screen."
        audit("fullscreen_toggle", {}, f"error: {e}")
        return msg


@tool(
    "get_window_titles",
    "List the open window titles, either for a named app or (if none given) for the active "
    "app.",
    properties={
        "app": {
            "type": "string",
            "description": "Optional app name. Omit to use the frontmost app.",
        }
    },
    required=[],
    risk=Risk.SAFE,
    category="windows",
)
def get_window_titles(app: str = "") -> str:
    """Report window titles for the given app (or the frontmost one). Caller app name, when
    given, reaches AppleScript only as argv. Needs Accessibility; never raises."""
    name = _valid_app_name(app) if str(app or "").strip() else None
    try:
        if name is not None:
            raw = run_osa(
                'tell application "System Events"',
                "set _titles to name of every window of "
                "(first application process whose name is (item 1 of argv))",
                "end tell",
                "set AppleScript's text item delimiters to \"||\"",
                "return _titles as text",
                args=[name],
            )
            who = name
        else:
            raw = run_osa(
                'tell application "System Events"',
                f"set _titles to name of every window of ({_FRONT_PROC})",
                "end tell",
                "set AppleScript's text item delimiters to \"||\"",
                "return _titles as text",
            )
            who = "the active app"
        titles = [t.strip() for t in raw.split("||") if t.strip()]
        if not titles:
            msg = f"{who.capitalize()} has no open windows."
            audit("get_window_titles", {"app": name}, msg)
            return msg
        msg = f"{who.capitalize()} windows: " + ", ".join(titles) + "."
        audit("get_window_titles", {"app": name}, f"{len(titles)} windows")
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read the window titles."
        audit("get_window_titles", {"app": name}, f"error: {e}")
        return msg


@tool(
    "focus_app_window",
    "Bring a named app to the front and raise its front window (focus it).",
    properties={"app": {"type": "string", "description": "The app's name to focus."}},
    required=["app"],
    risk=Risk.SAFE,
    category="windows",
)
def focus_app_window(app: str) -> str:
    """Activate the app and raise its front window. Caller name reaches AppleScript only as
    argv. Needs Accessibility for the raise; the activate alone may still succeed. Never raises."""
    name = _valid_app_name(app)
    if name is None:
        msg = "I need a valid app name to focus."
        audit("focus_app_window", {"app": app}, msg)
        return msg
    try:
        run_osa(
            "on run argv",
            "tell application (item 1 of argv) to activate",
            'tell application "System Events"',
            "tell (first application process whose name is (item 1 of argv))",
            "set frontmost to true",
            'perform action "AXRaise" of front window',
            "end tell",
            "end tell",
            "end run",
            args=[name],
        )
        msg = f"Focused {name}."
        audit("focus_app_window", {"app": name}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't focus {name}."
        audit("focus_app_window", {"app": name}, f"error: {e}")
        return msg
