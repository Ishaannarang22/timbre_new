# Mac Tools — Architecture Contract (source of truth for all builders)

This is the **binding interface contract** for the voice-controlled Mac tool system.
Every category module, the factory, and the Twilio integration build against the APIs
defined here. Do not change a signature without updating this file.

## Goal
Give the phone agent (`src/twilio_bot.py`, Pipecat 1.2.1 + Nemotron) access to
"everything an AppleScript / Mac can do," via a registry of small, audited tools — plus a
**dynamic factory** that authors a brand-new tool mid-call (via Z.AI/GLM) and hot-registers
it WITHOUT restarting the daemon or dropping the call.

## Safety policy (decided by the owner — enforce server-side, never trust the LLM alone)
| Class | Examples | Rule |
|------|----------|------|
| **SAFE** | get volume, list apps, screenshot, read clipboard, open URL, system info, create note | Run immediately. |
| **CONFIRM** | send message/email, move-to-Trash delete, sleep/lock/logout/restart/shutdown, toggle Wi-Fi/Bluetooth, quit app | Stage the action; return a spoken read-back + `needs_confirmation`; run ONLY after `confirm_action`. |
| Deletion | — | **Trash only** (recoverable). NEVER `rm`/permanent delete. |
| Sends | text/email/Slack/post | Read recipient + content back, then send on confirm. |

There is **no BLOCKED class** — everything is allowed, but every risky thing is CONFIRM-gated.

Generated (factory) tools: SAFE ones auto-activate immediately; ones the validator flags as
risky (`gated_if_generated=True`) are registered **disabled** and require owner approval.

## File layout
```
src/mac_tools/
  __init__.py          # exports REGISTRY, tool, Risk, dispatch, ConfirmationBroker, load_all()
  registry.py          # ToolRegistry, ToolSpec, @tool, dispatch()
  runner.py            # SAFE osascript/shell runners + helpers (the ONLY way to shell out)
  policy.py            # Risk enum + helpers; risk classification constants
  confirm.py           # ConfirmationBroker (per-call pending-action store)
  factory.py           # GLM/Z.AI dynamic tool authoring + hot-registration
  validator.py         # AST/static security validation of generated code
  categories/
    __init__.py        # imports every category module so @tool decorators run
    media.py system.py display.py apps.py windows.py files.py clipboard.py
    screen.py web.py notifications.py productivity.py messaging.py
    input_control.py network.py sysinfo.py power.py
  generated/
    __init__.py        # auto-discovers + imports generated tool modules
```

## `runner.py` — the ONLY shell-out path (no `shell=True`, ever)
```python
OSA_TIMEOUT = 5.0
def run_osa(*lines: str, args: list | None = None, timeout: float = OSA_TIMEOUT) -> str:
    """osascript with each statement as its own -e arg; dynamic USER values passed as
    trailing argv (read in AppleScript via `on run argv`), never string-interpolated.
    Returns stripped stdout; raises subprocess.SubprocessError/TimeoutExpired on failure."""

def run_shell(argv: list[str], timeout: float = 10.0, input_text: str | None = None) -> str:
    """List-arg subprocess (never shell=True). argv[0] must be an absolute path or resolved
    via shutil.which. Returns stripped stdout; raises on nonzero/timeout."""

def audit(action: str, args, result: str) -> None       # append logs/actions.log (same format as mac_actions)
def clamp(n, lo=0, hi=100) -> int
def app_is_running(name: str) -> bool                    # never launches the app
def frontmost_app() -> str | None
```
**Injection rule for category authors:** any value that originates from the caller/LLM and
must reach AppleScript goes through `args=[...]` + `on run argv`. Only values you fully
control (clamped ints, fixed-enum strings) may be inlined into a `-e` line. Validate/clamp
everything. Handlers must NEVER raise into the pipeline — catch and return a friendly string.

## `policy.py`
```python
class Risk(str, Enum): SAFE = "safe"; CONFIRM = "confirm"
```

