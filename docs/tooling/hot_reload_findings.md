# Hot-adding a function-calling tool to a LIVE Pipecat session (no restart)

Research target: Pipecat **1.2.1** at
`/Users/node3/projects/voice_fun/.venv/lib/python3.12/site-packages/pipecat`.
Our bot (`src/twilio_bot.py`) uses the **universal** `LLMContext`
(`pipecat.processors.aggregators.llm_context.LLMContext`), `OpenAILLMService`,
and `LLMContextAggregatorPair`.

**Bottom line:** Yes — a tool can be hot-added mid-call with **no pipeline /
daemon restart**. Tools are read **fresh from the live `LLMContext` on every
completion** (never snapshotted at construction), and handlers are looked up
**fresh from a live dict on every dispatch**. The minimal hot-add is two steps:
(1) mutate the live context's `ToolsSchema.standard_tools` and call
`context.set_tools(...)`, and (2) `llm.register_function(name, handler)`.

---

## 1. How `OpenAILLMService` obtains `tools` per completion — fresh, never cached

The service does **not** cache or snapshot tools at construction. On every
completion it reads them just-in-time from the `LLMContext` it was handed in the
`LLMContextFrame`:

- `BaseOpenAILLMService.process_frame` receives an `LLMContextFrame` and calls
  `self._process_context(frame.context)`.
  `services/openai/base_llm.py:553-557`
- `_process_context` calls `self.get_chat_completions(context)`.
  `services/openai/base_llm.py:404,416`
- `get_chat_completions` builds the request from the context **at call time**:
  `params_from_context = adapter.get_llm_invocation_params(context, ...)`.
  `services/openai/base_llm.py:300-306`
- The adapter reads `context.tools` (the live property) right then and converts
  it to the OpenAI wire format:
  `tools = self.from_standard_tools(context.tools)`
  `adapters/services/open_ai_adapter.py:150-152`, conversion in
  `to_provider_tools_format` `adapters/services/open_ai_adapter.py:156-180`.
- `context.tools` is a plain live property returning `self._tools`.
  `processors/aggregators/llm_context.py:354-361`

There is **no instance field** on the LLM service holding tools; nothing in
`__init__` copies tools. Therefore whatever `ToolsSchema` the context holds at
the moment a completion runs is what gets sent. Mutating the context between
completions is picked up on the next run with zero extra wiring.

**Granularity:** tools are resolved **per-run (per completion)**, off the **live
context object**. The aggregator always wraps that same live object — every
`LLMContextFrame` is `LLMContextFrame(context=self._context)`
(`processors/aggregators/llm_response_universal.py:433-439, 441-448`) — so a
mutation to the context is visible to all subsequent completions in the session.

## 2. Structure of `LLMContext.tools` and the runtime setter

- `LLMContext.__init__(..., tools: ToolsSchema | NotGiven = NOT_GIVEN)` stores
  `self._tools = LLMContext._normalize_and_validate_tools(tools)`.
  `processors/aggregators/llm_context.py:101-116`
- `tools` must be a **`ToolsSchema`** (or `NOT_GIVEN`); anything else raises
  `TypeError`. An empty `ToolsSchema` normalizes back to `NOT_GIVEN`.
  `processors/aggregators/llm_context.py:459-475`
- `ToolsSchema` holds `_standard_tools: list[FunctionSchema]` (exposed via the
  read-only `standard_tools` property) plus optional `_custom_tools`.
  `adapters/schemas/tools_schema.py:41-85`
  - `standard_tools` returns the **actual underlying list** (not a copy), so you
    can `.append(...)` to it in place. `adapters/schemas/tools_schema.py:69-76`
  - Each entry is a `FunctionSchema(name, description, properties, required)`.
    `adapters/schemas/function_schema.py:17-39`
- **Public runtime setter exists:** `LLMContext.set_tools(tools)` replaces the
  whole `ToolsSchema` (re-running normalize/validate).
  `processors/aggregators/llm_context.py:407-413`
