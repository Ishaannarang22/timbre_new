"""
categories/display.py — display info & brightness tools.

Three things the user reaches for by voice about the screen:
  * what display(s) am I on (read-only system info),
  * make it brighter / dimmer (the F1/F2 brightness keys),
  * read or set the current brightness level if the hardware/OS allows it.

All Risk.SAFE, category="display": none of these lose data or drop connectivity, so they run
immediately with no confirmation.

House style (matches src/mac_actions.py + the runner contract):
  * shell out ONLY via run_shell / run_osa (no shell=True, no string interpolation of caller
    text — the one caller value here, a brightness percentage, is clamped to an int we control);
  * clamp / validate; default-deny on bad input with a friendly string;
  * audit() every action; NEVER raise into the pipeline — catch and speak a friendly error.

Hardware/permission reality on macOS:
  * `system_profiler SPDisplaysDataType` is always readable (no TCC prompt) — get_display_info
    is therefore the most reliable tool here.
  * The brightness KEYS (key code 144 = brighter, 145 = dimmer) are sent via System Events and
    need Accessibility permission. If it isn't granted, osascript returns -1743/-1712; we catch
    it and say it's unavailable rather than raising.
  * There is no built-in CLI to READ/SET an exact brightness level on every Mac (the old
    `brightness` tool isn't installed and Apple's `corebrightnessdiag` is read-only/internal).
    So get_brightness / set_brightness are BEST-EFFORT: get tries IODisplay via system_profiler
    and reports "not available" if it can't; set nudges with the brightness keys toward a target
    and is honest that it's approximate. We never pretend to a precision the OS won't give us.
"""

import re
import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, clamp, run_osa, run_shell

# Absolute path so run_shell never has to resolve it / fall through to a shell.
_SYSTEM_PROFILER = "/usr/sbin/system_profiler"
# system_profiler can be slow (it enumerates hardware) — give it more than the osascript 5s.
_PROFILER_TIMEOUT = 15.0

# macOS virtual key codes for the brightness media keys (Apple keyboard F1/F2).
_KEY_BRIGHTNESS_UP = 144
_KEY_BRIGHTNESS_DOWN = 145
# Each tap of the key moves brightness by ~1/16 (~6%). Used to estimate how many taps to send
# when nudging toward a target in set_brightness.
_BRIGHTNESS_STEP_PCT = 100.0 / 16.0


# --- DISPLAY INFO (read-only, always available) -------------------------------


@tool(
    name="get_display_info",
    description=(
        "Report information about this Mac's display(s): the display name(s) and resolution(s). "
        "Read-only — just tells you what screens are connected."
    ),
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="display",
)
def get_display_info() -> str:
    try:
        # Read-only hardware query. No caller input at all; fixed argv.
        out = run_shell(
            [_SYSTEM_PROFILER, "SPDisplaysDataType"], timeout=_PROFILER_TIMEOUT
        )
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read the display info."
        audit("get_display_info", {}, f"error: {e}")
        return msg

    # Parse the (indented) text output. The actual screens are nested under a "Displays:" line;
    # ABOVE that are GPU/chipset headers (e.g. "Apple M4:") which are NOT displays. So we only
    # treat a "name:" header as a display once we've seen "Displays:", and we pair each display
    # with the FIRST "Resolution:" line that follows it (rather than zipping two lists by index,
    # which mis-aligns when a display lacks a resolution line). We also strip the long
    # parenthetical from the resolution so the spoken summary stays short.
    displays: list[tuple[str, str | None]] = []  # (name, resolution-or-None)
    in_displays = False
    pending_name: str | None = None

    def _flush(res: str | None) -> None:
        nonlocal pending_name
        if pending_name is not None:
            displays.append((pending_name, res))
            pending_name = None

    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "Displays:":
            in_displays = True
            continue
        if not in_displays:
            continue  # still in the GPU/chipset preamble — ignore headers there
        if line.startswith("Resolution:"):
            # This resolution belongs to the most recent display header we saw.
            res = line.split(":", 1)[1].strip()
            res = res.split("(", 1)[0].strip()  # drop the "(QHD/WQHD - ...)" tail
            if pending_name is not None:
                _flush(res)
            continue
        if line.endswith(":"):
            # A new display header — close out any prior one that had no resolution line.
            _flush(None)
            pending_name = line[:-1]
    _flush(None)  # trailing display with no resolution line

    if not displays:
        msg = "I couldn't make sense of the display info."
        audit("get_display_info", {}, msg)
        return msg

    parts = [f"{name} at {res}" if res else name for name, res in displays]
    if len(parts) == 1:
        msg = f"You're on {parts[0]}."
    else:
        msg = "Displays: " + "; ".join(parts) + "."
    audit("get_display_info", {}, msg)
    return msg


# --- BRIGHTNESS KEYS (best-effort, needs Accessibility) -----------------------


def _tap_brightness(key_code: int, times: int) -> None:
    """Send the brightness up/down media key `times` times. Raises subprocess.SubprocessError
    if System Events can't (e.g. no Accessibility grant). key_code is from our OWN constants,
    times is a small int we control — nothing caller-derived reaches AppleScript."""
    n = max(1, min(20, int(times)))  # bound the loop so a bad caller can't spam the keyboard
    run_osa(
        "on run argv",
        "  repeat (item 1 of argv) as integer times",
        f"    tell application \"System Events\" to key code {int(key_code)}",
        "    delay 0.05",
        "  end repeat",
        "end run",
        args=[n],
    )