## `registry.py`
```python
@dataclass
class ToolSpec:
    name: str
    description: str
    properties: dict           # {arg: {"type": "...", "description": "...", "enum": [...]?}}
    required: list[str]
    handler: Callable[..., str]    # SYNC; called handler(**args); returns SHORT spoken string
    risk: Risk = Risk.SAFE
    category: str = "misc"
    confirm_summary: Callable[..., str] | None = None   # CONFIRM: build read-back from args
    gated_if_generated: bool = False
    generated: bool = False
    enabled: bool = True

def tool(name, description, properties=None, required=None, *, risk=Risk.SAFE,
         category="misc", confirm_summary=None, gated_if_generated=False): ...
    # decorator: wraps a sync fn(**args)->str into a ToolSpec and REGISTRY.register()s it.

class ToolRegistry:
    def register(self, spec: ToolSpec) -> None          # last-wins on name collision
    def get(self, name: str) -> ToolSpec | None
    def specs(self, enabled_only: bool = True) -> list[ToolSpec]
    def names(self) -> list[str]
    def function_schemas(self) -> list[FunctionSchema]  # pipecat FunctionSchema per enabled tool
    def tools_schema(self) -> ToolsSchema               # pipecat ToolsSchema(standard_tools=[...])

REGISTRY = ToolRegistry()

def dispatch(name: str, arguments: dict, broker: "ConfirmationBroker") -> dict:
    """Look up the tool, keep only known args, and:
       - SAFE   -> run now, return {"result": <str>}
       - CONFIRM-> broker.stage(summary, do); return {"result": "<readback> Want me to go ahead?",
                   "needs_confirmation": True}
       Unknown/disabled tool -> {"result": "I don't have that tool."} Never raises."""
```
`FunctionSchema(name, description, properties, required)` and
`ToolsSchema(standard_tools=[...])` come from
`pipecat.adapters.schemas.function_schema` / `...tools_schema` (see `twilio_bot.py`).

## `confirm.py`
```python
class ConfirmationBroker:           # ONE per phone call
    def stage(self, summary: str, do: Callable[[], str]) -> None   # store single pending action
    def confirm(self) -> str        # run pending; clear; ("Nothing was waiting." if none)
    def cancel(self) -> str         # drop pending ("Okay, cancelled." / "Nothing to cancel.")
    def pending(self) -> bool
```

## Integration handles (provided by `twilio_bot.py`, used by factory)
- `register_all(llm, broker)` registers an async adapter per enabled tool, plus
  `confirm_action`, `cancel_action`, `request_new_tool`. Each adapter does
  `result = await asyncio.to_thread(dispatch, name, params.arguments, broker)` then
  `await params.result_callback(result)` — so blocking osascript never stalls the event loop.
- **Hot-add (no restart):** factory appends the new `FunctionSchema` to the live
  `context`'s `ToolsSchema.standard_tools`, calls `llm.register_function(name, adapter)`, and
  `REGISTRY.register(spec)`. The next LLM turn in the SAME call then sees + can call it.

## Handler conventions (match `src/mac_actions.py` exactly)
- Sync function, returns a SHORT human/spoken-friendly string (the agent speaks it).
- Clamp/validate inputs; default-deny on bad input with a friendly string.
- `audit(...)` every action.
- Catch all expected exceptions; return a friendly failure string — never raise.
- Never auto-launch an app for a media/control verb unless explicitly the tool's purpose.

## Testing rules for ALL builders (autonomous run — do no harm, make no noise)
- NO sound: never run `say`, `beep`, `afplay`, never play audio, never set audible volume.
- NEVER toggle Wi-Fi/Bluetooth/network OFF, never sleep/lock/logout/restart/shutdown — would
  drop connectivity/the call. You may WRITE these tools; do not EXECUTE them in tests.
- NEVER delete real files. Test file ops only on throwaway paths under `/tmp`.
- NEVER send a real message/email. Test compose paths up to (not including) send.
- Don't quit Terminal/iTerm or the running daemon/uvicorn.
- Read-only or temp-only verification only. Do NOT commit — the owner commits.

