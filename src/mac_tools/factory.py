"""
factory.py — dynamic tool authoring with Z.AI / GLM-5.1, plus live hot-registration.

When the phone agent is asked for something with no matching tool, it calls the
`request_new_tool` tool with a plain-English description. THIS module turns that description
into a brand-new, audited, hot-registered Mac tool — mid-call, with no daemon restart.

Flow (see docs/tooling/CONTRACT.md "factory.py" section):
  1. build_glm_system_prompt(task): introspect the LIVE REGISTRY + a static installed-apps
     list + the runner API + the @tool contract + the secrets carve-out + a COMPLETE exemplar,
     and emit the exact required OUTPUT format. The full existing tool list is included so GLM
     never duplicates. Every render is ALSO written to docs/tooling/glm_factory_prompt.md (the
     documented, current version). Secrets/keys/PII are NEVER placed in the prompt.
  2. create_tool(description, ...): render the prompt → call Z.AI glm-5.1 (OpenAI-compatible,
     env ZAI_API_KEY/ZAI_BASE_URL/ZAI_MODEL, the already-installed `openai` package) → extract
     the fenced python module → run it through validator.validate_tool_code → if ok: write to
     generated/<name>.py, import it (so @tool registers on REGISTRY), and — if a live llm+context
     were handed in — HOT-ADD it per docs/tooling/hot_reload_findings.md. SAFE → enabled now;
     RISKY (validator flagged or gated_if_generated) → registered DISABLED pending owner approval.
  3. Audit the rendered prompt + GLM response + outcome to logs/actions.log (NEVER the key).

Robustness: create_tool NEVER raises. Any failure (no key, network error, bad code, validator
reject) returns {"ok": False, "message": "..."} so the voice pipeline keeps running.

KEY RESERVATION (owner): the GLM key must NOT be spent on testing. In tests, pass a mock
`_client` (or `_completion`) that returns a canned module — the real Z.AI completions endpoint
is NEVER called from tests. The /models endpoint was already verified by a prior agent.
"""

import os
import re
import time
from pathlib import Path

from .policy import Risk
from .registry import REGISTRY
from .runner import audit

# --------------------------------------------------------------------------------------------
# ABUSE CAP (P1-1). The factory writes NEW code that auto-loads on the next restart and spends
# the ZAI key. A looping LLM (or a spoofed caller) could burn the key and fill the disk. We cap
# BOTH the rate (sliding window) and the absolute number of generated modules on disk. When
# either cap trips, create_tool returns a friendly refusal WITHOUT calling GLM.
# --------------------------------------------------------------------------------------------
_RATE_MAX = 5                 # max successful GLM-backed creations ...
_RATE_WINDOW_SECONDS = 600.0  # ... per this sliding window (10 min)
_MAX_GENERATED_FILES = 30     # hard ceiling on .py files in generated/

# Module-level sliding window of recent creation timestamps (monotonic seconds). Single daemon,
# single-threaded factory calls (each create_tool is awaited via to_thread one at a time), so a
# plain list is sufficient — no lock needed for correctness here.
_RECENT_CREATIONS: list[float] = []

_LIMIT_MESSAGE = "I've hit my limit on building new tools for now."


def _generated_file_count() -> int:
    """Count real generated .py modules on disk (excludes __init__ and dunder helpers)."""
    try:
        return sum(
            1 for p in _GENERATED_DIR.glob("*.py")
            if not p.name.startswith("_")
        )
    except OSError:
        return 0


def _abuse_cap_tripped() -> str | None:
    """Return a reason string if a cap is tripped (so we must refuse WITHOUT calling GLM), else
    None. Prunes the sliding window in place."""
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    # Prune expired timestamps.
    _RECENT_CREATIONS[:] = [t for t in _RECENT_CREATIONS if t >= cutoff]
    if len(_RECENT_CREATIONS) >= _RATE_MAX:
        return f"rate limit: {_RATE_MAX} creations / {int(_RATE_WINDOW_SECONDS)}s"
    if _generated_file_count() >= _MAX_GENERATED_FILES:
        return f"file cap: {_MAX_GENERATED_FILES} generated modules on disk"
    return None


def _record_creation() -> None:
    """Stamp a successful creation onto the sliding window."""
    _RECENT_CREATIONS.append(time.monotonic())

