"""
screen.py — category="screen": take a (silent) screenshot.

(Display info lives in display.py's `get_display_info`; a duplicate `screen_info` tool that
used to live here was removed.)

SAFE. House style matches src/mac_actions.py and the runner contract:
  * Sync fn(**args) -> SHORT spoken-friendly string. NEVER raises into the pipeline.
  * Everything goes through run_shell (list args, no shell=True).
  * audit() on every action.

NO SOUND: take_screenshot ALWAYS uses `screencapture -x`. The -x flag MUTES the camera/shutter
SOUND — it is mandatory here (autonomous, no-noise environment). The screenshot is written to a
timestamped file under /tmp (default) or ~/Desktop, and we return that path.
"""

import os
import subprocess
import time

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_shell

CATEGORY = "screen"

_SCREENCAPTURE = "/usr/sbin/screencapture"

# Where a screenshot lands when the caller doesn't name a path. /tmp keeps the autonomous test
# from littering the user's Desktop; the agent can also ask for ~/Desktop explicitly.
_DEFAULT_DIR = "/tmp"
_DESKTOP_DIR = os.path.expanduser("~/Desktop")


def _timestamped_name() -> str:
    return f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"


@tool(
    "take_screenshot",
    "Take a screenshot of the whole screen and save it to a file. Returns the file path.",
    properties={
        "path": {
            "type": "string",
            "description": (
                "Optional destination. A directory saves a timestamped PNG there; a full "
                "path is used as-is. Defaults to a timestamped PNG in /tmp."
            ),
        }
    },
    required=[],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def take_screenshot(path: str = "") -> str:
    # Resolve the destination. Empty -> timestamped file in /tmp. A directory (existing, or the
    # literal word "desktop") -> timestamped file inside it. Anything else -> used as a full path.
    raw = "" if path is None else str(path).strip()
    if not raw:
        dest = os.path.join(_DEFAULT_DIR, _timestamped_name())
    else:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        if expanded.lower() in ("desktop", "~/desktop", _DESKTOP_DIR.lower()):
            dest = os.path.join(_DESKTOP_DIR, _timestamped_name())
        elif os.path.isdir(expanded):
            dest = os.path.join(expanded, _timestamped_name())
        else:
            # Treat as a full file path; ensure it ends in .png for a sane default.
            dest = expanded if expanded.lower().endswith(".png") else expanded + ".png"

    try:
        # -x is MANDATORY: it mutes the screenshot SOUND. dest is a separate list arg (no shell).
        run_shell([_SCREENCAPTURE, "-x", dest])
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't take the screenshot."
        audit("take_screenshot", {"path": dest}, f"error: {e}")
        return msg

    if not os.path.exists(dest):
        msg = "Sorry, the screenshot didn't save."
        audit("take_screenshot", {"path": dest}, msg)
        return msg

    msg = f"Saved a screenshot to {dest}."
    audit("take_screenshot", {"path": dest}, msg)
    return msg


# NOTE: a `screen_info` tool (resolution + display count) used to live here, but it duplicated
# display.py's `get_display_info` (richer: it reports display NAMES + resolutions from the same
# `system_profiler SPDisplaysDataType` parse). Offering both confused tool selection, so
# screen_info was removed — display.py is the single home for display info.
