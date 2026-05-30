# Complexity-reduction review — `feat/mac-tools-factory-memory`

Read-only analysis. No code was edited. Scope: `src/mac_tools/*` (registry, runner, all 16
category modules, factory, validator), `src/agent_memory/*`, and the `src/twilio_bot.py`
changes. House-style references: `docs/tooling/CONTRACT.md`, `src/mac_actions.py`.

**Baseline facts (measured):**
- 93 registered tools across 17 categories; `src/mac_tools/categories/*.py` = **4361 LOC**.
- The `try → run_osa/run_shell → except subprocess.SubprocessError → audit("error") → return
  friendly string`, **else** `audit(ok) → return msg` idiom is the dominant shape: ~70+
  occurrences across the modules (per-file `except subprocess.SubprocessError` counts:
  power 8, network 7, productivity 7, system 7, display 6, windows 6, apps 5, files 4, web 4,
  input 4, messaging 3, clipboard 3, screen 2, sysinfo 3, notifications 1).
- Verified: the proposed `osa_action` helper preserves both guarantees — never-raise (a bad
  AppleScript returns the friendly string) and injection-safety (argv value `"x y; rm -rf /"`
  comes back literal). Verified the duplicated helpers (`_valid_app_name`, `_clean`) are
  **byte-identical** across the modules that copy them.

The single biggest lever is that **every category re-implements the same osascript/shell
"run-audit-or-friendly-string" boilerplate inline**, plus a handful of literally-duplicated
helpers. Centralizing the boilerplate in `runner.py` (where the contract already says shared
helpers live) is the highest-value, lowest-risk change.

Proposals are ranked by value ÷ risk. Each lists: pattern + locations, the fix (with sketch),
estimated LOC saved, and RISK (safe / needs-care). Anything touching injection-safety or the
never-raise guarantee is flagged **needs-care**.

---

## Ranked summary

| # | Proposal | LOC saved (est.) | Risk |
|---|----------|------------------|------|
| 1 | `runner.osa_action()` / `runner.shell_action()` audit+never-raise wrappers | ~250–400 | needs-care |
| 2 | Hoist duplicated `_valid_app_name` + `_clean` into `runner.py` | ~30 | safe |
| 3 | Collapse `sysinfo` vs `network` Wi-Fi/IP duplication (shared `_wifi_iface`) | ~40–60 | safe |
| 4 | Share `system_profiler` display parsing between `screen.py` and `display.py` | ~25 | safe |
| 5 | Adopt `sysinfo._safe()` pattern (or proposal 1) for the read-only no-arg tools | ~80 | safe |
| 6 | Share `_fmt_date` between `agent_memory` and `categories/memory.py` | ~12 | safe |
| 7 | Factor twilio_bot's repeated `try/except: pass` recorder wrappers | ~20 | safe |
| 8 | Dedupe `_make_adapter` (factory + twilio_bot) and the secret deny-lists | ~25 | needs-care |
| 9 | Minor dead/over-defensive code cleanups | ~15 | safe |

