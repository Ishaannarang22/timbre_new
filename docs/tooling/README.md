# Voice-Agent Mac Tooling — overview & operator guide

This subsystem gives the Timbre phone agent the ability to **do things on the Mac** during a
call, to **author brand-new tools on the fly** when it lacks one, and to **remember** across
calls. Built M4 (2026-05-28) as an orchestrated multi-agent effort.

- **Binding spec / interface contract:** [`CONTRACT.md`](CONTRACT.md) (read this first to change anything).
- **Live GLM factory prompt (auto-generated each render):** [`glm_factory_prompt.md`](glm_factory_prompt.md).
- **Audit reports (QA / security / complexity / code-check):** [`reviews/`](reviews/).
- **Pipecat hot-reload proof:** [`hot_reload_findings.md`](hot_reload_findings.md) · **GLM model choice:** [`zai_findings.md`](zai_findings.md).

## What's where
```
src/mac_tools/
  registry.py     ToolSpec + @tool decorator + REGISTRY + dispatch() (type-coerces args,
                  guards required args, gates CONFIRM tools on the broker, never raises)
  runner.py       the ONLY shell-out path: run_osa (injection-safe via `on run argv`),
                  run_shell (list-args, never shell=True), audit(), clamp(), app_is_running()
  policy.py       Risk = SAFE | CONFIRM
  confirm.py      ConfirmationBroker — one pending action per call (stage/confirm/cancel)
  validator.py    AST STRUCTURAL ALLOWLIST for generated code (import-safe, no eval/getattr/
                  os/subprocess/secrets); the gate the factory runs every authored tool through
  factory.py      request_new_tool -> GLM-5.1 authors a module -> validator -> write generated/
                  -> import -> HOT-ADD to the live call (no daemon restart). Timeout + abuse cap.
  categories/     ~90 tools across: media, system, display, apps, windows, files, clipboard,
                  screen, web, notifications, productivity, messaging, input, network, power, memory
  generated/      where factory-authored tools land (auto-discovered on load)

src/agent_memory/ cross-call memory: store.py (SQLite at data/, gitignored), recorder.py
                  (capture+scrub+summarize a call), summarizer.py (Nemotron, NOT the GLM key),
                  retrieval.py (recall(caller) -> prompt block; search())

scripts/grant_permissions.py   macOS TCC helper (--check probes silently; --open pops the panes)
scripts/memory_cli.py          terminal/harness memory lookup (calls/facts/search/add/show)
```

## Safety model (enforced server-side — never trusted to the LLM)
- **Risky actions are CONFIRM-gated:** sends, deletes, and disruptive/system/network-toggle
  actions are *staged*, read back aloud, and run **only** after a `confirm_action` tool fires.
- **Deletion is Trash-only** (recoverable) — never `rm`/permanent delete.
- **Caller authorization (owner-only):** tools are offered only to an authorized caller — the
  7 AM outbound call (we dialed you) always, an inbound call only if `From` ∈ `AUTHORIZED_CALLERS`
  (defaults to `TARGET_PHONE_NUMBER`). Unknown callers get a friendly chat-only persona.
- **Secrets are a hard carve-out:** no tool — and not the GLM factory — may read Keychain,
  passwords, SSH/GPG keys, `.env`, `~/.aws`/`~/.netrc`/`.gnupg`, browser credential stores, or
  tokens. Enforced in `read_text_file`, the validator, and the memory scrubber.

## Dynamic tool factory (GLM-5.1)
When the agent has no matching tool it calls `request_new_tool(description)`. The handler speaks
an immediate "give me a few seconds, I'm building it" filler, then off the event loop:
render a **live** system prompt from the current registry → call **Z.AI GLM-5.1** (env
`ZAI_API_KEY`/`ZAI_BASE_URL`/`ZAI_MODEL`, 45 s timeout, used *only* here) → `validator` →
write to `generated/` → import (registers via `@tool`) → hot-add to the live `context`+`llm`.
SAFE tools are usable on the next turn of the **same** call; risky ones register disabled,
pending owner approval. Abuse cap: ≤5 creations / 10 min and ≤30 generated files.

## Memory
Each finished call is captured (turns + tool invocations), secret-scrubbed, and summarized by
Nemotron into a short summary + durable facts (deduped, weight-bumped). At the start of the next
call, `recall(caller)` injects "what to remember about this caller" into the system prompt. The
agent can also query memory live (`recall_memory`, `remember_this`, `list_recent_calls`), and you
can inspect it from a terminal with `scripts/memory_cli.py`.

## Operating it
1. **Grant macOS permissions** (one-time): `.venv/bin/python scripts/grant_permissions.py --open`
   and add **both** your Terminal and `.venv/bin/python` to Accessibility, Automation, Screen
   Recording, and Full Disk Access. Re-run with `--check` to confirm. Until granted, automation
   tools return friendly failures instead of acting.
2. **Pick up new code:** the warm-tunnel daemon (`scripts/serve_persistent.py`) loads `twilio_bot`
   at boot, so restart the daemon to run this branch's code.
3. **Authorized callers:** set `AUTHORIZED_CALLERS` in `.env` (comma-separated E.164); defaults to
   `TARGET_PHONE_NUMBER`.
</content>