# pipecat schema types for the hot-add path. Imported lazily-safely: if pipecat isn't present
# (pure-unit context), hot-add is simply skipped — create_tool still writes+imports the tool.
try:
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    _HAVE_PIPECAT = True
except Exception:  # noqa: BLE001
    FunctionSchema = None  # type: ignore
    ToolsSchema = None  # type: ignore
    _HAVE_PIPECAT = False

# Where generated modules are written and where the documented prompt is mirrored.
_GENERATED_DIR = Path(__file__).resolve().parent / "generated"
_PROMPT_DOC = Path(__file__).resolve().parents[2] / "docs" / "tooling" / "glm_factory_prompt.md"

# Static installed-apps list for the prompt (so GLM targets apps that actually exist). This is
# a curated, non-sensitive list — NO paths, NO user data. Update as the machine changes.
_INSTALLED_APPS = [
    "Safari", "Google Chrome", "Brave Browser", "Firefox",
    "Notes", "Notion", "Reminders", "Calendar", "Mail",
    "Messages", "Spotify", "Music", "Photos", "Preview",
    "Finder", "Terminal", "iTerm", "Visual Studio Code", "Cursor",
    "Sublime Text", "System Settings", "Activity Monitor", "Calculator",
    "Pages", "Numbers", "Keynote", "Maps", "Weather", "Zoom",
]


# --------------------------------------------------------------------------------------------
# The EXEMPLAR module GLM copies the shape from. Kept verbatim in the prompt so the model has a
# correct, complete, injection-safe template — argv-only caller values, clamp/validate, audit,
# never raise, SHORT spoken string, correct Risk + confirm_summary for a CONFIRM tool.
# --------------------------------------------------------------------------------------------
_EXEMPLAR = '''\
"""generated/brightness.py — set the Mac display brightness. category="display"."""

from mac_tools.policy import Risk
from mac_tools.registry import tool
from mac_tools.runner import audit, clamp, run_osa


@tool(
    name="set_brightness",
    description="Set this Mac's display brightness to an absolute level from 0 to 100.",
    properties={"level": {"type": "integer", "description": "Target brightness, 0-100."}},
    required=["level"],
    risk=Risk.SAFE,
    category="display",
)
def set_brightness(level: int = 0) -> str:
    """Set brightness. Clamp the int we control and inline it (safe); never raise."""
    pct = clamp(level, 0, 100)            # validate/clamp the caller value
    frac = pct / 100.0
    try:
        # `frac` is a number WE computed (not raw caller text), so inlining is safe here.
        # Any DYNAMIC caller STRING must instead go via args=[...] + `on run argv`.
        run_osa(
            "tell application \\"System Events\\"",
            f"set brightness of every desktop to {frac}",
            "end tell",
        )
        msg = f"Set brightness to {pct} percent."
        audit("set_brightness", {"level": pct}, msg)
        return msg
    except Exception as e:            # run_osa raises on failure; catch broadly and never raise
        msg = "Sorry, I couldn't change the brightness."
        audit("set_brightness", {"level": pct}, f"error: {e}")
        return msg
'''


def _format_existing_tools() -> str:
    """One line per ENABLED tool: `name · category · description`. Built from the LIVE
    REGISTRY so GLM always sees the current surface and never duplicates a tool."""
    lines = []
    for s in sorted(REGISTRY.specs(enabled_only=True), key=lambda x: (x.category, x.name)):
        desc = " ".join(str(s.description).split())  # collapse whitespace for a tidy line
        if len(desc) > 160:
            desc = desc[:157] + "..."
        lines.append(f"- {s.name} · {s.category} · {desc}")
    return "\n".join(lines) if lines else "- (none registered yet)"