Total realistic reduction: **~450–650 LOC** out of ~4900 in scope (most of it from #1).

---

## 1. Shared `osa_action` / `shell_action` wrappers in `runner.py` — **needs-care**, highest value

### Pattern
Almost every handler is the identical envelope around one or two runner calls:

```python
try:
    run_osa(<lines>, args=[...])           # or run_shell([...])
    msg = "Did the thing."
    audit("tool_name", {...}, msg)
    return msg
except subprocess.SubprocessError as e:
    msg = "Sorry, I couldn't do the thing."
    audit("tool_name", {...}, f"error: {e}")
    return msg
```

This exact block recurs (a partial map of the no-extra-logic instances):
- `power.py` — **all 8** handlers (`lock_screen` 36-51, `sleep_display` 62-73,
  `system_sleep` 83-94, `start_screensaver` 104-117, `logout` 127-139, `restart` 149-160,
  `shutdown` 170-181, `empty_trash` 191-203). These are nearly pure boilerplate: one fixed
  AppleScript line + one ok message + one error message.
- `system.py` — `set_dark_mode` 108-122, `toggle_do_not_disturb` 141-172,
  `toggle_night_shift` 191-225, `get_wallpaper` 244-260, `set_wallpaper` 279-307.
- `apps.py` — `activate_app` 79-99, `hide_app` 111-132, `quit_app` 196-217 (and the
  validate-then-run shape in `launch_app` 50-67).
- `windows.py` — `minimize_front_window` 48-63, `zoom_front_window` 72-86,
  `close_front_window` 110-125, `fullscreen_toggle` 134-151, `focus_app_window` 215-242.
- `clipboard.py` — all 3 (35-48, 59-72, 81-91).
- `notifications.py` — `notify` 41-71.
- `messaging.py` — `send_imessage` 62-95, `send_mail` 119-154, `mail_unread_count` 163-182.
- `productivity.py` — every handler (5 of them, 53-460).
- `display.py` — `brightness_up`/`brightness_down`/etc. (the `_tap_brightness` callers).
- `web.py` — the `_open_url_now` / `_open_url_in` returning bool then re-auditing is a
  variant of the same shape (101-223).

### Fix
Add two small helpers to `runner.py` (the contract already designates runner.py as the home
for shared helpers, and `runner.audit` lives there):

```python
def osa_action(action, ok_msg, *lines, args=None, err_msg=None, timeout=OSA_TIMEOUT,
               audit_args=None) -> str:
    """Run an osascript action, audit, and translate failure into a friendly spoken string.
    NEVER raises. Use ONLY for the run-and-report shape (no post-processing of stdout)."""
    err_msg = err_msg or "Sorry, that didn't work."
    a = {} if audit_args is None else audit_args
    try:
        run_osa(*lines, args=args, timeout=timeout)
    except (subprocess.SubprocessError, ValueError) as e:
        audit(action, a, f"error: {e}")
        return err_msg
    audit(action, a, ok_msg)
    return ok_msg

def shell_action(action, ok_msg, argv, err_msg=None, timeout=10.0, input_text=None,
                 audit_args=None) -> str:
    """Same envelope for run_shell. NEVER raises."""
    ...
```

Then e.g. `power.lock_screen` collapses from 16 lines to:

```python
@tool("lock_screen", "...", risk=Risk.CONFIRM, category="power",
      confirm_summary=lambda: "Lock the screen?")
def lock_screen() -> str:
    return osa_action(
        "lock_screen", "Locked the screen.",
        'tell application "System Events" to keystroke "q" using {control down, command down}',
        err_msg="Sorry, I couldn't lock the screen.",
    )
```

**Verified** (PYTHONPATH=src): the wrapper returns the friendly string on a failing script
(never raises) and still passes argv values through literally (injection-safe), because it
calls the unchanged `run_osa`. Note `run_osa` already raises `subprocess.SubprocessError`
including `TimeoutExpired` (a subclass), so the existing `except subprocess.SubprocessError`
coverage is preserved.

### Why needs-care (not auto-apply)
- It is the never-raise + audit path for ~70 handlers. The wrapper's `except` tuple must
  exactly cover what each call site caught today. Most catch only `subprocess.SubprocessError`;
  a few also catch `ValueError` (e.g. `get_dark_mode`, `toggle_dark_mode`). Use `(SubprocessError,
  ValueError)` in the helper to be a superset — but confirm no handler relied on `ValueError`
  propagating (none do; all return friendly strings).
- Handlers that **post-process stdout** (read-back state, parse output) cannot use the pure
  wrapper — they need the return value. Those should keep an explicit try/except (or use a
  `run`-returning variant). Apply the wrapper **only** to the "fire-and-fixed-message" handlers
  (power.py is the cleanest 100% fit; apps/windows/clipboard/notifications/messaging-send are
  next). Migrate incrementally, module by module, so a regression is isolated.
- Keep the wrapper in `runner.py` so generated/factory tools can use it too (and update the
  exemplar + GLM prompt to show it — a follow-up, not required).

**Recommended rollout:** apply to `power.py` first (8 handlers, exact fit, all CONFIRM so not
executed in tests anyway), verify the registry still loads + schemas unchanged, then expand.
Estimated **~250–400 LOC** removed across all eligible handlers; power.py alone is ~90→~40.

---

## 2. Hoist `_valid_app_name` and `_clean` into `runner.py` — **safe**

### Pattern (verified byte-identical)
- `_APP_NAME_RE`, `_MAX_APP_NAME`, `_valid_app_name` are **duplicated identically** in
  `apps.py` (27-38) and `windows.py` (23-32). Confirmed: same regex pattern, same max, same
  function body.
- `_clean(value, limit)` is **duplicated identically** in `productivity.py` (33-36) and
  `messaging.py` (32-35). Confirmed identical source.

### Fix
Move both into `runner.py` as shared helpers and import them:

```python
# runner.py
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9 .\-]+$")
def valid_app_name(name, max_len=80) -> str | None: ...
def clean_text(value, limit) -> str:
    return str(value or "").strip()[:limit]
```

`apps.py` / `windows.py` import `valid_app_name`; `productivity.py` / `messaging.py` import
`clean_text`. This removes 2 copies of each. The validator's import-allowlist already permits
`mac_tools.runner`, so generated tools could reuse them too.

**LOC saved ~30.** Risk: safe — pure de-duplication of identical code; the injection guard is
unchanged (still argv + the same regex). Worth doing alongside #1 since both touch runner.py.

---

## 3. Collapse the `sysinfo` ↔ `network` Wi-Fi/IP duplication — **safe**

### Pattern
`network.py` and `sysinfo.py` independently reimplement the **same three things**:

- Wi-Fi interface discovery from `networksetup -listallhardwareports`:
  `network._wifi_iface` (46-68, with a module cache) vs `sysinfo._wifi_interface` (39-50,
  no cache, regex-based). Same goal, two parsers, two fallbacks-to-en0.
- Local IP via `ipconfig getifaddr` over `[wifi, en0, en1]`:
  `network.get_local_ip` (88-117) vs `sysinfo.local_ip` (272-289). Same loop, same fallback.
- Wi-Fi SSID via `networksetup -getairportnetwork`:
  `network.get_wifi_name` (126-145) vs `sysinfo.wifi_network_name` (244-263). Same parse of
  the `"Current Wi-Fi Network: "` marker.

This is also a **tool-surface duplication**: the registry now offers BOTH `get_local_ip`
(network) and `local_ip` (sysinfo), and BOTH `get_wifi_name` (network) and `wifi_network_name`
(sysinfo) — two pairs of near-identical tools the LLM must disambiguate between (cognitive load
for the model + the reader). See §3b.

### Fix
3a (mechanical): add one `wifi_iface()` (with the cache) to `runner.py`; both modules import
it. Have `sysinfo.local_ip`/`wifi_network_name` call small shared helpers (or call the
`network.py` implementations). Removes one full interface-discovery parser + one IP loop +
one SSID parser. **~40–60 LOC.**

3b (judgment call — flag for owner, do not auto-apply): consider dropping one tool from each
duplicated pair so the model is offered `get_local_ip` **or** `local_ip` (not both), and
`get_wifi_name` **or** `wifi_network_name` (not both). This shrinks the 93-tool surface and
removes a genuine "which one?" ambiguity. Not auto-safe because it removes advertised tools;
the orchestrator/owner should pick the canonical names.

Risk: 3a is safe (shared read-only helper, no caller input reaches it). 3b is safe-but-product
decision.

---

## 4. Share the `system_profiler SPDisplaysDataType` parse between `screen.py` and `display.py` — **safe**

### Pattern
- `_SYSTEM_PROFILER = "/usr/sbin/system_profiler"` is defined twice (`screen.py:25`,
  `display.py:39`).
- Both run `run_shell([_SYSTEM_PROFILER, "SPDisplaysDataType"], timeout=...)` and parse the
  output for resolutions: `display.get_display_info` (65-125, the richer name+resolution
  parser) and `screen.screen_info` (93-120, a simpler resolution-only parser).
- The two **tools overlap**: `get_display_info` (display) and `screen_info` (screen) both
  "report the displays/resolution" — another near-duplicate tool pair on the surface.

### Fix
Move the constant + a single `_displays() -> list[(name, res)]` parser into a shared spot
(runner.py or a tiny internal helper used by both). `screen_info` can derive its
resolution-only string from the same parsed list. **~25 LOC.** Optionally (judgment) collapse
the two tools into one canonical "display info" tool to reduce the offered surface.

Risk: safe — read-only, no caller input.

---

## 5. Use a `_safe`-style wrapper for the read-only no-arg tools — **safe**

### Pattern
`sysinfo.py` already has a clean local idiom — `_safe(action, fn)` (53-63) wraps a parse
function with audit + never-raise, so each tool body is just `return _safe("x", _do)`. This is
strictly nicer than the inline try/except everywhere else, and it is **only used in sysinfo**.
Read-only tools elsewhere reimplement the same envelope inline:
`apps.list_running_apps` (142-163), `apps.frontmost_app_tool` (172-181),
`messaging.mail_unread_count` (163-182), the `calendar_*` readers in `productivity.py`
(374-459), `network` reads (88-145).

### Fix
Either (a) promote `sysinfo._safe` to `runner.safe_read(action, fn, err_msg=...)` and adopt it
in the other read-only tools, or (b) just let proposal #1's `osa_action` cover the osascript
readers and a `run_action`-returning variant cover the parsers. Pick ONE wrapper so the
codebase has a single "never-raise envelope" rather than two (`_safe` vs inline). **~80 LOC**
across the read-only tools, and it removes the inconsistency of two patterns doing the same job.

Risk: safe, but coordinate with #1 so you don't introduce a *third* wrapper. Recommend the
runner-level wrapper be the single canonical one.

---

## 6. Share `_fmt_date` between `agent_memory` and `categories/memory.py` — **safe**

### Pattern (verified duplicated)
`_fmt_date(ts) -> "May 28"|"recently"` is defined twice with the same intent:
`agent_memory/retrieval.py` (40-46) and `mac_tools/categories/memory.py` (49-58). Same
`time.strftime("%b %d", ...)` with the same exception fallback.

### Fix
Export one `fmt_date` from `agent_memory` (it's already imported by `categories/memory.py`)
and have the category use it. **~12 LOC.** Risk: safe.

---

## 7. Factor the repeated `try: recorder.action(...) except: pass` in `twilio_bot.py` — **safe**

### Pattern
The "best-effort memory capture" block appears verbatim in every adapter:

```python
try:
    recorder.action(tool_name, args, result.get("result"))
except Exception:  # noqa: BLE001
    pass
```

It repeats in `_make_adapter` (the per-tool adapter), `_confirm_action`, `_cancel_action`, and
`_request_new_tool` (4 copies in the diff). The pattern of `try/except: pass` around a
best-effort recorder call recurs ~6 times.

### Fix
A tiny local helper closes over `recorder`:

```python
def _record(tool, args, result):
    try:
        recorder.action(tool, args, result)
    except Exception:  # noqa: BLE001
        pass
```

(Or push the swallow into `CallRecorder.action` — it *already* swallows internally, so the
outer `try/except: pass` is **redundant defense-in-depth**. `recorder.action`, `recorder.turn`,
and `recorder.finalize` each already wrap their whole body in `except Exception: pass`. The
outer guards in twilio_bot can simply be **removed**, since the recorder method cannot raise.)
That's the cleanest win: delete the redundant outer try/except entirely. **~20 LOC.**

Risk: safe — `recorder.*` is already never-raise (confirmed in `recorder.py` 131-195), so
removing the duplicate guard changes nothing behaviorally. Keep ONE guard only where the value
is computed before the call (it isn't here).

---

## 8. Dedupe `_make_adapter` and the secret deny-lists — **needs-care**

### 8a. `_make_adapter` exists twice
`factory._make_adapter` (303-320) and `twilio_bot._make_adapter` (inside
`build_tools_and_register`) build the **same** async adapter: `dispatch` in a worker thread →
`result_callback`, both swallowing exceptions. The factory's omits the recorder; twilio_bot's
adds memory capture. They could share one factory in `runner.py`/`registry.py` that optionally
takes a recorder callback. **~15 LOC.** Risk: needs-care — it's on the live tool-dispatch hot
path; keep the exact `asyncio.to_thread(dispatch, ...)` + never-raise shape. Low urgency.

### 8b. Three overlapping secret deny-lists
The secrets carve-out is encoded **three times**, each slightly different:
- `validator._SECRET_DENY` (validator.py 55-82) — for generated code (source-text patterns).
- `files._SECRET_PATTERNS` (files.py 49-62) — for `read_text_file` path refusal.
- `recorder._SECRET_PATTERNS` + `_SECRET_LINE_MARKERS` (recorder.py 30-55) — for scrubbing
  stored text.

These serve genuinely different inputs (code vs file path vs free text), so full unification is
**not** advisable — but the **shared building blocks** (the file-name markers: `.env`, `.ssh/id_`,
`login.keychain`, `id_rsa|ed25519|dsa|ecdsa`, `security find-generic-password`) are copied
verbatim in all three. Extract just those literal markers into a single
`agent_memory`/shared constant and reference it, so a future addition to the deny-list can't be
forgotten in one of the three places. **~10 LOC**, but the real value is correctness/safety:
one source of truth for the file-name markers.

Risk: **needs-care** — this is the secrets carve-out (non-negotiable per CONTRACT.md §Secrets).
Any refactor must keep all three call sites strictly ⊇ their current pattern set. Recommend:
extract only the shared marker list, leave each module's input-specific patterns in place, and
add a test asserting each known secret string is still rejected/scrubbed/refused. Do NOT
auto-apply.

---

## 9. Minor dead / over-defensive code — **safe**

- **`windows._FRONT_PROC`** is wrapped in needless parens: `_FRONT_PROC = ("first ...")` — the
  parentheses around a single string literal (37-39) do nothing; minor, cosmetic.
- **`files.get_info`** does `import time as _time` *inside* the function (328) and `move_file`
  does `import shutil` inside the `except` (383). `files.py` already imports `os`, `re`,
  `subprocess`, `Path` at top; these lazy imports add a line each and don't buy anything (no
  heavy/optional dep). Hoist to module top. ~4 LOC + readability.
- **`display.py`** imports `os` mid-file with a `# noqa: E402` (system.py does the same at 233).
  Both can move to the top import block. Cosmetic.
- **`registry._filter_args`** guards `if not isinstance(arguments, dict): return {}` — fine, but
  note `dispatch` already only ever receives a dict from the adapters; harmless, keep (cheap
  defense). Not a change, just noting it's not a bug.
- **`factory._extract_python`** fallback (`"@tool" in stripped or "def " in stripped`) is a
  reasonable safety net; keep.
- **`twilio_bot` `messages = context.get_messages() or context.messages`** (finally block) — two
  ways to get messages with a try/except around each; fine as belt-and-suspenders on teardown.

These are tiny; bundle them only if touching the file anyway.

---

## Things that are NOT over-engineered (leave alone)

- The **injection-safe argv pattern** (`on run argv` + `args=[...]`) is consistently applied and
  is the core safety property — do not "simplify" any caller value into an inlined `-e` line.
- **`media.py`** correctly thin-wraps `mac_actions` rather than re-implementing — that's the
  right call, not duplication.
- The **per-module docstrings** are long but they encode real safety rationale (TCC grants,
  Trash-only, no-sound) and match the project's "teach while building" working agreement. Don't
  cut them for LOC.
- **`confirm.py` / `policy.py` / `registry.py`** are already minimal and clean.
- The **agent_memory store** short-lived-connection + lock pattern is appropriate for the
  single-call-at-a-time workload; not over-built.
- The **validator's** dual raw-text + AST passes are intentional defense-in-depth for the
  secrets carve-out — keep both.

---

## Suggested apply order (for the orchestrator)

1. **Safe, mechanical first:** #2 (hoist identical helpers), #6 (`_fmt_date`), #7 (drop
   redundant recorder guards), #9 (cosmetics). Low blast radius, immediately verifiable by
   "registry still loads 93 tools + schemas unchanged."
2. **Safe, parsing dedup:** #3a, #4 (shared `system_profiler`/wifi helpers). Verify read-only
   tools still return sensible strings.
3. **High-value, needs-care:** #1 — add `osa_action`/`shell_action` to `runner.py`, migrate
   `power.py` first, then expand module-by-module; #5 — unify on the single runner wrapper.
   Re-run the registry load + a dry dispatch of a couple SAFE tools after each module.
4. **Needs-care, do last / owner sign-off:** #3b/#4 tool-pair consolidation (changes the
   offered surface), #8 (`_make_adapter` dedup; shared secret markers with a regression test).

Verification used here was read-only: `PYTHONPATH=src .venv/bin/python` to confirm the
`osa_action` sketch never raises + preserves argv literalness, to count the 93 tools, and to
confirm the duplicated helpers are byte-identical. No network, no sound, no code edits.
