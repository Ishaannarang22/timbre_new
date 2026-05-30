"""
policy.py — the safety classification for every tool.

The owner decided (see docs/tooling/CONTRACT.md) that there are exactly two risk classes and
NO blocked class — everything is allowed, but anything risky is CONFIRM-gated server-side
(never trusting the LLM alone):

  * SAFE    — run immediately (get volume, list apps, screenshot, read clipboard, open URL...)
  * CONFIRM — stage the action, speak a read-back + needs_confirmation, run ONLY after the
              owner says yes (sends, Trash deletes, sleep/lock/restart/shutdown, Wi-Fi/BT
              toggles, quitting an app...).

`Risk` subclasses `str` so a spec's risk serializes/compares cleanly and reads naturally in
audit logs.
"""

from enum import Enum


class Risk(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
