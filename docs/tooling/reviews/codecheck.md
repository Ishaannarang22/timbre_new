# CODE-CHECK gate — `feat/mac-tools-factory-memory`

Scope: `src/mac_tools/**`, `src/agent_memory/**`, `src/twilio_bot.py`,
`scripts/grant_permissions.py`, `scripts/memory_cli.py`.

Method: byte-compile (`py_compile` / `compileall`), live import of every package
(`import mac_tools; mac_tools.load_all()`, `import agent_memory`, `import twilio_bot`),
AST-based static pass (no ruff/flake8/pyflakes in the venv), house-style audit vs
`src/mac_actions.py` + `docs/tooling/CONTRACT.md`, and a no-sound/no-network/no-daemon
functional smoke of dispatch/broker/validator/factory. Read-only — no code edits.

## Summary

- **Compile:** all 31 files byte-compile cleanly.
- **Imports:** `mac_tools` (93 enabled tools across 17 categories), `agent_memory`,
  and `twilio_bot` all import cleanly. No circular imports. No leftover references to the
  deleted `MAC_TOOLS` / `register_mac_tools` / `_handle_*` in `twilio_bot.py` (grep: none).
- **Contract conformance:** every category handler audits via `runner.audit`, returns a
  short string, never raises, has a docstring/comment, and sane `@tool` metadata. All 19
  `Risk.CONFIRM` tools have a `confirm_summary` (verified programmatically: none missing).
- **Counts: Errors 1 · Warnings 3 · Nits 5.**

---

## Errors

### E1 — `twilio_bot.py:926` — `CallRecorder()` called with non-existent `mode=` kwarg
```python
recorder = agent_memory.CallRecorder(call_sid, mode=mode, caller=caller)
```
`CallRecorder.__init__` (`src/agent_memory/recorder.py:116`) is
`def __init__(self, call_sid, direction="outbound", caller=""):` — there is **no `mode`
parameter**. This raises `TypeError: __init__() got an unexpected keyword argument 'mode'`.

Impact: it's inside the `if authorized:` branch of `/ws`, so **every authorized call**
(the 7 AM outbound call, and any allowlisted inbound caller) hits this line and the `/ws`
handler throws before the pipeline is built — the call drops. Unauthorized callers are
unaffected (no recorder). The bug only fails at runtime, so compile/import don't catch it;
the package's own docstrings (`recorder.py:107`, `agent_memory/__init__.py:14`) correctly
use `direction=mode`, confirming the intended call.

Fix: `agent_memory.CallRecorder(call_sid, direction=mode, caller=caller)`.

---

## Warnings

### W1 — `src/mac_tools/registry.py:22` — unused import `field`
```python
from dataclasses import dataclass, field
```
`field` is never used (`ToolSpec` uses plain defaults). Harmless but dead.
Fix: `from dataclasses import dataclass`.

### W2 — `src/mac_tools/categories/input_control.py:293-297` — `mouse_move` likely a silent no-op
```python
run_osa('tell application "System Events"',
        f"set mouse location to {{{cx}, {cy}}}", "end tell")
```
`System Events` exposes the cursor position as a **read-only** property in standard macOS;
`set mouse location to {...}` typically errors (no settable `mouse location`). Because the
handler wraps everything in `try/except subprocess.SubprocessError` and audits, it won't
raise — but the pointer won't move and the user is told "Moved the pointer to x, y." That's
a misleading success string for an action that didn't happen. (The companion `mouse_click`
uses `click at {x, y}`, which is the supported verb.) Not testable here (no sound/UI in this
gate), so flagged rather than asserted. Consider verifying live, or downgrading the success
copy / using a cliclick-style path if pointer-move is actually required.

### W3 — `src/mac_tools/categories/memory.py:78` — typed-`None` default mismatch
```python
def recall_memory(query: str = None) -> str:
```
Annotated `str` but defaulted to `None`. It's handled safely (`q = (query or "").strip()`),
so no runtime bug, but the annotation is inconsistent with the rest of the codebase, which
uses `str = ""` defaults (e.g. every other handler). Fix: `query: str = ""` (matches the
`media.py` / `web.py` convention), or annotate `str | None`.