def build_glm_system_prompt(task: str) -> str:
    """Render the LIVE system prompt for GLM-5.1 and ALSO write it to
    docs/tooling/glm_factory_prompt.md (the documented, current version).

    Introspects the live REGISTRY each call. NEVER includes secrets/keys/PII.
    `task` is the owner's plain-English description of the tool they want."""
    task_clean = " ".join(str(task or "").split())

    prompt = f"""You are a senior macOS automation engineer authoring ONE new tool for a \
voice-controlled Mac agent. The agent runs a Pipecat + Nemotron phone pipeline; tools are \
small audited Python functions the agent can call mid-call. Your output is a SINGLE \
self-contained Python module that will be dropped into `src/mac_tools/generated/` and \
imported live (its @tool decorator registers it on the running agent).

# The task
Author a tool that does this:
  "{task_clean}"

# The runner API — the ONLY way to touch the system (never shell out yourself)
Import from `mac_tools.runner`:
  - run_osa(*lines, args=None, timeout=5.0) -> str
      Runs osascript with each statement as its own -e arg. ANY value that comes from the \
caller/LLM MUST be passed via `args=[...]` and read inside AppleScript with `on run argv` \
(e.g. `item 1 of argv`). NEVER string-interpolate caller text into a script line — that is an \
injection. Only values YOU fully control (clamped ints, fixed enum strings) may be inlined.
  - run_shell(argv: list[str], timeout=10.0, input_text=None) -> str
      List-arg subprocess. NEVER shell=True. argv[0] is an absolute path or a bare binary \
name resolved on PATH. No shell, so no glob/quote/injection surface.
  - audit(action: str, args, result: str) -> None   # log every action
  - clamp(n, lo=0, hi=100) -> int                    # bound/validate an int
  - app_is_running(name) -> bool                      # read-only; never launches an app
  - frontmost_app() -> str | None
Both run_osa and run_shell RAISE on failure — you MUST wrap every call in \
`try/except Exception as e:` (catch `Exception`, NOT a subprocess-specific type — you CANNOT \
import subprocess) and return a friendly spoken string. Handlers must NEVER raise.

# The @tool / Risk contract
Import `from mac_tools.registry import tool` and `from mac_tools.policy import Risk`.
Decorate exactly ONE sync function with @tool(...):
  @tool(name=..., description=..., properties={{...}}, required=[...], risk=Risk.SAFE|Risk.CONFIRM, \
category="...", confirm_summary=<optional callable>)
  - name: snake_case, unique (do NOT reuse an existing tool name below).
  - description: clear, spoken-facing — the agent reads it to decide when to call the tool.
  - properties: {{arg: {{"type": "integer|string|boolean", "description": "...", "enum": [...]?}}}}.
  - required: list of required arg names.
  - The function is SYNC, takes the args as keyword args with sensible defaults, returns a \
SHORT spoken-friendly string (the agent speaks it). Clamp/validate every input; default-deny \
on bad input with a friendly string. audit() the action.
  - risk: pick SAFE for read/observe/non-destructive recoverable actions (get state, list, \
open URL, create a note, screenshot). Pick Risk.CONFIRM for anything that SENDS \
(message/email/post), DELETES (Trash only — NEVER permanent delete), is DISRUPTIVE \
(sleep/lock/logout/restart/shutdown/quit an app), or TOGGLES the network (Wi-Fi/Bluetooth). \
For a CONFIRM tool, also pass confirm_summary=lambda **args: "...?" — a one-line spoken \
read-back the agent says before doing it.

# HARD safety carve-out — SECRETS (non-negotiable)
The tool MUST NOT read or exfiltrate ANY secret: no Keychain, no \
`security find-generic-password`, no SSH/GPG private keys, no .env/dotfiles with credentials, \
no saved/browser passwords, no API tokens. Do not even reference these. A tool that touches \
secrets will be REJECTED outright.

# Other hard rules — your code is statically validated by a STRUCTURAL ALLOWLIST and REJECTED \
if it breaks ANY of these (no exceptions):
  - IMPORTS — you may import ONLY from this exact allowlist, nothing else:
      from mac_tools.runner import run_osa, run_shell, audit, clamp, app_is_running, frontmost_app
      from mac_tools.registry import tool
      from mac_tools.policy import Risk
      stdlib (pure-data only): re, json, math, time, datetime, urllib.parse, textwrap, string, html
    You may NOT import os, subprocess, io, pathlib, sys, urllib.request, urllib.error, socket, \
http, requests, importlib, ctypes, pickle, codecs, builtins, OR any other module. There is NO \
`import subprocess` — touch the system ONLY through run_osa / run_shell.
  - NO top-level code beyond the docstring, the allowlisted imports, your @tool function \
definition(s), and simple CONSTANT assignments. NO module-level calls, loops, ifs, with/try \
blocks, or any statement that runs at import time — the module is imported live, so its top \
level must do NOTHING.
  - NO eval / exec / compile / __import__ / getattr / setattr / vars / globals / locals / \
open / input / breakpoint, NO `__builtins__`, NO dunder-attribute access (e.g. `.__globals__`, \
`.__class__`, `.__subclasses__`), NO subprocess shell=True. Use `open` NEVER — read files only \
via an existing file tool, never directly.
  - NO permanent delete (Trash only, and that's a CONFIRM tool). NO requests/socket/network libs.
  - Error handling: catch `Exception` (NOT subprocess.SubprocessError — subprocess is not \
importable). Handlers must NEVER raise.
  - All shelling out goes through run_osa / run_shell. Caller strings reach AppleScript ONLY \
as argv.

# Installed apps (target apps that exist; don't invent app names)
{", ".join(_INSTALLED_APPS)}

# Existing tools — DO NOT duplicate any of these (name · category · description)
{_format_existing_tools()}

# EXEMPLAR — copy this shape exactly (a complete, correct, injection-safe module)
```python
{_EXEMPLAR}```

# OUTPUT FORMAT (strict)
Reply with EXACTLY ONE fenced Python code block and nothing else — no prose before or after:
```python
# ...your complete module here...
```
The module must be self-contained, import only from the allowlist above, define exactly ONE \
@tool function, follow the runner/injection/never-raise rules, and pick the correct Risk.
"""

    # Mirror the rendered prompt to the documented, current version. Best-effort: a docs write
    # failure must never break tool creation.
    try:
        _PROMPT_DOC.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# GLM Factory System Prompt (LIVE — auto-generated)\n\n"
            "_This file is rewritten by `factory.build_glm_system_prompt()` on every render._\n"
            "_It is the documented, current version of the system prompt sent to Z.AI/GLM-5.1._\n"
            "_It NEVER contains secrets, keys, or PII._\n\n"
            f"Most recent task: `{task_clean}`\n\n"
            "---\n\n"
            "````text\n"
        )
        _PROMPT_DOC.write_text(header + prompt + "\n````\n")
    except OSError:
        pass

    return prompt