- There is **no `add_tool` / `append_tool`** convenience method on `LLMContext`
  or `ToolsSchema`. To add one tool you either append to
  `standard_tools` in place, or build a new `ToolsSchema` and pass it to
  `set_tools`.

In our bot the context is created with `tools=MAC_TOOLS` (a `ToolsSchema`),
`src/twilio_bot.py:754-763`; `MAC_TOOLS` is built at
`src/twilio_bot.py:385-395`.

> Universal vs OpenAI-specific note: we use `LLMContext` from
> `pipecat.processors.aggregators.llm_context`, whose `tools` is **always a
> `ToolsSchema`** (`adapters/services/open_ai_adapter.py:150` comment confirms
> this). This differs from the legacy `OpenAILLMContext`, where `.tools` would
> already be the provider-format list. The `set_tools` / `ToolsSchema` recipe
> below is the correct one for the universal context our bot uses.

## 3. How `register_function` stores handlers and how dispatch works

- `register_function(name, handler, ...)` just writes into a live dict:
  `self._functions[function_name] = FunctionCallRegistryItem(...)`.
  `services/llm_service.py:633-674` (the assignment is `:669-674`).
  `self._functions` is created in `__init__` as a plain dict
  `services/llm_service.py:304`. `function_name=None` registers a catch-all.
- Dispatch reads that **same live dict at call time**. When the LLM emits a tool
  call, `_process_context` builds `FunctionCallFromLLM`s and calls
  `self.run_function_calls(...)` (`services/openai/base_llm.py:515-540`).
  `run_function_calls` looks the handler up fresh:
  `if function_call.function_name in self._functions.keys(): item = self._functions[...]`
  `services/llm_service.py:794-812`.
- It is **re-resolved a second time** at execution time inside
  `_run_function_call` (`services/llm_service.py:857-876`), so even a handler
  registered slightly late is found.
- If no handler matches, it does **not** crash — it routes to
  `_missing_function_call_handler`, which returns a terminal
  "function not currently available" result. `services/llm_service.py:799-801,
  988-1002, 252-254`.

**Safe to call after the pipeline is running?** Yes. `register_function` is a
synchronous dict write with no lifecycle guard, no lock, no "started" check, and
nothing in `start()` snapshots `_functions` (`start()` only touches the
sequential runner / async-tool-cancellation, `services/llm_service.py:355-365`).
Our bot already calls it once per call via `register_mac_tools(llm)` after
construction (`src/twilio_bot.py:440-448, 741`). Calling it again later in the
same session is the same operation.

## 4. After a tool result, does the LLM run again? — Yes

The assistant aggregator re-triggers a completion after a function-call result,
so a tool added *during* the handling of a prior tool call is visible on that
next completion:

