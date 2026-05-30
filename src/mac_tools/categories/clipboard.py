"""
clipboard.py — category="clipboard": read, write, and clear the macOS pasteboard.

All three are SAFE (reading/writing the clipboard is low-risk and recoverable). House style
matches src/mac_actions.py and the runner contract:
  * Sync fn(**args) -> SHORT spoken-friendly string. NEVER raises into the pipeline.
  * Everything goes through run_shell (list args, no shell=True) — pbpaste/pbcopy. The text we
    set is fed to pbcopy via stdin (run_shell's input_text), so caller text is never put on a
    command line at all (no quoting/injection surface).
  * audit() on every action.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_shell

CATEGORY = "clipboard"

_PBPASTE = "/usr/bin/pbpaste"
_PBCOPY = "/usr/bin/pbcopy"

# Cap what we read back so a giant clipboard can't flood the spoken context. The clipboard is
# still fully set/cleared regardless; this only bounds what get_clipboard returns to speak.
_READ_CAP = 4000


@tool(
    "get_clipboard",
    "Read what's currently on the clipboard.",
    risk=Risk.SAFE,
    category=CATEGORY,
)
def get_clipboard() -> str:
    try:
        text = run_shell([_PBPASTE])
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read the clipboard."
        audit("get_clipboard", {}, f"error: {e}")
        return msg
    if not text:
        msg = "The clipboard is empty."
        audit("get_clipboard", {}, msg)
        return msg
    out = text if len(text) <= _READ_CAP else text[:_READ_CAP] + " ...(truncated)"
    audit("get_clipboard", {}, f"{len(text)} chars")
    return out


@tool(
    "set_clipboard",
    "Put the given text onto the clipboard.",
    properties={"text": {"type": "string", "description": "The text to copy to the clipboard."}},
    required=["text"],
    risk=Risk.SAFE,
    category=CATEGORY,
)
def set_clipboard(text: str) -> str:
    # Coerce to a string; None/other types become a sensible value rather than raising.
    payload = "" if text is None else str(text)
    try:
        # Caller text is piped to pbcopy via STDIN (input_text) — it never appears on a command
        # line, so there is nothing to quote or inject.
        run_shell([_PBCOPY], input_text=payload)
        msg = "Copied that to the clipboard."
        audit("set_clipboard", {"chars": len(payload)}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't set the clipboard."
        audit("set_clipboard", {"chars": len(payload)}, f"error: {e}")
        return msg


@tool(
    "clear_clipboard",
    "Clear the clipboard (empty it).",
    risk=Risk.SAFE,
    category=CATEGORY,
)
def clear_clipboard() -> str:
    try:
        # Clearing = copying an empty string in via stdin.
        run_shell([_PBCOPY], input_text="")
        msg = "Cleared the clipboard."
        audit("clear_clipboard", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't clear the clipboard."
        audit("clear_clipboard", {}, f"error: {e}")
        return msg