# --------------------------------------------------------------------------------------------
# Code extraction from a GLM reply.
# --------------------------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_python(text: str) -> str | None:
    """Pull the python module out of a GLM reply. Prefers a fenced ```python block; falls back
    to the whole reply if it already looks like a module (has an @tool/import). Returns None if
    nothing module-like is found."""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    if m:
        code = m.group(1).strip()
        if code:
            return code
    # Fallback: maybe the model returned bare code without a fence.
    stripped = text.strip()
    if "@tool" in stripped or "def " in stripped:
        return stripped
    return None


def _module_name_for(tool_name: str) -> str:
    """A safe module filename stem for generated/<stem>.py. Snake-cases and strips anything
    non-identifier so we never write a weird path."""
    stem = re.sub(r"[^0-9a-zA-Z_]", "_", str(tool_name)).strip("_").lower()
    if not stem or stem[0].isdigit():
        stem = "gen_" + stem
    return stem


def _call_glm(system_prompt: str, description: str, _client=None, _completion=None) -> str:
    """Call Z.AI glm-5.1 (OpenAI-compatible) and return the assistant text.

    TEST HOOKS (to RESERVE the real key — owner requirement):
      * _completion: a string -> returned directly, NO API call. (simplest mock)
      * _client    : an object with .chat.completions.create(...) -> used instead of building a
                     real OpenAI client. Lets tests inject a fake that returns a canned module.
    With neither hook, builds a real OpenAI client from env and calls the live endpoint — this
    path is NEVER exercised by tests.
    """
    if _completion is not None:
        return str(_completion)

    model = os.environ.get("ZAI_MODEL", "glm-5.1")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Build the tool: {description}"},
    ]

    client = _client
    if client is None:
        # Real client — only reached in genuine live tool-creation, never in tests.
        from openai import OpenAI

        base_url = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4/")
        api_key = os.environ.get("ZAI_API_KEY")
        if not api_key:
            raise RuntimeError("ZAI_API_KEY is not set")
        # Bounded timeout + single retry so a wedged Z.AI connection can't block the worker
        # thread (and its thread-pool slot) forever — same guard twilio_bot uses for NVIDIA.
        client = OpenAI(api_key=api_key, base_url=base_url).with_options(
            timeout=45.0, max_retries=1
        )

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def _make_adapter(tool_name: str, broker):
    """Build the async pipecat adapter for a generated tool, matching twilio_bot.py's pattern:
    dispatch in a worker thread (so blocking osascript never stalls the event loop), then
    result_callback. `broker` is the per-call ConfirmationBroker."""
    import asyncio

    from .registry import dispatch

    async def _adapter(params) -> None:
        try:
            result = await asyncio.to_thread(
                dispatch, tool_name, getattr(params, "arguments", {}) or {}, broker
            )
        except Exception:  # noqa: BLE001 — never raise into the pipeline
            result = {"result": "Sorry, that didn't work."}
        await params.result_callback(result)

    return _adapter


