"""
input_control.py — category="input": synthetic keyboard + mouse input via System Events.

⚠️  DANGER / LIVE-SESSION WARNING ⚠️
These tools INJECT real keystrokes and mouse events into whatever app is frontmost RIGHT NOW.
They are not sandboxed — typing text, pressing keys, or clicking goes to the live desktop, so a
stray call can trigger destructive UI actions (Command-Delete, Command-Q, hitting a "Delete"
button, etc.) in whatever window happens to be in front. Because the EFFECT depends entirely on
the unknown frontmost app, these can be far more dangerous than their SAFE risk tag implies in
isolation. NEVER execute these during autonomous testing — validate registration/schema ONLY.
(They need macOS Accessibility permission; without it System Events errors / times out -1712.)

They are tagged SAFE per the contract (no Trash delete / send / power / network of their own),
but the system prompt + the owner-authorization gate are what actually keep them in check.

House style (matches src/mac_actions.py): validate/clamp input, shell out only via
runner.run_osa / runner.run_shell (caller TEXT reaches AppleScript solely as trailing argv via
`on run argv` — never interpolated, so no AppleScript/shell injection even though it comes from
the LLM; the only run_shell use here is the `cliclick` pointer path, fed our own clamped ints),
audit each action, NEVER raise, return a SHORT spoken-friendly string.
"""

import re
import shutil
import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, clamp, run_osa, run_shell

# Modifier words the LLM/user might say -> the AppleScript `keystroke ... using {...}` token.
# Fixed enum (we control these strings; only our own values are ever inlined into the script).
_MODIFIER_MAP = {
    "command": "command down",
    "cmd": "command down",
    "⌘": "command down",
    "option": "option down",
    "opt": "option down",
    "alt": "option down",
    "⌥": "option down",
    "control": "control down",
    "ctrl": "control down",
    "⌃": "control down",
    "shift": "shift down",
    "⇧": "shift down",
}

# Named special keys -> AppleScript `key code` numbers. Single printable characters go through
# `keystroke` instead (handled below). Fixed map -> only our own ints reach the script.
_KEY_CODE_MAP = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "spacebar": 49,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "forward delete": 117,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    "home": 115,
    "end": 119,
    "page up": 116,
    "page down": 121,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

# Cap typed text so a runaway LLM can't paste a novel into the live session.
_MAX_TYPE_LEN = 2000

# Pointer MOVEMENT is the honest tricky bit: standard `System Events` exposes the cursor
# position as a READ-ONLY property, so `set mouse location to {...}` is NOT real — it errors (or
# is ignored), meaning the old mouse_move silently no-op'd while reporting success. The reliable
# way to MOVE the pointer is the third-party `cliclick` CLI (`m:x,y` to move, `c:x,y` to click),
# if it happens to be installed. We DON'T add it as a dependency (the contract forbids new pip
# deps and this is a binary anyway); we just USE it when present and are HONEST when it isn't.
# This makes pointer control a good candidate for request_new_tool later.
def _cliclick() -> str | None:
    """Absolute path to the `cliclick` binary if it's installed, else None. Discovery only —
    never installs anything."""
    return shutil.which("cliclick")


# Friendly, honest message when we can't move the pointer (no cliclick) — better than lying.
_NO_POINTER_TOOL = (
    "I can't move the pointer on this Mac without a helper tool (cliclick) installed."
)


def _modifier_clause(modifiers) -> str:
    """Turn a list/str of modifier words into an AppleScript `using {...}` clause, or "".
    Unknown modifiers are dropped (default-deny). Only our own fixed tokens are emitted."""
    if not modifiers:
        return ""
    if isinstance(modifiers, str):
        parts = re.split(r"[\s,+]+", modifiers)
    elif isinstance(modifiers, (list, tuple)):
        parts = modifiers
    else:
        return ""
    tokens = []
    for m in parts:
        tok = _MODIFIER_MAP.get(str(m).strip().lower())
        if tok and tok not in tokens:
            tokens.append(tok)
    if not tokens:
        return ""
    return " using {" + ", ".join(tokens) + "}"