@tool(
    name="brightness_up",
    description=(
        "Make this Mac's screen brighter (presses the brightness-up key one or more steps). "
        "Optionally pass steps (default 1) for how many notches to raise it."
    ),
    properties={
        "steps": {
            "type": "integer",
            "description": "How many brightness notches to go up (default 1).",
        }
    },
    required=[],
    risk=Risk.SAFE,
    category="display",
)
def brightness_up(steps: int = 1) -> str:
    n = clamp(steps, lo=1, hi=16)
    try:
        _tap_brightness(_KEY_BRIGHTNESS_UP, n)
        msg = "Brightness up." if n == 1 else f"Brightness up {n} steps."
        audit("brightness_up", {"steps": n}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = (
            "I couldn't change the brightness — I may need Accessibility permission to use the "
            "brightness keys."
        )
        audit("brightness_up", {"steps": n}, f"error: {e}")
        return msg


@tool(
    name="brightness_down",
    description=(
        "Make this Mac's screen dimmer (presses the brightness-down key one or more steps). "
        "Optionally pass steps (default 1) for how many notches to lower it."
    ),
    properties={
        "steps": {
            "type": "integer",
            "description": "How many brightness notches to go down (default 1).",
        }
    },
    required=[],
    risk=Risk.SAFE,
    category="display",
)
def brightness_down(steps: int = 1) -> str:
    n = clamp(steps, lo=1, hi=16)
    try:
        _tap_brightness(_KEY_BRIGHTNESS_DOWN, n)
        msg = "Brightness down." if n == 1 else f"Brightness down {n} steps."
        audit("brightness_down", {"steps": n}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = (
            "I couldn't change the brightness — I may need Accessibility permission to use the "
            "brightness keys."
        )
        audit("brightness_down", {"steps": n}, f"error: {e}")
        return msg


# --- BRIGHTNESS LEVEL (best-effort read; approximate set) ---------------------
# There's no universal CLI to read/set an exact brightness on every Mac. We try system_profiler
# (some Macs report "Brightness:" for the built-in panel) for a read, and approximate a set by
# tapping the brightness keys toward the requested target. Both are honest when they can't.

_BRIGHTNESS_RE = re.compile(r"Brightness:\s*([0-9]+)%?", re.IGNORECASE)


def _read_brightness_pct() -> int | None:
    """Best-effort current brightness as 0-100, or None if the OS won't tell us. Raises only on
    the system_profiler call failing (caller catches)."""
    out = run_shell([_SYSTEM_PROFILER, "SPDisplaysDataType"], timeout=_PROFILER_TIMEOUT)
    m = _BRIGHTNESS_RE.search(out)
    if not m:
        return None
    return clamp(m.group(1))


@tool(
    name="get_brightness",
    description=(
        "Report this Mac's current screen brightness as a percentage, if the system exposes it. "
        "On some Macs the exact level isn't available — this will say so."
    ),
    properties={},
    required=[],
    risk=Risk.SAFE,
    category="display",
)
def get_brightness() -> str:
    try:
        pct = _read_brightness_pct()
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read the brightness."
        audit("get_brightness", {}, f"error: {e}")
        return msg
    if pct is None:
        msg = "This Mac doesn't report an exact brightness level, so I can't read it."
        audit("get_brightness", {}, msg)
        return msg
    msg = f"Screen brightness is about {pct} percent."
    audit("get_brightness", {}, msg)
    return msg


@tool(
    name="set_brightness",
    description=(
        "Set this Mac's screen brightness to roughly a target percentage (0 to 100). This is "
        "APPROXIMATE — it nudges the brightness keys toward the target — and needs the system "
        "to report the current level. Use brightness_up / brightness_down for simple steps."
    ),
    properties={
        "level": {
            "type": "integer",
            "description": "Target brightness percentage, 0 to 100.",
        }
    },
    required=["level"],
    risk=Risk.SAFE,
    category="display",
)
def set_brightness(level: int = 50) -> str:
    target = clamp(level)  # 0-100, int we control
    try:
        current = _read_brightness_pct()
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't set the brightness."
        audit("set_brightness", {"level": target}, f"error: {e}")
        return msg

    if current is None:
        # We can't measure where we are, so we can't aim — be honest and point at the steppers.
        msg = (
            "This Mac doesn't report its brightness level, so I can't set an exact percentage — "
            "try asking me to make it brighter or dimmer instead."
        )
        audit("set_brightness", {"level": target}, msg)
        return msg

    delta = target - current
    if abs(delta) < (_BRIGHTNESS_STEP_PCT / 2):
        msg = f"Brightness is already about {current} percent."
        audit("set_brightness", {"level": target, "current": current}, msg)
        return msg

    # Estimate notches and tap toward the target. _tap_brightness bounds the count internally.
    steps = max(1, round(abs(delta) / _BRIGHTNESS_STEP_PCT))
    key = _KEY_BRIGHTNESS_UP if delta > 0 else _KEY_BRIGHTNESS_DOWN
    try:
        _tap_brightness(key, steps)
    except subprocess.SubprocessError as e:
        msg = (
            "I couldn't adjust the brightness — I may need Accessibility permission to use the "
            "brightness keys."
        )
        audit("set_brightness", {"level": target, "current": current}, f"error: {e}")
        return msg

    direction = "up" if delta > 0 else "down"
    msg = f"Nudged brightness {direction} toward about {target} percent."
    audit("set_brightness", {"level": target, "current": current, "steps": steps}, msg)
    return msg
