"""
categories/system.py — macOS appearance & system-toggle tools.

These drive the kind of "system settings" the user reaches for by voice: dark mode, Do Not
Disturb / Focus, Night Shift, and the desktop wallpaper path. All are Risk.SAFE — they're
cosmetic / focus-only and fully reversible (no data loss, no connectivity drop), so they run
immediately with no confirmation.

House style (matches src/mac_actions.py and the runner contract):
  * Every value that reaches AppleScript and originates from the caller goes through
    run_osa(args=[...]) + `on run argv` — NEVER string-interpolated. Here the only such value
    is a wallpaper PATH; toggles/sets use fixed strings we fully control.
  * Clamp / validate inputs; default-deny on bad input with a friendly string.
  * audit() every action.
  * NEVER raise into the pipeline — catch and return a friendly spoken string.

TCC note: several of these poke `System Events` / `Control Center`, which need Automation
(and sometimes Accessibility) permission. If the grant is missing, osascript returns -1743
(not authorized) or times out at -1712; the runner raises subprocess.SubprocessError, we catch
it, and we speak a friendly "I couldn't do that" — never an exception.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa

# Do Not Disturb / Focus is the messiest one across macOS versions: there is no stable public
# AppleScript verb. The reliable cross-version path is the Control Center menu-bar UI, which we
# click via System Events (needs Accessibility). We keep the UI script in one place. If it
# fails (permission/version drift) the handler reports that gracefully rather than guessing.
_DND_TIMEOUT = 8.0  # UI scripting is slower than a plain `set` — give it a little more room.


# --- DARK MODE ----------------------------------------------------------------
# System Events exposes the global appearance as a boolean: `dark mode of appearance
# preferences`. Read it, toggle it, or set it. No caller text touches AppleScript here.


def _dark_mode_is_on() -> bool:
    """True iff macOS is currently in Dark mode. Raises on osascript failure (caller catches)."""
    res = run_osa(
        'tell application "System Events" to tell appearance preferences to '
        "return dark mode"
    )
    return res.strip().lower() == "true"


@tool(
    name="get_dark_mode",
    description="Report whether this Mac is currently in Dark mode or Light mode.",
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="system",
)
def get_dark_mode() -> str:
    try:
        on = _dark_mode_is_on()
        msg = "Dark mode is on." if on else "Dark mode is off — you're in Light mode."
        audit("get_dark_mode", {}, msg)
        return msg
    except (subprocess.SubprocessError, ValueError) as e:
        msg = "Sorry, I couldn't check the appearance setting."
        audit("get_dark_mode", {}, f"error: {e}")
        return msg


@tool(
    name="toggle_dark_mode",
    description="Switch this Mac between Dark mode and Light mode (flips whichever is active).",
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="system",
)
def toggle_dark_mode() -> str:
    try:
        run_osa(
            'tell application "System Events" to tell appearance preferences to '
            "set dark mode to not dark mode"
        )
        # Read back the resulting state so we can say which way we flipped it.
        now_on = _dark_mode_is_on()
        msg = "Switched to Dark mode." if now_on else "Switched to Light mode."
        audit("toggle_dark_mode", {}, msg)
        return msg
    except (subprocess.SubprocessError, ValueError) as e:
        msg = "Sorry, I couldn't change the appearance."
        audit("toggle_dark_mode", {}, f"error: {e}")
        return msg


@tool(
    name="set_dark_mode",
    description=(
        "Turn Dark mode on or off explicitly. Pass enabled=true for Dark mode, false for "
        "Light mode."
    ),
    properties={
        "enabled": {"type": "boolean", "description": "true for Dark mode, false for Light mode."}
    },
    required=["enabled"],
    risk=Risk.SAFE,
    category="system",
)
def set_dark_mode(enabled: bool = False) -> str:
    flag = bool(enabled)
    try:
        # Boolean literal is from our OWN bool, not caller text — nothing to inject.
        run_osa(
            'tell application "System Events" to tell appearance preferences to '
            f"set dark mode to {'true' if flag else 'false'}"
        )
        msg = "Dark mode is on." if flag else "Dark mode is off."
        audit("set_dark_mode", {"enabled": flag}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the appearance."
        audit("set_dark_mode", {"enabled": flag}, f"error: {e}")
        return msg


# --- DO NOT DISTURB / FOCUS ---------------------------------------------------
# Toggled via the Control Center menu-bar item (the only path that works across recent macOS
# without private frameworks). Needs Accessibility permission; if it isn't granted we say so.


@tool(
    name="toggle_do_not_disturb",
    description=(
        "Toggle Do Not Disturb (Focus) on this Mac via Control Center. Use this to silence "
        "notifications, or to turn them back on. This flips whatever the current Focus state is."
    ),
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="system",
)
def toggle_do_not_disturb() -> str:
    # Click Control Center -> Focus, toggle, then dismiss. This is UI scripting, so it's
    # fragile to layout/version changes and to a missing Accessibility grant — hence the
    # generous catch and friendly fallback. No caller text is involved at all.
    try:
        run_osa(
            'tell application "System Events"',
            '  tell application process "ControlCenter"',
            '    set ccItem to (first menu bar item of menu bar 1 '
            'whose description contains "Focus")',
            "    click ccItem",
            "  end tell",
            "end tell",
            timeout=_DND_TIMEOUT,
        )
        # Best-effort: press Escape to close the panel we just opened so we don't leave UI up.
        try:
            run_osa(
                'tell application "System Events" to key code 53', timeout=_DND_TIMEOUT
            )
        except subprocess.SubprocessError:
            pass
        msg = "Toggled Do Not Disturb."
        audit("toggle_do_not_disturb", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        # -1743 (not authorized) or -1712 (timeout) => Accessibility not granted. Say so plainly.
        msg = (
            "I couldn't toggle Do Not Disturb — I may need Accessibility permission to do that."
        )
        audit("toggle_do_not_disturb", {}, f"error: {e}")
        return msg


# --- NIGHT SHIFT --------------------------------------------------------------
# Night Shift has no public AppleScript verb either; the dependable path is the Control Center
# "Display" panel toggle. Same Accessibility caveat as DND.


@tool(
    name="toggle_night_shift",
    description=(
        "Toggle Night Shift (the warm/blue-light filter on the display) on or off via Control "
        "Center. Use this when the user wants warmer evening colors or to turn that off."
    ),
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="system",
)
def toggle_night_shift() -> str:
    # Open Control Center's Display module, then click the Night Shift toggle inside it. The
    # exact element names drift across macOS versions, so we search by name and fall back to a
    # friendly message if we can't find/operate it. No caller text touches AppleScript.
    try:
        run_osa(
            'tell application "System Events"',
            '  tell application process "ControlCenter"',
            "    -- open the Display control-center module",
            '    set dispItem to (first menu bar item of menu bar 1 '
            'whose description is "Display")',
            "    click dispItem",
            "    delay 0.4",
            '    tell window 1 to click (first checkbox '
            'whose title contains "Night Shift")',
            "  end tell",
            "end tell",
            timeout=_DND_TIMEOUT,
        )
        try:
            run_osa(
                'tell application "System Events" to key code 53', timeout=_DND_TIMEOUT
            )
        except subprocess.SubprocessError:
            pass
        msg = "Toggled Night Shift."
        audit("toggle_night_shift", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = (
            "I couldn't toggle Night Shift — I may need Accessibility permission, or the "
            "Control Center layout changed."
        )
        audit("toggle_night_shift", {}, f"error: {e}")
        return msg


# --- DESKTOP WALLPAPER --------------------------------------------------------
# Read the current desktop picture path, or set it to a new image. The path for `set` is the
# one caller-derived value here, so it goes through args=[...] + `on run argv` (never inlined)
# and we sanity-check that it actually points at an existing file first.

import os  # noqa: E402  — used only by the wallpaper handlers below


@tool(
    name="get_wallpaper",
    description="Report the file path of the current desktop wallpaper on this Mac.",
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="system",
)
def get_wallpaper() -> str:
    try:
        path = run_osa(
            'tell application "System Events" to '
            "get picture of current desktop"
        )
        if not path:
            msg = "I couldn't read the current wallpaper."
            audit("get_wallpaper", {}, msg)
            return msg
        msg = f"The current wallpaper is {path}."
        audit("get_wallpaper", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read the current wallpaper."
        audit("get_wallpaper", {}, f"error: {e}")
        return msg


@tool(
    name="set_wallpaper",
    description=(
        "Set this Mac's desktop wallpaper to an image file. Pass the full path to an existing "
        "image (e.g. /Users/you/Pictures/beach.jpg)."
    ),
    properties={
        "path": {
            "type": "string",
            "description": "Absolute path to an existing image file to use as the wallpaper.",
        }
    },
    required=["path"],
    risk=Risk.SAFE,
    category="system",
)
def set_wallpaper(path: str = "") -> str:
    p = str(path).strip()
    if not p:
        msg = "Tell me which image file to use."
        audit("set_wallpaper", {"path": path}, msg)
        return msg
    # Validate locally BEFORE touching AppleScript: the file must actually exist. This also
    # gives a clearer spoken error than a cryptic AppleScript failure.
    if not os.path.isfile(os.path.expanduser(p)):
        msg = "I couldn't find an image file at that path."
        audit("set_wallpaper", {"path": p}, msg)
        return msg
    full = os.path.expanduser(p)
    try:
        # Caller-derived path passed ONLY as trailing argv (on run argv) — never interpolated.
        run_osa(
            "on run argv",
            '  tell application "System Events" to set picture of '
            "current desktop to (item 1 of argv)",
            "end run",
            args=[full],
        )
        msg = "Wallpaper updated."
        audit("set_wallpaper", {"path": full}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the wallpaper."
        audit("set_wallpaper", {"path": full}, f"error: {e}")
        return msg