---

## Nits

### N1 — `src/mac_tools/categories/files.py:328,383` — function-local imports
`import time as _time` (inside `get_info`) and `import shutil` (inside `move_file`) are
imported inside functions rather than at module top. It works and is even mildly defensive
(shutil only on the cross-volume fallback path), but it diverges from the module-top import
style every other category uses. Optional: hoist to the top of the file.

### N2 — `scripts/grant_permissions.py:7` (docstring) — stale single-file reference
The docstring says the agent "drives the Mac via AppleScript (`src/mac_actions.py`)". On
this branch the surface is the much larger `src/mac_tools/**` registry (mac_actions is now
only wrapped by `categories/media.py`). The probe logic itself is correct and unchanged;
only the explanatory prose is dated. Cosmetic.

### N3 — Two independent Wi-Fi-interface resolvers (duplicated logic)
`categories/network.py:_wifi_iface()` and `categories/sysinfo.py:_wifi_interface()` both
parse `networksetup -listallhardwareports` to find the Wi-Fi device, with slightly different
parsing (line-walk vs regex) and different caching (network.py caches, sysinfo.py doesn't).
Not wrong — but it's duplicated constant/logic that could live once in `runner.py`. Both
also hardcode the `en0` fallback independently. Low priority.

### N4 — `local_ip`/`get_local_ip` and `screen_info`/`get_display_info`/`wifi_network_name`
overlap across categories. `sysinfo.local_ip` vs `network.get_local_ip`; `screen.screen_info`
vs `display.get_display_info`; `sysinfo.wifi_network_name` vs `network.get_wifi_name`. These
are genuinely distinct tool names (no registry collision — last-wins only applies to identical
names), so it's not a bug, but the model is offered near-duplicate tools, which can dilute
tool selection. Consider consolidating or differentiating descriptions if selection accuracy
matters. Informational.

### N5 — `src/mac_tools/factory.py:51` writes `docs/tooling/glm_factory_prompt.md` on every
`build_glm_system_prompt()` call (including during the validator/factory smoke test in this
review). That's by design (the contract says the rendered prompt is mirrored to docs as the
"documented, current version"), but note that running the factory — even with a mock
`_completion` — mutates a tracked doc file as a side effect. Best-effort/OSError-swallowed, so
no failure risk; just be aware tests/regenerations touch that file.

---

## Verified-good (no action)

- **No bare `except:`** anywhere in scope — all broad catches are `except Exception`
  with an explanatory `# noqa: BLE001` or a narrow exception tuple, matching mac_actions.
- **No mutable default args** anywhere in scope.
- **No `print()` in library code** (`mac_tools` / `agent_memory`); the two scripts use
  `print()` legitimately for CLI output.
- **No `shell=True`** anywhere; all shelling goes through `runner.run_osa` / `run_shell`
  (list-arg, caller values as `on run argv` argv or stdin). Validator bans the spawning
  `subprocess.*` calls + `shell=True` + `eval/exec/compile/__import__` + permanent-delete.
- **Secrets carve-out** enforced in three independent places (validator deny-list,
  `files.read_text_file` deny-by-name, `recorder._scrub` / public `scrub`), all consistent
  with the CONTRACT deny patterns. `remember_this` refuses if scrubbing redacts anything.
- **CONFIRM gating** works: `dispatch` stages on the broker and returns
  `needs_confirmation=True` with a read-back; the deferred `_do` is exception-wrapped.
  Smoke-tested: `quit_app` stages, unknown tool default-denies, validator rejects a secret
  module and accepts a clean one, `create_tool(_completion=...)` builds a SAFE tool with no
  network call.
- **Deletion is Trash-only** (`files.move_to_trash` via Finder `delete`; `power.empty_trash`
  via Finder `empty trash`) — no `rm` / `os.remove` / `shutil.rmtree` anywhere.
- **`twilio_bot.py`** has no leftover `MAC_TOOLS` / `register_mac_tools` / `_handle_*`
  references; it uses `mac_tools.load_all()`, `REGISTRY`, `dispatch`, `ConfirmationBroker`,
  and `factory.create_tool` per the contract's integration handles.
