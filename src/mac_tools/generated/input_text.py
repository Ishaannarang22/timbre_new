"""generated/input_text.py — type arbitrary text into the frontmost app. category="input"."""

from mac_tools.policy import Risk
from mac_tools.registry import tool
from mac_tools.runner import audit, run_osa


@tool(
    name="input_text",
    description="Type arbitrary text into the frontmost app, as if typed on the keyboard. "
    "Use this to input any string directly into whatever window is currently in front, "
    "for example 'my name is Ishaan'.",
    properties={
        "text": {
            "type": "string",
            "description": "The text to type into the frontmost app's active text field.",
        }
    },
    required=["text"],
    risk=Risk.SAFE,
    category="input",
)
def input_text(text: str = "") -> str:
    """Type the given text into the frontmost app via System Events keystroke."""
    if not text:
        msg = "No text was provided to type."
        audit("input_text", {"text": ""}, msg)
        return msg
    try:
        # The caller-supplied string is passed safely via args/argv — never interpolated.
        run_osa(
            "on run argv",
            "  tell application \"System Events\"",
            "    keystroke (item 1 of argv)",
            "  end tell",
            "end run",
            args=[text],
        )
        msg = "Typed the text into the frontmost app."
        audit("input_text", {"text_length": len(text)}, msg)
        return msg
    except Exception as e:
        msg = (
            "Sorry, I couldn't type the text. "
            "Make sure the app you want to type into is in front "
            "and has a text field focused, and that accessibility is allowed."
        )
        audit("input_text", {"text_length": len(text)}, f"error: {e}")
        return msg