@tool(
    "type_text",
    "Type a string of text into the frontmost app, as if typed on the keyboard. Goes to "
    "whatever window is in front right now.",
    properties={"text": {"type": "string", "description": "The text to type."}},
    required=["text"],
    risk=Risk.SAFE,
    category="input",
)
def type_text(text: str) -> str:
    """Inject text via System Events `keystroke`. The text reaches AppleScript ONLY as argv
    (`on run argv`) so there is no interpolation/injection. Caps length; never raises.

    ⚠️ Sends real keystrokes to the live frontmost app — do not execute in tests."""
    s = str(text or "")
    if not s:
        msg = "There's no text to type."
        audit("type_text", {"text": text}, msg)
        return msg
    if len(s) > _MAX_TYPE_LEN:
        s = s[:_MAX_TYPE_LEN]
    try:
        run_osa(
            "on run argv",
            'tell application "System Events" to keystroke (item 1 of argv)',
            "end run",
            args=[s],
        )
        preview = s if len(s) <= 40 else s[:40] + "…"
        msg = f"Typed: {preview}"
        audit("type_text", {"len": len(s)}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't type that."
        audit("type_text", {"len": len(s)}, f"error: {e}")
        return msg


@tool(
    "press_key",
    "Press a single key (optionally with modifiers), e.g. Return, Escape, the arrow keys, or "
    "a letter with Command/Option/Control/Shift held.",
    properties={
        "key": {
            "type": "string",
            "description": "Key name (return, tab, escape, up, down, f5, …) or a single "
            "character to type.",
        },
        "modifiers": {
            "type": "string",
            "description": "Optional modifiers held down, e.g. 'command' or 'command+shift'.",
        },
    },
    required=["key"],
    risk=Risk.SAFE,
    category="input",
)
def press_key(key: str, modifiers: str = "") -> str:
    """Press one key with optional modifiers. Named keys -> `key code` (from our fixed map);
    a single printable char -> `keystroke` with the char passed as argv. The modifier clause is
    built only from our own fixed tokens. Never raises.

    ⚠️ Sends a real key event to the live frontmost app — do not execute in tests."""
    k = str(key or "").strip()
    if not k:
        msg = "Tell me which key to press."
        audit("press_key", {"key": key}, msg)
        return msg
    using = _modifier_clause(modifiers)
    try:
        code = _KEY_CODE_MAP.get(k.lower())
        if code is not None:
            # Named special key -> `key code N`. N is our own int from the fixed map (safe to
            # inline); modifier clause is also our own fixed tokens.
            run_osa(
                f'tell application "System Events" to key code {code}{using}',
            )
        elif len(k) == 1:
            # Single printable character -> keystroke, char passed as argv (never inlined).
            run_osa(
                "on run argv",
                f'tell application "System Events" to keystroke (item 1 of argv){using}',
                "end run",
                args=[k],
            )
        else:
            msg = f"I don't recognize the key '{k}'."
            audit("press_key", {"key": k}, msg)
            return msg
        label = k if not using else f"{modifiers}+{k}"
        msg = f"Pressed {label}."
        audit("press_key", {"key": k, "modifiers": modifiers}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't press {k}."
        audit("press_key", {"key": k, "modifiers": modifiers}, f"error: {e}")
        return msg


@tool(
    "key_combo",
    "Press a keyboard shortcut written as 'mod+mod+key', e.g. 'cmd+s' to save or "
    "'cmd+shift+t'.",
    properties={
        "combo": {
            "type": "string",
            "description": "Shortcut like 'cmd+s', 'command+shift+t', 'ctrl+c'.",
        }
    },
    required=["combo"],
    risk=Risk.SAFE,
    category="input",
)
def key_combo(combo: str) -> str:
    """Parse 'mod+mod+key' (last token is the key, the rest are modifiers) and dispatch via
    press_key, which keeps all the injection-safe handling. Never raises.

    ⚠️ Sends a real shortcut to the live frontmost app — do not execute in tests."""
    c = str(combo or "").strip()
    if not c:
        msg = "Tell me which shortcut to press."
        audit("key_combo", {"combo": combo}, msg)
        return msg
    parts = [p for p in re.split(r"[\s+]+", c) if p]
    if len(parts) < 1:
        msg = f"I couldn't parse the shortcut '{c}'."
        audit("key_combo", {"combo": c}, msg)
        return msg
    *mods, key = parts
    # Only treat leading tokens as modifiers if they're known modifiers; otherwise the whole
    # thing was a single key. Reuse press_key for the actual (injection-safe) injection.
    mod_str = "+".join(m for m in mods if str(m).lower() in _MODIFIER_MAP)
    return press_key(key, mod_str)


@tool(
    "mouse_click",
    "Click the mouse at absolute screen coordinates (x, y), measured in points from the "
    "top-left of the main screen.",
    properties={
        "x": {"type": "integer", "description": "Horizontal position in points."},
        "y": {"type": "integer", "description": "Vertical position in points."},
    },
    required=["x", "y"],
    risk=Risk.SAFE,
    category="input",
)
def mouse_click(x: int, y: int) -> str:
    """Click at (x, y). Coordinates are clamped to a sane on-screen range and used as our OWN
    clamped ints (never raw caller text). Prefers the `cliclick` CLI (`c:x,y`) when installed —
    it both moves the pointer and clicks reliably — otherwise falls back to System Events
    `click at {x, y}`, which clicks at the point (the supported verb) without visibly moving the
    cursor. Never raises.

    ⚠️ Performs a real click on the live desktop — do not execute in tests."""
    cx = clamp(x, 0, 20000)
    cy = clamp(y, 0, 20000)
    cli = _cliclick()
    try:
        if cli:
            # cliclick c:x,y — coords are our own clamped ints, passed as one list arg (no shell).
            run_shell([cli, f"c:{cx},{cy}"])
        else:
            run_osa(f'tell application "System Events" to click at {{{cx}, {cy}}}')
        msg = f"Clicked at {cx}, {cy}."
        audit("mouse_click", {"x": cx, "y": cy}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't click there."
        audit("mouse_click", {"x": cx, "y": cy}, f"error: {e}")
        return msg


@tool(
    "mouse_move",
    "Move the mouse pointer to absolute screen coordinates (x, y), in points from the "
    "top-left of the main screen, without clicking.",
    properties={
        "x": {"type": "integer", "description": "Horizontal position in points."},
        "y": {"type": "integer", "description": "Vertical position in points."},
    },
    required=["x", "y"],
    risk=Risk.SAFE,
    category="input",
)
def mouse_move(x: int, y: int) -> str:
    """Move the pointer to (x, y) without clicking. HONEST about the macOS reality: standard
    System Events has NO settable cursor position (`set mouse location` is not real — it silently
    does nothing while the old code reported success), so we move the pointer ONLY via the
    `cliclick` CLI (`m:x,y`) when it's installed. If it isn't, we say so plainly rather than lie.
    Coordinates are clamped and used as our own ints (never raw caller text). Never raises.

    ⚠️ Moves the real pointer on the live desktop — do not execute in tests."""
    cx = clamp(x, 0, 20000)
    cy = clamp(y, 0, 20000)
    cli = _cliclick()
    if not cli:
        # No real way to move the pointer without a helper — be honest instead of faking success.
        audit("mouse_move", {"x": cx, "y": cy}, _NO_POINTER_TOOL)
        return _NO_POINTER_TOOL
    try:
        # cliclick m:x,y moves (no click). Coords are our own clamped ints, one list arg, no shell.
        run_shell([cli, f"m:{cx},{cy}"])
        msg = f"Moved the pointer to {cx}, {cy}."
        audit("mouse_move", {"x": cx, "y": cy}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't move the pointer."
        audit("mouse_move", {"x": cx, "y": cy}, f"error: {e}")
        return msg