- The result handler `_handle_function_call_result` sets `run_llm` and (when
  true and the user isn't speaking) calls
  `self.push_context_frame(FrameDirection.UPSTREAM)`.
  `processors/aggregators/llm_response_universal.py:1294-1370` (push at `:1370`).
- For our case the result is non-empty and `run_llm`/`group_id` default such
  that `run_llm = True` (`:1326-1348`).
- `push_context_frame` -> `_get_context_frame` pushes
  `LLMContextFrame(context=self._context)` — **the same live context object**.
  `processors/aggregators/llm_response_universal.py:433-448`
- That frame re-enters the LLM, which again reads `context.tools` fresh (see §1).

So the loop is: tool call -> handler runs -> result appended -> **LLM runs
again** with the now-mutated context + registry. If you append a tool and
register its handler *inside* a handler before calling `result_callback`, the
**very next** completion already advertises and can dispatch it.

(Caveat: the re-run is deferred while the bot is still speaking or while more
results are queued — `:1350-1367` — but it still uses the live context whenever
it fires, so the new tool is included.)

---

## 5. Recommended hot-add implementation

Minimal correct sequence to hot-add a tool to a live session:

1. **Append the `FunctionSchema`** to the live context's
   `ToolsSchema.standard_tools` (in place — it's the real list), then call
   **`context.set_tools(...)`**. The `set_tools` call is the clean, public way
   to (re-)install the schema and run validation/normalization. (A bare
   in-place `.append` is *also* picked up, because the adapter reads
   `context.tools` fresh each run and `standard_tools` returns the live list —
   but prefer `set_tools` so empty->non-empty normalization and validation run.)
2. **Register the handler**: `llm.register_function(name, handler)`.
3. Nothing else. No cache to invalidate; no pipeline restart; no new
   `LLMContextFrame` needs to be manually pushed for the *next* turn — the
   aggregator pushes the live context on the next user turn or tool-result
   re-run automatically.

```python
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext, NOT_GIVEN, is_given
from pipecat.services.openai.llm import OpenAILLMService


def hot_add_tool(
    llm: OpenAILLMService,
    context: LLMContext,
    schema: FunctionSchema,
    handler,  # async def handler(params: FunctionCallParams) -> None
) -> None:
    """Add a function-calling tool to a LIVE session. Safe to call mid-call.

    The new tool is advertised + dispatchable on the very next LLM completion
    (next user turn, or the re-run that follows a tool result) with NO pipeline
    or daemon restart.
    """
    # 1) Mutate the live context's tools. context.tools is the live ToolsSchema
    #    (or NOT_GIVEN if none were set). Build the updated standard-tools list
    #    and reinstall via the public setter so validation/normalization run.
    current = context.tools
    existing = list(current.standard_tools) if is_given(current) else []
    custom = current.custom_tools if is_given(current) else None
    # Idempotency: don't double-advertise the same tool name.
    if not any(t.name == schema.name for t in existing):
        existing.append(schema)
    context.set_tools(ToolsSchema(standard_tools=existing, custom_tools=custom))

    # 2) Register the handler (live dict write; no lock/lifecycle guard needed).
    llm.register_function(schema.name, handler)

    # 3) Done. No cache invalidation, no restart. The adapter reads
    #    context.tools fresh on the next completion; run_function_calls reads
    #    llm._functions fresh on the next dispatch.
```

To remove a tool live: drop it from `standard_tools` + `set_tools(...)` and call
`llm.unregister_function(name)` (`services/llm_service.py:718-726`).

### Gotchas / notes
- **Per-run, not per-session, resolution.** Tools and handlers are both read
  fresh on every completion/dispatch, so ordering between turns doesn't matter;
  just make sure both steps happen **before** the next completion fires. Doing
  both inside a prior tool handler (before `result_callback`) guarantees the
  immediate re-run sees the new tool.
- **Must be a `ToolsSchema`.** `set_tools` rejects anything else with
  `TypeError`; passing a bare list or dict will fail
  (`processors/aggregators/llm_context.py:459-475`).
- **Universal context.** We use
  `pipecat.processors.aggregators.llm_context.LLMContext` (see
  `src/twilio_bot.py:47`), whose `.tools` is a `ToolsSchema` — use the recipe
  above. The older `OpenAILLMContext` has a different `.tools` shape; don't mix
  them.
- **Missing-handler is non-fatal.** If a schema is advertised but its handler
  isn't registered, the LLM gets a graceful "not currently available" result and
  logs an error (`services/llm_service.py:1016-1038`) — it won't crash the call.
  Still, register the handler in the same hot-add call to avoid that window.
- **Mid-speech deferral.** The post-result re-run is deferred while the bot is
  speaking or while more results are queued
  (`processors/aggregators/llm_response_universal.py:1350-1367`); the new tool
  is still included once it fires (live context).
- **Per-session object.** Each call builds its own `LLMService` + `LLMContext`
  in `/ws` (`src/twilio_bot.py:723-763`), so hot-add the specific live
  `llm`/`context` for that session — there is no shared global to mutate.
- **In-place `.append` works too**, because `standard_tools` returns the live
  list (`adapters/schemas/tools_schema.py:69-76`) and the adapter re-reads it
  each run; `set_tools` is still preferred for the normalize/validate pass.
