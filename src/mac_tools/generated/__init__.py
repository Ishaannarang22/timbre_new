"""
mac_tools.generated — home for factory-authored (Z.AI/GLM) tool modules.

The factory writes a new module here and hot-registers it mid-call. On (re)load this package
auto-discovers every .py file in this directory via pkgutil and imports it so its @tool
decorators run. Each import is guarded in its own try/except: a single malformed generated
module must never break load_all() or the rest of the generated tools.
"""

import importlib
import logging
import pkgutil

_log = logging.getLogger("mac_tools.generated")

for _info in pkgutil.iter_modules(__path__):
    # Skip private/dunder helpers; import every real generated module.
    if _info.name.startswith("_"):
        continue
    try:
        importlib.import_module(f"{__name__}.{_info.name}")
    except Exception as e:  # noqa: BLE001 — a bad generated module must not break load_all
        _log.warning("generated: skipping %s (%s)", _info.name, e)
