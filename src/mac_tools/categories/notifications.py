"""
categories/notifications.py — registry tools for posting macOS notifications.

One tool: `notify(title, message)` posts a SILENT Notification Center banner via osascript's
`display notification`. "Silent" is deliberate (CONTRACT testing rule: make no noise) — we
do NOT pass `sound name`, so no audible alert is ever played.

House style (matches src/mac_actions.py + the runner contract):
  * Both `title` and `message` originate from the caller/LLM, so they are passed to AppleScript
    as TRAILING argv (read via `on run argv`) — NEVER string-interpolated into a script line.
    That kills the AppleScript/shell injection surface even though the text comes from an LLM.
  * Validate/default-deny on empty input with a friendly spoken string.
  * audit() every action.
  * Catch all expected exceptions; return a friendly failure string — never raise.

Risk.SAFE, category="notifications": showing a banner is non-destructive and recoverable.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa


@tool(
    name="notify",
    description=(
        "Show a notification banner on this Mac (Notification Center). Use this to surface a "
        "short reminder or message visually on screen, e.g. 'remind me to call mom' or 'put up "
        "a note that the build is done'. The banner is silent (no sound)."
    ),
    properties={
        "title": {"type": "string", "description": "Short notification title/heading."},
        "message": {"type": "string", "description": "The notification body text."},
    },
    required=["title", "message"],
    risk=Risk.SAFE,
    category="notifications",
)
def notify(title: str = "", message: str = "") -> str:
    """Post a SILENT notification banner. Returns a SHORT spoken-friendly string.

    Caller text (title + message) reaches AppleScript ONLY as argv via `on run argv`, so there
    is no injection surface. We deliberately omit `sound name`, so nothing audible plays."""
    t = str(title).strip()
    m = str(message).strip()
    if not m:
        # A notification needs body text to be meaningful; title alone is optional in AppleScript
        # but a bare banner isn't useful, so default-deny on empty message.
        msg = "Tell me what the notification should say."
        audit("notify", {"title": t, "message": m}, msg)
        return msg

    try:
        # title/message are DYNAMIC caller values -> trailing argv, read via `on run argv`.
        # `display notification <body> with title <title>` posts a banner; NO `sound name`
        # clause means it is silent (CONTRACT: make no noise).
        run_osa(
            "on run argv",
            "display notification (item 1 of argv) with title (item 2 of argv)",
            "end run",
            args=[m, t or "Notification"],
        )
        spoken = f"Posted a notification: {t}." if t else "Posted that notification."
        audit("notify", {"title": t, "message": m}, spoken)
        return spoken
    except subprocess.SubprocessError as e:
        spoken = "Sorry, I couldn't post that notification."
        audit("notify", {"title": t, "message": m}, f"error: {e}")
        return spoken