def _hot_add(spec, llm, context, broker) -> bool:
    """Append the new tool's FunctionSchema to the live context, set_tools(), and
    register the handler — per docs/tooling/hot_reload_findings.md. Returns True on success.

    Only called for ENABLED (SAFE) generated tools with a live llm+context. RISKY/disabled
    tools are NOT advertised to the live model (they await owner approval)."""
    if not (_HAVE_PIPECAT and llm is not None and context is not None):
        return False
    try:
        schema = FunctionSchema(
            name=spec.name,
            description=spec.description,
            properties=spec.properties,
            required=spec.required,
        )
        # Read the live ToolsSchema; append idempotently; reinstall via the public setter so
        # normalize/validate run (empty->non-empty transition handled).
        current = getattr(context, "tools", None)

        def _is_given(v):
            # NOT_GIVEN is a sentinel; treat None / falsy-sentinel as "no tools yet".
            return v is not None and hasattr(v, "standard_tools")

        existing = list(current.standard_tools) if _is_given(current) else []
        custom = getattr(current, "custom_tools", None) if _is_given(current) else None
        if not any(getattr(t, "name", None) == spec.name for t in existing):
            existing.append(schema)
        if custom is not None:
            context.set_tools(ToolsSchema(standard_tools=existing, custom_tools=custom))
        else:
            context.set_tools(ToolsSchema(standard_tools=existing))

        llm.register_function(spec.name, _make_adapter(spec.name, broker))
        return True
    except Exception as e:  # noqa: BLE001 — hot-add failure must not raise; tool is still registered
        audit("factory.hot_add", {"name": spec.name}, f"error: {e}")
        return False


