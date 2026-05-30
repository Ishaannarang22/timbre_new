"""
registry.py — the tool registry, the @tool decorator, and dispatch().

This is the core the whole system hangs off of:

  * `ToolSpec` describes one tool: its name, spoken-facing description, JSON-schema-ish
    `properties`, `required` args, the sync `handler(**args) -> str`, its Risk, and (for
    CONFIRM tools) a `confirm_summary(**args) -> str` that builds the spoken read-back.
  * `@tool(...)` wraps a plain sync `fn(**args) -> str` into a ToolSpec and registers it on
    the module-level `REGISTRY`, so importing a category module is all it takes to register
    its tools (that's what load_all() relies on).
  * `ToolRegistry` stores specs by name (last-wins on collision — needed for hot-reload and
    for the factory re-registering a tool) and can emit pipecat FunctionSchema / ToolsSchema
    for the enabled tools so twilio_bot.py can offer them to the LLM.
  * `dispatch(name, arguments, broker)` is the ONE entry the Twilio adapters call. It keeps
    only known args, then either runs a SAFE tool now or stages a CONFIRM tool on the broker.
    It NEVER raises — every failure becomes a friendly spoken string.

FunctionSchema / ToolsSchema come from the same pipecat 1.2.1 paths twilio_bot.py uses.
"""

import re
from dataclasses import dataclass
from typing import Callable

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from .policy import Risk
from .runner import audit


@dataclass
class ToolSpec:
    name: str
    description: str
    properties: dict  # {arg: {"type": "...", "description": "...", "enum": [...]?}}
    required: list[str]
    handler: Callable[..., str]  # SYNC; called handler(**args); returns SHORT spoken string
    risk: Risk = Risk.SAFE
    category: str = "misc"
    # CONFIRM tools build their spoken read-back from the (filtered) args:
    confirm_summary: Callable[..., str] | None = None
    gated_if_generated: bool = False
    generated: bool = False
    enabled: bool = True


class ToolRegistry:
    """Name-keyed store of ToolSpecs. Not thread-safe by design — registration happens at
    import time (load_all) and during single-threaded hot-add on the event loop's thread."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register (or replace) a spec by name. Last-wins on collision so the factory can
        re-author/hot-replace a tool without a restart."""
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def specs(self, enabled_only: bool = True) -> list[ToolSpec]:
        """All registered specs. By default only enabled ones (disabled = a generated tool
        the validator gated pending owner approval)."""
        out = list(self._specs.values())
        if enabled_only:
            out = [s for s in out if s.enabled]
        return out

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def function_schemas(self) -> list[FunctionSchema]:
        """A pipecat FunctionSchema for each ENABLED tool — exactly what the LLM is offered."""
        return [
            FunctionSchema(
                name=s.name,
                description=s.description,
                properties=s.properties,
                required=s.required,
            )
            for s in self.specs(enabled_only=True)
        ]

    def tools_schema(self) -> ToolsSchema:
        """The single ToolsSchema (standard_tools=[...]) passed to the LLM context."""
        return ToolsSchema(standard_tools=self.function_schemas())


# The one process-wide registry every category module and the factory write into.
REGISTRY = ToolRegistry()


def tool(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    *,
    risk: Risk = Risk.SAFE,
    category: str = "misc",
    confirm_summary: Callable[..., str] | None = None,
    gated_if_generated: bool = False,
):
    """Decorator: wrap a sync `fn(**args) -> str` into a ToolSpec and REGISTRY.register() it.

    Usage (in a category module):

        @tool("get_volume", "Report this Mac's output volume.", risk=Risk.SAFE,
              category="media")
        def get_volume() -> str:
            ...

    Returns the original function unchanged so the module can still call it directly."""

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        spec = ToolSpec(
            name=name,
            description=description,
            properties=properties or {},
            required=required or [],
            handler=fn,
            risk=risk,
            category=category,
            confirm_summary=confirm_summary,
            gated_if_generated=gated_if_generated,
        )
        REGISTRY.register(spec)
        return fn

    return decorator


