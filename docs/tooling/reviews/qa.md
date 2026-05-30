# QA / Problem-Finder Report — mac-tools + agent-memory

Branch: `feat/mac-tools-factory-memory`
Scope: `src/mac_tools/**`, `src/agent_memory/**`, `src/twilio_bot.py` integration,
`scripts/grant_permissions.py`, `scripts/memory_cli.py`. Verified against `docs/tooling/CONTRACT.md`.

Method: loaded the full registry (93 tools, no duplicate names), dispatched SAFE read-only tools,
exercised the `ConfirmationBroker` directly, round-tripped `agent_memory` against a TEMP `/tmp` DB
with a monkeypatched summarizer, and ran `factory.create_tool` with a MOCKED GLM (`_completion`).
No real network/Twilio/Z.AI/Deepgram/Cartesia calls, no sound, no destructive/CONFIRM-to-completion
tools, no touch of the real `data/` DB or the daemon. All test artifacts (2 generated test modules,
the temp DB, the test-mutated `glm_factory_prompt.md`) were cleaned up / restored.

---

## P0 — breaks a real call / data risk

### P0-1 `CallRecorder(... mode=mode ...)` raises `TypeError` on EVERY authorized call
**File:** `src/twilio_bot.py:926`
```python
recorder = agent_memory.CallRecorder(call_sid, mode=mode, caller=caller)
```
`CallRecorder.__init__` is `(self, call_sid, direction="outbound", caller="")` — there is **no
`mode` parameter**. Passing `mode=` raises `TypeError: CallRecorder.__init__() got an unexpected
keyword argument 'mode'`.

This line sits inside the `if authorized:` block and is **NOT** wrapped in try/except (the only
`try` above it wraps `recall()`). It runs during `/ws` setup BEFORE the pipeline is built, so the
exception propagates out of the websocket handler and the call dies — no greeting, no tools, no
conversation. Because `recorder` never gets assigned, the `finally` block's `if recorder is not
None:` also skips, so nothing is persisted either.

Blast radius = **every authorized call**: the 7 AM outbound morning call (always authorized) AND
every authorized inbound call. This is the most severe issue in the changeset — the headline
feature (memory + the whole authorized tool surface) never runs.

Repro (verified):
```
$ PYTHONPATH=src .venv/bin/python -c "from agent_memory.recorder import CallRecorder; CallRecorder('CA', mode='inbound', caller='+1')"
TypeError: CallRecorder.__init__() got an unexpected keyword argument 'mode'
```
**Fix:** `recorder = agent_memory.CallRecorder(call_sid, direction=mode, caller=caller)`
(the local variable is named `mode`; the recorder/store column is `direction`).

---

## P1 — wrong behavior

### P1-1 Boolean tool args do the OPPOSITE when the LLM sends a string `"false"`
**Files:**
- `src/mac_tools/categories/media.py:63` `set_muted(muted)` → `bool(muted)` (via `mac_actions.set_muted`, `src/mac_actions.py:134`)
- `src/mac_tools/categories/system.py:108-109` `set_dark_mode(enabled)` → `flag = bool(enabled)`
- `src/mac_tools/categories/network.py:204` `set_wifi_power(on)` → `bool(on)`
- `src/mac_tools/categories/network.py:236` `set_bluetooth_power(on)` → `bool(on)`

These schemas declare `"type": "boolean"`, but the handlers coerce with bare `bool(x)`. When a
model emits the JSON arg as a **string** (`"false"` / `"true"` / `"0"`), Python truthiness makes
**any non-empty string `True`**:
```
bool("false") == True    bool("true") == True    bool("0") == True
```
So "turn dark mode off", "unmute", "turn Wi-Fi off" can all silently do the *opposite*. Integer
tools are unaffected — they go through `runner.clamp()` which does `int(n)` and handles strings
correctly (`clamp("40") == 40`). Only the boolean handlers are exposed.

Likelihood depends on the model (Nemotron-nano is small and not always type-clean about JSON), but
the failure mode — confidently doing the inverse of a destructive/CONFIRM action (Wi-Fi/BT toggle) —
is bad enough to fix.

**Fix:** add a shared string-aware bool coercer, e.g. treat `"false"/"0"/"no"/"off"/""` (case-
insensitive) and falsey values as `False`, everything else as truthy; apply in all four handlers
(and ideally fix `mac_actions.set_muted` at the root).

### P1-2 `request_new_tool` GLM round-trip has NO bounded timeout
**Files:** `src/mac_tools/factory.py:285-300` (`_call_glm` builds `OpenAI(...)` with no
`.with_options(timeout=...)`), and `src/twilio_bot.py:490-497` (the adapter calls
`asyncio.to_thread(create_tool, ...)` with no `asyncio.wait_for`).

`create_tool` → `_call_glm` builds a plain `OpenAI(api_key=..., base_url=...)` client with **no
timeout**, so it uses the SDK default (~600 s / 10 min). The summarizer, by contrast, correctly does
`OpenAI(...).with_options(timeout=8.0, max_retries=1)` (`src/agent_memory/summarizer.py:84-85`).

In a live call, `_request_new_tool` queues an immediate spoken filler (`append_to_context=False`),
which is good — the event loop and the caller's audio are NOT blocked. But the **function-call
result** (`params.result_callback`, `twilio_bot.py:520`) cannot fire until `create_tool` returns. If
Z.AI stalls, that LLM tool turn stays pending for up to ~10 minutes. The `MAX_CALL_SECS=150`
backstop will eventually `EndFrame` the call, but the turn hangs unresolved in the meantime.

The CONTRACT explicitly says: "`create_tool` ... have a bounded timeout" / "returns promptly".
**Fix:** mirror the summarizer — `client.with_options(timeout=~20s, max_retries=0/1)` in
`_call_glm`, and/or wrap the `to_thread(create_tool, ...)` call in `asyncio.wait_for(...)` in
`twilio_bot._request_new_tool`.

### P1-3 Required-arg handlers with no parameter default surface a generic error instead of their own friendly read-back
**Files (23 handlers):** e.g. `src/mac_tools/categories/files.py:173` `search_files(query: str, ...)`,
`apps.py` `launch_app(name)/activate_app/hide_app/quit_app`, `files.py`
`open_path/reveal_in_finder/list_dir/make_folder/get_info/move_file/read_text_file/move_to_trash`,
`clipboard.py` `set_clipboard(text)`, `input_control.py`
`type_text/press_key/key_combo/mouse_click/mouse_move`.

These declare a required arg but the handler param has **no default value**. If the LLM omits a
required arg, `dispatch` calls `handler(**{})`, which raises `TypeError: missing required positional
argument` **before the function body runs** — so the handler's own graceful "Tell me what to search
for." branch is never reached. `dispatch`'s catch-all then returns the generic
`"Sorry, that didn't work."` (verified: `dispatch('search_files', {})` → `"Sorry, that didn't
work."`, not the handler's friendlier message).