## Caller authorization (owner decision: "your number only")
Tools are OFFERED to the model ONLY for an AUTHORIZED call; otherwise the agent runs a
friendly NO-TOOLS persona.
- OUTBOUND (the 7 AM call, Direction=outbound-api): always authorized — we dialed the owner.
- INBOUND (Direction=inbound): authorized IFF the caller's `From` number is on an allowlist.
  - `/twiml` reads `From`, normalizes to E.164, and compares against
    `AUTHORIZED_CALLERS` (comma-separated E.164 in .env; default to `TARGET_PHONE_NUMBER`).
  - It passes `authorized=true|false` to `/ws` via a `<Parameter>` (same channel as token/mode).
  - `/ws`: if not authorized → build the context with NO tools and the no-tools persona prompt;
    if authorized → full registry tools + factory.
- This is the primary gate. (Caller ID can be spoofed; treat it as access-control, not crypto.
  The per-call ws token still blocks non-Twilio scanners.)

## Secrets — HARD carve-out (owner decision: "never")
No tool may read or exfiltrate secrets: Keychain entries, saved/browser passwords, SSH/GPG
private keys, `.env`/dotfiles with credentials, API tokens, `security`/`keychain` CLI dumps,
browser credential stores. `validator.py` MUST reject any generated tool whose code touches
these (deny patterns: `security find-generic-password`, `Keychain`, `.ssh/id_`, `.env`,
`login.keychain`, `*_token`, `password`, etc.), and `factory.py` MUST refuse such a request
and have the agent say it's not allowed to access secrets. This carve-out is non-negotiable
and applies even to an authorized caller.

## `factory.py` — dynamic tool authoring with GLM-5.1 (owner requirement)
When the agent is asked for something with no matching tool, it calls the `request_new_tool`
tool with a plain-English description of what's needed. The factory then:
1. Builds a LIVE system prompt for GLM (see below) and calls **Z.AI `glm-5.1`** (env
   `ZAI_API_KEY`, `ZAI_BASE_URL`, `ZAI_MODEL`) via the OpenAI-compatible API, asking for ONE
   self-contained tool module. GLM-5.1 is used ONLY here, never in the voice hot path.
2. Runs the returned code through `validator.py` (AST + deny-pattern check: no secrets access,
   no `shell=True`, no `eval/exec`, no `rm -rf`/`os.remove`/permanent delete, no raw network
   toggles unless properly risk-tagged, imports limited to an allowlist). Reject → tell the
   owner it couldn't safely build it.
3. Writes the validated module to `src/mac_tools/generated/<name>.py`, imports it (registers via
   `@tool`), and HOT-ADDS it to the live call (append FunctionSchema to
   `context.tools.standard_tools` → `context.set_tools(ToolsSchema(...))` → `llm.register_function`).
4. SAFE generated tool → enabled immediately (usable on the next turn of the SAME call).
   Risk/`gated_if_generated` → registered DISABLED; agent tells owner it needs approval.

`request_new_tool` is itself slow (a GLM round-trip), so its handler returns promptly with a
"building it now, give me a moment" message and the system prompt instructs the agent to say
it doesn't have that yet and is creating it (per owner's wording). On success the agent is told
the new tool name so it can call it.

### The GLM system prompt — LIVE, regenerated every iteration, and documented
`factory.build_glm_system_prompt(task: str) -> str` introspects the LIVE `REGISTRY` each call
and includes, with JUST ENOUGH context to one-shot a correct tool:
- One-paragraph project overview + that the output is ONE Python module for `generated/`.
- The `runner.py` API (`run_osa` with the `on run argv` injection-safe pattern, `run_shell`,
  `audit`, `clamp`, `app_is_running`) and the `@tool`/`ToolSpec`/`Risk` contract.
- The injection/safety rules: pass caller values only as argv, clamp/validate, NEVER raise,
  return a SHORT spoken string, pick the correct `risk` (+ `confirm_summary` for CONFIRM).
- The **secrets HARD carve-out** verbatim (it must NOT need or touch sensitive data).
- The list of installed apps (so it targets apps that exist) and the FULL list of EXISTING
  tools (name · category · description) so it neither duplicates nor reinvents conventions.
- A complete EXEMPLAR tool module to copy the shape from.
- The exact required OUTPUT format (a single fenced ```python module, one `@tool` function).
Every render is also written to `docs/tooling/glm_factory_prompt.md` (the documented, current
version) and the rendered prompt + GLM response are audit-logged — so the prompt is always
up to date with what's been built. The factory NEVER puts secrets/keys/PII into the prompt.
</content>
</invoke>
