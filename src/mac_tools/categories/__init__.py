"""
mac_tools.categories — importing this package imports every category module, which runs
their @tool decorators and registers their tools on the global REGISTRY.

Each module is imported in its OWN try/except so a category that another parallel agent
hasn't written yet (or that fails to import) doesn't break load_all() — we log it and keep
going. The expected module names are fixed by docs/tooling/CONTRACT.md (File layout).
"""

import importlib
import logging

_log = logging.getLogger("mac_tools.categories")

# The category modules named in the contract's file layout. Some are authored by other
# parallel agents and may not exist yet — that's fine, each import is guarded below.
_CATEGORY_MODULES = (
    "media",
    "system",
    "display",
    "apps",
    "windows",
    "files",
    "clipboard",
    "screen",
    "web",
    "notifications",
    "productivity",
    "messaging",
    "input_control",
    "network",
    "sysinfo",
    "power",
    "memory",
)

for _name in _CATEGORY_MODULES:
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as e:  # noqa: BLE001 — a missing/broken category must not break load_all
        _log.debug("categories: skipping %s (%s)", _name, e)