def create_tool(
    description: str,
    *,
    llm=None,
    context=None,
    call_id=None,
    broker=None,
    _client=None,
    _completion=None,
) -> dict:
    """Author, validate, register, and (if live) hot-add a new Mac tool. NEVER raises.

    Args:
      description : plain-English description of the desired tool (from request_new_tool).
      llm, context: the LIVE pipecat OpenAILLMService + LLMContext for the current call. If both
                    are given AND the tool is SAFE, it is hot-added so the SAME call can use it.
      broker      : the per-call ConfirmationBroker (needed so a CONFIRM/generated tool's adapter
                    can stage; defaults to a throwaway broker if not supplied).
      call_id     : optional id for audit correlation.
      _client/_completion: TEST HOOKS that avoid the real Z.AI endpoint (reserve the key).

    Returns: {ok: bool, tool_name: str|None, risk: "safe"|"confirm"|None, message: str}.
    """
    try:
        # 0) ABUSE CAP — refuse (WITHOUT calling GLM) if the rate window is saturated or the
        #    generated/ directory is at its file ceiling. Cheap DoS / key-burn / disk-fill guard.
        cap = _abuse_cap_tripped()
        if cap:
            audit("factory.create_tool", {"desc": description, "call_id": call_id},
                  f"refused (abuse cap): {cap}")
            return {"ok": False, "tool_name": None, "risk": None, "message": _LIMIT_MESSAGE}

        # 1) Render the live prompt (also writes docs/tooling/glm_factory_prompt.md).
        system_prompt = build_glm_system_prompt(description)

        # 2) Call GLM (or the test hook). NEVER hits the real endpoint when a hook is given.
        try:
            reply = _call_glm(system_prompt, description, _client=_client, _completion=_completion)
        except Exception as e:  # noqa: BLE001
            audit("factory.create_tool", {"desc": description, "call_id": call_id},
                  f"glm error: {e}")
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "I couldn't reach the tool builder just now."}

        # Audit the prompt + response (NEVER the key). Truncate to keep the log readable.
        audit("factory.glm",
              {"desc": description, "call_id": call_id, "prompt_chars": len(system_prompt)},
              (reply or "")[:2000])

        # 3) Extract the python module.
        code = _extract_python(reply)
        if not code:
            audit("factory.create_tool", {"desc": description}, "no code in reply")
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "The tool builder didn't return any code I could use."}

        # 4) Validate (AST + deny patterns). Reject is final.
        from .validator import validate_tool_code

        ok, reason, meta = validate_tool_code(code)
        if not ok:
            audit("factory.validate", {"desc": description}, f"REJECT: {reason}")
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": f"I couldn't safely build that. {reason}"}

        tool_names = meta.get("tool_names") or []
        tool_name = tool_names[0] if tool_names else None
        if not tool_name:
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "I couldn't safely build that. No tool was defined."}

        risky = bool(meta.get("risky"))

        # 5) Validation passed (step 4, BEFORE any write/import). Now write to
        #    generated/<stem>.py and import it so its @tool runs (registers on REGISTRY). If the
        #    import itself raises, delete the file we just wrote so a broken module doesn't
        #    linger and auto-load on the next restart.
        stem = _module_name_for(tool_name)
        target = _GENERATED_DIR / f"{stem}.py"
        try:
            _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
            target.write_text(code)
        except OSError as e:
            audit("factory.write", {"name": tool_name}, f"error: {e}")
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "I built the tool but couldn't save it."}

        try:
            import importlib

            mod_path = f"mac_tools.generated.{stem}"
            if mod_path in __import__("sys").modules:
                importlib.reload(__import__("sys").modules[mod_path])
            else:
                importlib.import_module(mod_path)
        except Exception as e:  # noqa: BLE001
            audit("factory.import", {"name": tool_name}, f"error: {e}")
            # Roll back the write — a module that fails to import must not auto-load later.
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "I built the tool but it failed to load."}

        # Record this successful creation against the sliding-window rate cap.
        _record_creation()

        spec = REGISTRY.get(tool_name)
        if spec is None:
            audit("factory.register", {"name": tool_name}, "spec not found after import")
            return {"ok": False, "tool_name": None, "risk": None,
                    "message": "I built the tool but it didn't register properly."}

        # Mark provenance. Disable if risky/gated (awaits owner approval — NOT advertised live).
        spec.generated = True
        gate = risky or spec.risk == Risk.CONFIRM or spec.gated_if_generated
        spec.gated_if_generated = spec.gated_if_generated or risky
        if gate:
            spec.enabled = False
            risk_val = (spec.risk.value if isinstance(spec.risk, Risk) else "confirm")
            audit("factory.create_tool", {"name": tool_name},
                  f"registered DISABLED (gated): {reason}")
            return {"ok": True, "tool_name": tool_name, "risk": risk_val,
                    "message": (f"I built a tool called {tool_name}, but it does something "
                                "risky, so it's waiting for your approval before I can use it.")}

        # SAFE → enabled now; hot-add to the live call if we have a live llm+context.
        spec.enabled = True
        the_broker = broker
        if the_broker is None:
            from .confirm import ConfirmationBroker

            the_broker = ConfirmationBroker()
        hot = _hot_add(spec, llm, context, the_broker)
        audit("factory.create_tool", {"name": tool_name},
              f"registered ENABLED; hot_add={hot}")
        return {"ok": True, "tool_name": tool_name, "risk": "safe",
                "message": (f"Done — I built a new tool called {tool_name} and it's ready to "
                            "use now.")}

    except Exception as e:  # noqa: BLE001 — absolute backstop: create_tool NEVER raises.
        audit("factory.create_tool", {"desc": description}, f"unexpected error: {e}")
        return {"ok": False, "tool_name": None, "risk": None,
                "message": "Something went wrong building that tool."}
