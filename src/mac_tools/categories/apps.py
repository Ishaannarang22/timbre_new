"""
apps.py — category="apps": launch / activate / hide / list / quit applications.

House style (matches src/mac_actions.py exactly): every handler validates/clamps its input,
shells out ONLY via runner.run_osa / run_shell (no shell=True, caller values reach AppleScript
solely as trailing argv via `on run argv`), audits each action, NEVER raises into the pipeline,
and returns a SHORT spoken-friendly string.

Risk policy (docs/tooling/CONTRACT.md): launch/activate/hide/list/frontmost are SAFE; quit_app
is CONFIRM-gated because quitting an app can lose unsaved work.

App-name validation: we accept only letters, numbers, spaces, dots and hyphens. That rejects
slashes (no paths), so callers can never point these at an arbitrary executable on disk — only
at an installed app by name. The validated name is STILL passed as argv (defense in depth), so
even an allowed-but-weird name can't break out of the AppleScript/shell.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, frontmost_app, run_osa, run_shell
from ..runner import valid_app_name as _valid_app_name

# App-name validation (charset + length cap, default-deny on slashes/odd chars) lives in
# runner.valid_app_name — shared with windows.py so there's one source of truth. Imported above
# as `_valid_app_name` so the existing call sites are unchanged.


@tool(
    "launch_app",
    "Open (launch) an installed Mac app by name, e.g. Safari, Notes, Spotify. Brings it up "
    "if it isn't already running.",
    properties={"name": {"type": "string", "description": "The app's name, e.g. 'Safari'."}},
    required=["name"],
    risk=Risk.SAFE,
    category="apps",
)
def launch_app(name: str) -> str:
    """Launch an app via `open -a` (run_shell — no shell=True). The name is validated to a
    safe charset and then passed as a list arg, so there's no injection surface."""
    app = _valid_app_name(name)
    if app is None:
        msg = "I need a valid app name to open."
        audit("launch_app", {"name": name}, msg)
        return msg
    try:
        # `open -a <name>` launches (or foregrounds) the app by name. argv list, no shell.
        run_shell(["open", "-a", app])
        msg = f"Opened {app}."
        audit("launch_app", {"name": app}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't open {app}."
        audit("launch_app", {"name": app}, f"error: {e}")
        return msg


@tool(
    "activate_app",
    "Bring an already-running app to the front (give it focus) by name. Will also launch it "
    "if needed.",
    properties={"name": {"type": "string", "description": "The app's name, e.g. 'Notes'."}},
    required=["name"],
    risk=Risk.SAFE,
    category="apps",
)
def activate_app(name: str) -> str:
    """Activate (foreground) an app. The caller name reaches AppleScript only as argv."""
    app = _valid_app_name(name)
    if app is None:
        msg = "I need a valid app name to switch to."
        audit("activate_app", {"name": name}, msg)
        return msg
    try:
        run_osa(
            "on run argv",
            "tell application (item 1 of argv) to activate",
            "end run",
            args=[app],
        )
        msg = f"Switched to {app}."
        audit("activate_app", {"name": app}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't switch to {app}."
        audit("activate_app", {"name": app}, f"error: {e}")
        return msg


@tool(
    "hide_app",
    "Hide an app's windows (like Command-H) by name. The app keeps running, just out of "
    "sight.",
    properties={"name": {"type": "string", "description": "The app's name to hide."}},
    required=["name"],
    risk=Risk.SAFE,
    category="apps",
)
def hide_app(name: str) -> str:
    """Hide an app via System Events. Caller name passed as argv. Never raises."""
    app = _valid_app_name(name)
    if app is None:
        msg = "I need a valid app name to hide."
        audit("hide_app", {"name": name}, msg)
        return msg
    try:
        run_osa(
            "on run argv",
            'tell application "System Events" to set visible of '
            "(first application process whose name is (item 1 of argv)) to false",
            "end run",
            args=[app],
        )
        msg = f"Hid {app}."
        audit("hide_app", {"name": app}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't hide {app}."
        audit("hide_app", {"name": app}, f"error: {e}")
        return msg


@tool(
    "list_running_apps",
    "List the apps that are currently running (the visible, regular apps the user could "
    "switch to).",
    risk=Risk.SAFE,
    category="apps",
)
def list_running_apps() -> str:
    """Report the running, user-visible apps. Read-only; launches nothing."""
    try:
        # Regular (non-background) processes whose name we can speak. System Events lists
        # everything; `background only is false` trims daemons/agents to real apps.
        raw = run_osa(
            'tell application "System Events" to get name of every application process '
            "whose background only is false"
        )
        # AppleScript returns a comma-space-joined list; normalize to a clean, sorted set.
        names = sorted({p.strip() for p in raw.split(",") if p.strip()})
        if not names:
            msg = "I don't see any apps running."
            audit("list_running_apps", {}, msg)
            return msg
        msg = "Running apps: " + ", ".join(names) + "."
        audit("list_running_apps", {}, f"{len(names)} apps")
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't list the running apps."
        audit("list_running_apps", {}, f"error: {e}")
        return msg


@tool(
    "frontmost_app",
    "Say which app is currently in front (the active app the user is looking at).",
    risk=Risk.SAFE,
    category="apps",
)
def frontmost_app_tool() -> str:
    """Report the frontmost app via the runner helper. Never raises."""
    name = frontmost_app()
    if not name:
        msg = "I couldn't tell which app is in front."
        audit("frontmost_app", {}, msg)
        return msg
    msg = f"{name} is in front."
    audit("frontmost_app", {}, msg)
    return msg


@tool(
    "quit_app",
    "Quit (close) a running app by name. Quitting can lose unsaved work, so this is "
    "confirmed first.",
    properties={"name": {"type": "string", "description": "The app's name to quit."}},
    required=["name"],
    risk=Risk.CONFIRM,
    category="apps",
    # Spoken read-back built by dispatch() before anything happens. Mirror the validation so
    # the read-back never echoes a bad name; the real handler validates again on confirm.
    confirm_summary=lambda name="": f"Quit {(_valid_app_name(name) or str(name).strip())}?",
)
def quit_app(name: str) -> str:
    """Quit an app (CONFIRM-gated). Runs only after the owner confirms via the broker. Caller
    name reaches AppleScript only as argv; never raises."""
    app = _valid_app_name(name)
    if app is None:
        msg = "I need a valid app name to quit."
        audit("quit_app", {"name": name}, msg)
        return msg
    try:
        run_osa(
            "on run argv",
            "tell application (item 1 of argv) to quit",
            "end run",
            args=[app],
        )
        msg = f"Quit {app}."
        audit("quit_app", {"name": app}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't quit {app}."
        audit("quit_app", {"name": app}, f"error: {e}")
        return msg