The pipeline never crashes (dispatch is the backstop), so this is wrong-behavior/UX rather than a
crash. The CONTRACT's "default-deny on bad input with a friendly string" intent is defeated by the
signatures.

**Worse for CONFIRM tools with a defaulted `confirm_summary`** (`quit_app`, `move_to_trash`): the
`confirm_summary` lambda *does* have a default, so it doesn't raise — it produces a nonsensical
read-back and stages a broken action. Verified:
```
dispatch('quit_app', {}, broker) -> {'result': 'Quit ? Want me to go ahead?', 'needs_confirmation': True}
broker.confirm()                 -> 'Sorry, that didn't work.'
```
i.e. the agent reads back "Quit ?" (empty app name) and stages an action that fails on confirm.

**Fix:** give every required handler param a default (`= ""` / `= None`) so the handler body's
validate-and-friendly-refuse runs, OR have `dispatch` check `spec.required ⊆ args.keys()` up front
and return a friendly "I need the X to do that." Either keeps the friendly-string contract intact.

---

## P2 — polish / observations (correct-by-design but worth noting)

### P2-1 Double-staged CONFIRM action is silently overwritten (by design, but not surfaced)
**File:** `src/mac_tools/confirm.py:28-32` (`stage` replaces unconditionally),
dispatch `src/mac_tools/registry.py:182`.
If the model requests two CONFIRM tools before the owner confirms, the second `stage()` silently
drops the first; a later `confirm_action` runs only the second. This matches the CONTRACT ("Only ONE
action can be pending at a time: staging a new action replaces any prior one") and `confirm.py`'s
docstring, so it is **intended**. The only gap is that nothing tells the model/user the first was
discarded. Verified the overwrite. Optional polish: have `stage()`/`dispatch` mention when it
replaces an existing pending action so the model can re-offer the dropped one. Low priority.

### P2-2 Caller extension digits defeat the authorization allowlist match
**File:** `src/twilio_bot.py:114-123` `_normalize_e164`.
`'+14155550100x123'` normalizes to `'+14155550100123'` (extension digits appended), so an authorized
owner whose CID carries an extension would NOT match the bare `+14155550100` allowlist entry and
would be denied tools. Anonymous/withheld/restricted all normalize to `''` and correctly fail the
allowlist (the gate fails closed — verified). Extensions on caller ID are uncommon; minor.

### P2-3 `_call_glm`'s real-client path uses `OpenAI(...)` without `max_retries`/timeout knobs
Covered functionally by P1-2; calling out separately only as a consistency note — the rest of the
codebase consistently pins `timeout`/`max_retries` on OpenAI clients (`summarizer.py`,
`twilio_bot.py:894`) and the factory should too.

### P2-4 `load_all()` reload comment over-claims pickup of freshly-written modules
**File:** `src/mac_tools/__init__.py:60-63`. `importlib.reload(mac_tools.categories)` re-runs the
package `__init__` body, which `import_module`s already-cached child modules WITHOUT re-running their
`@tool` decorators, so a *newly-added* category file isn't actually picked up by the reload alone.
This is harmless in practice (the factory imports generated modules directly; register is
last-wins; idempotency verified — 2× `load_all()` gives a stable count) — it's only the docstring's
"so a freshly-written module gets picked up" that overstates what reload does. Doc-only.

---

## Things checked and found CORRECT (no action)

- **No duplicate tool names** across the 93 registered tools, and no duplicate `name=` declared in
  source. Control tools (`confirm_action`/`cancel_action`/`request_new_tool`) don't collide with any
  registry tool.
- **Integer args** are robust to string input via `runner.clamp` (`clamp("40")==40`,
  `clamp("abc")==0`) and `int()`+except in `search_files`/`list_recent_calls`.
- **`dispatch` never raises** — unknown/disabled tool → friendly string; SAFE/CONFIRM handler
  exceptions are caught (defense in depth) and `confirm_summary` exceptions fall back to description.
- **`ConfirmationBroker`**: `confirm()`/`cancel()` with nothing staged return
  "Nothing was waiting."/"Nothing to cancel."; `confirm()` clears before running; exceptions in the
  deferred `do` become a friendly string.
- **Factory safety gating** (mocked GLM, verified): SAFE module → registered ENABLED;
  secrets-touching module → **REJECTED** (raw-text deny fires first); send/CONFIRM module →
  registered **DISABLED** and absent from the enabled schema (awaits approval). `create_tool` returns
  `{ok,...}` on every path and never raises.
- **agent_memory round-trip** (temp `/tmp` DB + monkeypatched summarizer): turns/summary/facts
  stored; `recall(known)` returns the compact block, `recall(unknown)`/`recall("")` return `""`
  (the recognition gate works). `context.get_messages()` returns `list[{"role","content"}]` dicts —
  matching `recorder.finalize`'s assumption; non-string content is gracefully skipped.
- **`recorder.finalize`** dedupes (skips backfill if turns already captured), is idempotent
  (`_finalized` guard), scrubs all text, and swallows everything.
- **Secret scrubbing** (`recorder.scrub`) and **`memory_cli add`** refuse/redact secret-shaped
  input before storing.
- **No-try handlers** (`open_url`, `web_search`, `key_combo`, `disk_space`, sysinfo readers, …) all
  delegate to wrapped helpers (`_safe`, `_open_url_now`, runner helpers), so they don't raise into
  the pipeline; dispatch is the final backstop regardless.
- **`grant_permissions.py`** probes are read-only, short-timeout, sound-free, and classify
  -1712/-1743 correctly; `memory_cli.py` sets `--db` before any `init_store()` in `main()`.

---

## Summary
- **P0: 1** — `CallRecorder(mode=...)` `TypeError` kills every authorized call (`twilio_bot.py:926`).
- **P1: 3** — (a) boolean args coerced with bare `bool()` invert on string `"false"`
  (`set_muted`/`set_dark_mode`/`set_wifi_power`/`set_bluetooth_power`); (b) factory GLM call has no
  bounded timeout (`factory._call_glm` + `twilio_bot._request_new_tool`); (c) 23 required-arg handlers
  with no param default surface a generic error / stage a broken CONFIRM instead of their friendly
  refusal.
- **P2: 4** — pending-confirm overwrite not surfaced (by design); extension digits defeat allowlist;
  factory client knobs; `load_all` reload comment over-claim.
