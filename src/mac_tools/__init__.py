"""
mac_tools — voice-controlled Mac tool system (core framework).

Public surface (what twilio_bot.py and the factory import):
    REGISTRY            — the process-wide ToolRegistry
    tool                — decorator that registers a sync handler as a ToolSpec
    Risk                — SAFE / CONFIRM classification
    dispatch            — the single entry the Twilio adapters call
    ConfirmationBroker  — per-call pending-action store
    load_all()          — import every category + generated module so all @tool
                          decorators run and REGISTRY is populated

Importing this package does NOT register any tools by itself — call load_all() once at
startup (it's idempotent). That keeps the core importable in isolation (e.g. for tests)
without dragging in every category's side effects.
"""

import importlib
import logging

from .confirm import ConfirmationBroker
from .policy import Risk
from .registry import REGISTRY, ToolRegistry, ToolSpec, dispatch, tool

__all__ = [
    "REGISTRY",
    "ToolRegistry",
    "ToolSpec",
    "tool",
    "Risk",
    "dispatch",
    "ConfirmationBroker",
    "load_all",
]

_log = logging.getLogger("mac_tools")

# Guard so load_all() is idempotent: importing the category/generated packages re-runs their
# @tool decorators (register() is last-wins, so it's harmless), but we still short-circuit so
# repeated calls are cheap and quiet.
_loaded = False


def load_all() -> None:
    """Import the category and generated sub-packages so every @tool decorator runs and
    REGISTRY is populated.

    Robust by design: a missing or broken category/generated package must NOT take down the
    daemon (this runs while a phone call is live). Each import is wrapped — any failure is
    logged and skipped so the rest of the tools still load. Idempotent: safe to call repeatedly.
    """
    global _loaded
    if _loaded:
        return

    # Order: categories first (the hand-written tools), then generated (factory output).
    for pkg in ("mac_tools.categories", "mac_tools.generated"):
        try:
            mod = importlib.import_module(pkg)
            # If the sub-package was imported earlier in this interpreter, importlib returns
            # the cached module without re-running its body (and thus without re-importing its
            # children). Reload so a freshly-written category/generated module gets picked up.
            importlib.reload(mod)
        except Exception as e:  # noqa: BLE001 — never let a bad module break startup
            _log.warning("mac_tools.load_all: failed to load %s: %s", pkg, e)
            continue

    _loaded = True
