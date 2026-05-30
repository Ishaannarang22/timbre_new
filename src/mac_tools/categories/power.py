"""
power.py — category="power": lock/sleep/screensaver/logout/restart/shutdown/empty-trash.

House style (matches src/mac_actions.py and the other category modules exactly): every
handler shells out ONLY via runner.run_osa / run_shell (no shell=True; these tools take no
caller text, so there is nothing to interpolate or pass as argv), audits each action, NEVER
raises into the voice pipeline, and returns a SHORT spoken-friendly string.

Risk policy (docs/tooling/CONTRACT.md): EVERY tool here is Risk.CONFIRM. Locking, sleeping,
starting the screensaver, logging out, restarting, shutting down, and emptying the Trash all
disrupt the machine (and would drop a live call), so each is staged with a clear spoken
read-back and runs ONLY after the owner confirms via the broker. Empty-trash is the one
"deletion" here, but it's the OS's own Trash-empty (recoverable until then) and still gated.

NONE of these take caller arguments, so confirm_summary is a fixed read-back per tool. The
actual handlers run only on confirm and are therefore NOT executed during autonomous tests.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa

# pmset lives here on macOS; used for display sleep.
_PMSET = "/usr/bin/pmset"


@tool(
    "lock_screen",
    "Lock this Mac's screen (requires the password to get back in). Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Lock the screen?",
)
def lock_screen() -> str:
    """Lock the screen (runs ONLY after the owner confirms). Uses the keychain lock command via
    System Events' built-in lock menu. No caller input. Never raises."""
    try:
        # Trigger the standard "Lock Screen" via the Keyboard shortcut equivalent (Cmd-Ctrl-Q)
        # using System Events keystroke — the most reliable cross-version lock.
        run_osa(
            'tell application "System Events" to keystroke "q" using {control down, command down}'
        )
        msg = "Locked the screen."
        audit("lock_screen", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't lock the screen."
        audit("lock_screen", {}, f"error: {e}")
        return msg


@tool(
    "sleep_display",
    "Put this Mac's display to sleep (turn the screen off) while keeping the system awake. "
    "Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Put the display to sleep?",
)
def sleep_display() -> str:
    """Sleep just the display (runs ONLY after the owner confirms). `pmset displaysleepnow`.
    No caller input. Never raises."""
    try:
        run_osa('do shell script "/usr/bin/pmset displaysleepnow"')
        msg = "Put the display to sleep."
        audit("sleep_display", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't sleep the display."
        audit("sleep_display", {}, f"error: {e}")
        return msg


@tool(
    "system_sleep",
    "Put this whole Mac to sleep. Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Put the Mac to sleep?",
)
def system_sleep() -> str:
    """Sleep the whole system (runs ONLY after the owner confirms). No caller input. Never
    raises."""
    try:
        run_osa('tell application "System Events" to sleep')
        msg = "Going to sleep."
        audit("system_sleep", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't put the Mac to sleep."
        audit("system_sleep", {}, f"error: {e}")
        return msg


@tool(
    "start_screensaver",
    "Start the screen saver on this Mac. Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Start the screen saver?",
)
def start_screensaver() -> str:
    """Start the screensaver (runs ONLY after the owner confirms). Launches the system
    ScreenSaverEngine. No caller input. Never raises."""
    try:
        run_osa(
            'tell application "System Events" to start current screen saver'
        )
        msg = "Starting the screen saver."
        audit("start_screensaver", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't start the screen saver."
        audit("start_screensaver", {}, f"error: {e}")
        return msg


@tool(
    "logout",
    "Log out of this Mac's current user session (closes apps). Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Log out of your account now?",
)
def logout() -> str:
    """Log out the current user (runs ONLY after the owner confirms). Sends the loginwindow
    'log out' Apple event. No caller input. Never raises."""
    try:
        # `log out` event to loginwindow. Using the standard System Events / loginwindow path.
        run_osa('tell application "loginwindow" to «event aevtrlgo»')
        msg = "Logging out."
        audit("logout", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't log out."
        audit("logout", {}, f"error: {e}")
        return msg


@tool(
    "restart",
    "Restart (reboot) this Mac. Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Restart the Mac now?",
)
def restart() -> str:
    """Restart the Mac (runs ONLY after the owner confirms). Sends the System Events 'restart'
    event. No caller input. Never raises."""
    try:
        run_osa('tell application "System Events" to restart')
        msg = "Restarting now."
        audit("restart", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't restart the Mac."
        audit("restart", {}, f"error: {e}")
        return msg


@tool(
    "shutdown",
    "Shut down (power off) this Mac. Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Shut down the Mac now?",
)
def shutdown() -> str:
    """Shut the Mac down (runs ONLY after the owner confirms). Sends the System Events
    'shut down' event. No caller input. Never raises."""
    try:
        run_osa('tell application "System Events" to shut down')
        msg = "Shutting down now."
        audit("shutdown", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't shut down the Mac."
        audit("shutdown", {}, f"error: {e}")
        return msg


@tool(
    "empty_trash",
    "Empty this Mac's Trash, permanently removing what's in it. Confirmed first.",
    risk=Risk.CONFIRM,
    category="power",
    confirm_summary=lambda: "Empty the Trash? This can't be undone.",
)
def empty_trash() -> str:
    """Empty the Trash (runs ONLY after the owner confirms). This is the one irreversible action
    here, so it is — like every power tool — CONFIRM-gated and read back. Uses Finder's own
    `empty trash` (the OS's Trash-empty, not a raw rm). No caller input. Never raises."""
    try:
        run_osa('tell application "Finder" to empty trash')
        msg = "Emptied the Trash."
        audit("empty_trash", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't empty the Trash."
        audit("empty_trash", {}, f"error: {e}")
        return msg