def _filter_args(spec: ToolSpec, arguments: dict) -> dict:
    """Keep only the args named in spec.properties — the LLM occasionally invents extra keys,
    and a handler called handler(**args) would TypeError on an unexpected kwarg. Default-deny:
    unknown keys are silently dropped."""
    if not isinstance(arguments, dict):
        return {}
    allowed = set(spec.properties.keys())
    return {k: v for k, v in arguments.items() if k in allowed}


_TRUTHY = {"true", "1", "yes", "on", "y", "t"}
_FALSY = {"false", "0", "no", "off", "n", "f", "none", "null", ""}


def _coerce_value(value, jtype):
    """Best-effort coerce one LLM-supplied value to its declared JSON-schema type. The model
    usually sends the right shape, but sometimes a STRING where a boolean/integer is wanted —
    most dangerously the string 'false' (Python's bool('false') is True), which would INVERT a
    toggle like set_muted/set_dark_mode/set_wifi_power. On anything unparseable, return the
    value unchanged (the handler still clamps/validates)."""
    try:
        if jtype == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            if isinstance(value, str):
                s = value.strip().lower()
                if s in _TRUTHY:
                    return True
                if s in _FALSY:
                    return False
                return bool(s)
            return bool(value)
        if jtype in ("integer", "number"):
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, (int, float)):
                return int(value) if jtype == "integer" else value
            if isinstance(value, str):
                m = re.search(r"-?\d+(?:\.\d+)?", value)
                if m:
                    num = float(m.group())
                    return int(num) if jtype == "integer" else num
        return value
    except Exception:  # noqa: BLE001 — coercion is best-effort; fall back to the raw value
        return value


def _coerce_args(spec: ToolSpec, args: dict) -> dict:
    """Coerce each filtered arg to the type declared in spec.properties (best-effort)."""
    out = {}
    for k, v in args.items():
        jtype = (spec.properties.get(k) or {}).get("type")
        out[k] = _coerce_value(v, jtype)
    return out


def dispatch(name: str, arguments: dict, broker) -> dict:
    """The single entry every Twilio tool-adapter calls. Never raises.

    Behaviour:
      * Unknown OR disabled tool -> {"result": "I don't have that tool yet."}
      * SAFE    -> run handler now -> {"result": <str>}
      * CONFIRM -> stage on broker (read-back + the deferred action), return
                   {"result": "<readback> Want me to go ahead?", "needs_confirmation": True}

    Only args named in the spec's properties are passed through to the handler."""
    spec = REGISTRY.get(name)
    if spec is None or not spec.enabled:
        msg = "I don't have that tool yet."
        audit("dispatch", {"name": name}, msg)
        return {"result": msg}

    args = _coerce_args(spec, _filter_args(spec, arguments))

    # Required-arg guard: if the LLM omitted a required arg (or sent it empty/None), say so
    # plainly. Without this the handler(**args) would TypeError before its own friendly
    # branch, and a CONFIRM tool would stage a nonsensical read-back ("Quit ?").
    missing = [r for r in spec.required if args.get(r) in (None, "")]
    if missing:
        msg = f"I need the {missing[0].replace('_', ' ')} to do that."
        audit("dispatch", {"name": name, "missing": missing}, msg)
        return {"result": msg}

    if spec.risk == Risk.CONFIRM:
        # Build the spoken read-back. If no confirm_summary was provided, fall back to the
        # tool's description so we still say something sensible.
        try:
            if spec.confirm_summary is not None:
                summary = spec.confirm_summary(**args)
            else:
                summary = spec.description
        except Exception:
            summary = spec.description

        # The deferred action: run the handler when the owner confirms. Wrapped so a handler
        # that (against convention) raises still yields a friendly spoken string.
        def _do(_spec=spec, _args=args) -> str:
            try:
                return _spec.handler(**_args)
            except Exception:
                return "Sorry, that didn't work."

        broker.stage(summary, _do)
        readback = f"{summary} Want me to go ahead?"
        audit("dispatch", {"name": name, "args": args}, f"staged: {summary}")
        return {"result": readback, "needs_confirmation": True}

    # SAFE: run immediately. Handlers are supposed to catch their own errors, but we wrap as
    # defense in depth so dispatch can NEVER raise into the pipeline.
    try:
        result = spec.handler(**args)
    except Exception:
        result = "Sorry, that didn't work."
    audit("dispatch", {"name": name, "args": args}, result)
    return {"result": result}
