# PRD — Postpartum voice-flow agent

**Owner:** Kanika (kanika@findraya.com)
**Hand-off:** This document is the complete brief. The receiving agent has no prior context.
**Sibling PRD:** `~/Documents/GitHub/timbre_dashboard/supabase/CREATE_TABLES_PRD.md` (tables + DB setup). That work can run in parallel with this one — they only meet at `.env` and the live dashboard URL.

---

## Goal

Build a **postpartum maternal check-in voice agent** on top of `timbre_new` using **Pipecat Flows** (a state-graph layer on top of Pipecat). The agent runs a structured clinical conversation — recovery → mental-health screen → newborn check → medication adherence → social screen → doula handoff → CSAT — and pushes every answer to the timbre dashboard's `/api/v1/*` routes in real time. It also exposes **global functions** that fire mid-flow: clinical escalations (nurse/pediatric/crisis) and a patient concierge (billing/appointments/prescriptions lookups + open-ended feedback capture).

Done when:
- `pipecat-ai-flows` is in `requirements.txt` and imports clean.
- `src/dashboard_client.py` exists and can hit every dashboard endpoint with bearer-token auth.
- `src/flows/postpartum.py` defines the full NodeConfig graph + global functions and returns a `FlowConfig`.
- `prompts/prompts.json` has one entry per node + per global function.
- `src/postpartum_bot.py` is a new entrypoint that runs the Flow over the same Twilio + Deepgram + Nemotron + Cartesia pipeline as `twilio_bot.py`.
- A simulated dry-run with `scripts/sim_twilio_ws.py` walks all 7 nodes end-to-end and successfully POSTs to the dashboard.
- **The existing inbound voice companion (`twilio_bot.py`) still works unchanged.**

---

## Pipecat vs Cekura — the boundary (read this if there's any confusion)

These are two different systems with **non-overlapping responsibilities**. Crossing the boundary is the #1 way this project goes off the rails.

| | **Pipecat (+ Pipecat Flows)** | **Cekura** |
|---|---|---|
| Role | The live voice runtime | The evaluation harness |
| When it runs | During every real phone call | Offline, on demand (not during real calls) |
| Hosts | Pipecat Cloud | Cekura's own infra (we connect via MCP) |
| What it owns | Twilio audio ↔ Deepgram STT ↔ Nemotron LLM ↔ Cartesia TTS, the postpartum NodeConfig state graph, global functions (escalate / concierge), the API calls to the dashboard | Persona simulations (the 4 patients: Contradiction / Cost-Blocker / Proxy / Ambiguous Healer), scoring runs against criteria (node transition accuracy, context isolation, tool-call latency, global-function reliability, PII redaction, escalation correctness), posting results |
| Talks to dashboard via | `POST /api/v1/escalations`, `PATCH /api/v1/calls/{id}`, etc. — the "live data" routes | `POST /api/v1/evals`, `PATCH /api/v1/evals/{id}`, `POST /api/v1/evals/{id}/results` — the "evaluation" routes |
| Knows about the other? | **No.** The Pipecat agent has zero awareness of Cekura. It runs the same code whether a real patient or a Cekura persona is on the line. | **Yes, partial.** Cekura drives a synthetic call against the Pipecat agent and observes its behavior from the outside (transcripts, function calls, dashboard writes). It does not modify the Pipecat code. |

**Why this matters for your work:** This PRD is the **Pipecat** half. You build the voice runtime, the state graph, the global functions, and the `/api/v1/*` writes the agent does during a call. You do **NOT** wire any Cekura code into `timbre_new/`. There is no `cekura` package import in the Pipecat repo. The Cekura wiring is a separate task (see `~/Documents/GitHub/timbre_dashboard/README.md` for that flow) and happens outside this repo.

**What this means concretely:**
- ✅ Do build: `pipecat-ai-flows` based NodeConfig graph, `dashboard_client.py` with all 16 patient/call/escalation/feedback methods, the 3 escalation global functions, the 3 concierge global functions, the `capture_feedback` global function.
- ✅ Do build the eval-write methods on `dashboard_client.py` (`start_eval`, `post_eval_result`, `finish_eval`) — but only because Cekura might later drive an *external* runner that uses our client lib. The Pipecat live path never calls them.
- ❌ Do NOT add a Cekura SDK to `requirements.txt`. There is no `pip install cekura` step in this work.
- ❌ Do NOT make the Pipecat agent "evaluate itself." Self-evaluation is what Cekura does, from outside.
- ❌ Do NOT create persona files (`personas/*.yaml`) inside `timbre_new/`. Those live in the Cekura side.

---

## Context (read this first)

### Repo layout (already exists)

```
~/Documents/GitHub/timbre_new/
├── .env                    # populated — has NVIDIA, Deepgram, Cartesia, Twilio keys,
│                           # plus DASHBOARD_API_URL + DASHBOARD_API_TOKEN
├── CLAUDE.md               # project guide — wellness/check-up pivot (2026-05-30)
├── requirements.txt        # current Pipecat deps
├── prompts/prompts.json    # one prompt right now (m0_local_mic_voice_agent)
├── src/
│   ├── twilio_bot.py       # inbound voice companion — production phone webhook + Pipecat pipeline
│   │                       # Pattern to mirror: /twiml + /ws + per-call token + greeting seed
│   │                       # + Smart Turn endpointing + GoodbyeProcessor.
│   ├── m0_local_bot.py     # local-mic dev variant. Same brain, no telephony.
│   ├── prompts.py          # tiny loader for prompts/prompts.json
│   └── turn_helpers.py     # PatientSmartTurnV3 — prosody endpointing
├── scripts/
│   ├── sim_twilio_ws.py    # simulator — use this for end-to-end test
│   ├── serve_persistent.py # the production webhook server
│   └── bench_llm_latency.py
└── docs/                   # architecture.md, roadmap.md, setup.md (read these)
```

### Stack (locked — do not change)

- **LLM:** NVIDIA Nemotron via `https://integrate.api.nvidia.com/v1` (OpenAI-compatible). Default model `nvidia/nemotron-3-nano-30b-a3b`. `enable_thinking: False`. Temp 0.2, top_p 0.95.
- **STT:** Deepgram (NVIDIA speech is partner-gated for this key).
- **TTS:** Cartesia Sonic. Default voice: Brooke (`e07c00bc-4134-4eae-9ea4-1a55fb45746b`). Speed 0.95.
- **Telephony:** Twilio Media Streams. **Trial account** — only dials `TARGET_PHONE_NUMBER` (verified). 8kHz μ-law throughout.
- **Endpointing:** `PatientSmartTurnV3` (in `turn_helpers.py`). Reuse it via `build_turn_analyzer()` from `twilio_bot.py` if you can — preloaded ONNX model is non-trivial.

### Dashboard (parallel work — may or may not be live yet)

The dashboard repo is `~/Documents/GitHub/timbre_dashboard`. It exposes 18 routes under `/api/v1/*`. Until the sibling PRD finishes, `DASHBOARD_API_URL=http://localhost:3000` (in `.env`). After the table-creation PRD wraps, it'll be a Vercel URL. Your code must:
- Read `DASHBOARD_API_URL` and `DASHBOARD_API_TOKEN` from env.
- Send `Authorization: Bearer $DASHBOARD_API_TOKEN` on every call.
- Tolerate `DASHBOARD_API_URL` being unset during early dev — log a warning and no-op, never crash the call.

Full API contract is in `~/Documents/GitHub/timbre_dashboard/README.md` (the table near the bottom). Skim it before writing `dashboard_client.py`.

---

## Decisions already made (do NOT re-litigate)

| Decision | Value |
|---|---|
| Flow framework | **Pipecat Flows** (`pipecat-ai-flows`). Use `FlowManager` + `NodeConfig` + `FlowsFunctionSchema`. |
| Context strategy | **APPEND** by default. RESET only on big transitions like leaving identity_verify. |
| Languages | **English + Spanish**. Detect from `patient.language` at call start; seed the system message accordingly. Don't try to auto-switch mid-flow in v1. |
| Identity verify | Confirm name + dob (or last 4 of phone). If someone other than the patient answers ("Hi, this is her husband"), trigger `proxy_reject_reschedule` node and end politely. |
| Global functions | Defined at FlowManager level so they're available in every node. See "The graph" section. |
| Escalation behavior | Calling `escalate_to_nurse` / `escalate_pediatric` / `escalate_crisis` ends the call after a brief reassurance line. POST to `/api/v1/escalations` first, then transition to a terminal `escalation_handoff` node that thanks them, says "a nurse will call you back within X minutes" (urgent: 15, warning: 60), and hangs up. |
| Concierge interrupts | When the patient interrupts a clinical node to ask about billing/appointments/rx, the concierge global function answers, then the LLM is instructed to "and now let's continue where we left off — [last node question]". Pipecat Flows context preserves state. |
| Feedback capture | Always available globally. The agent may also explicitly ask at end-of-CSAT: "Anything you'd like the hospital to hear?" |
| Telephony entrypoint | NEW file `src/postpartum_bot.py`. Do not modify `src/twilio_bot.py`. |
| Greeting | Like `twilio_bot.py`, seed a deterministic first turn so the model can't re-greet on barge-in. English: `"Hi {preferred_name}, this is Maya from Raya Memorial calling to check in on you and the baby. Is this a good time?"`. Spanish equivalent. |
| Backstop | Keep `MAX_CALL_SECS` guard from `twilio_bot.py`. Set to **900s (15 min)** for postpartum — much longer than the companion's 150s. |
| Call records | At /ws connect time, POST `/api/v1/calls` to create the row (capture the returned `call_id` — every downstream POST needs it). On every node transition PATCH `/api/v1/calls/{id}` with `current_node`. On disconnect, PATCH with `status='completed'` and `ended_at=now()`. |
| PII redaction | Demo-grade. Strip phone numbers, emails, SSNs, and address-looking strings from the `transcript_redacted` field before POSTing to `/api/v1/calls/{id}`. Use a simple regex pass — Presidio is overkill for the demo. |

---

## Step-by-step plan

### 1. Inventory and read

Read these files end to end before writing anything:

- `CLAUDE.md` — the wellness pivot direction (2026-05-30).
- `src/twilio_bot.py` — the production pattern you're modeling on. Pay attention to: `/twiml` per-call token, `<Parameter>` delivery, greeting-seed trick, `build_turn_analyzer()`, MAX_CALL_SECS guard, `BotStoppedSpeakingFrame` hangup.
- `src/m0_local_bot.py` — sampling params, endpointing knobs, metrics observers.
- `src/prompts.py` — how prompts are loaded.
- `prompts/prompts.json` — current shape (single JSON object, key = agent name).
- `requirements.txt`.
- `~/Documents/GitHub/timbre_dashboard/README.md` (sibling repo) — the API contract table.
- `~/Documents/GitHub/timbre_dashboard/src/lib/types.ts` — payload shapes for `recovery_answer`, `newborn_answer`, etc. (the field names you POST must match.)

### 2. Add Pipecat Flows + verify import

```bash
cd ~/Documents/GitHub/timbre_new
# Edit requirements.txt — add this line under the pipecat-ai[...] line:
#   pipecat-ai-flows
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c "from pipecat_flows import FlowManager, FlowConfig, NodeConfig, FlowsFunctionSchema; print('ok')"
```

If a venv doesn't exist at `.venv`, create one (`python3.11 -m venv .venv`) and install. Don't change the Python version — Pipecat's audio bindings can be fragile across versions.

### 3. Write `src/dashboard_client.py`

A thin async HTTP client used by every node + global function. Specifics:

- Use `httpx.AsyncClient` (already a transitive dep of pipecat; verify with `pip show httpx`).
- Construct with `base_url = os.environ["DASHBOARD_API_URL"]` and a `headers={"Authorization": f"Bearer {os.environ['DASHBOARD_API_TOKEN']}"}` default.
- If either env is missing, return a **no-op stub** that logs at WARN level and swallows calls — must not crash the voice path during early dev.
- One method per route. Use the exact path and method from the dashboard README. Each method returns the parsed JSON `data` field on success and raises a typed exception on non-2xx.
- 5s timeout per request. 2 retries with exponential backoff on `httpx.TimeoutException` only.
- Helper `redact(text: str) -> str` that strips:
  - phone numbers (`\+?\d{1,2}[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}`)
  - emails (standard regex)
  - SSN-shaped strings (`\d{3}-?\d{2}-?\d{4}`)
  - Replace each with `<REDACTED>`. Apply ONLY to fields going to `transcript_redacted`, not to medical notes.

Methods to implement (one per dashboard route):

```
async def get_call_queue() -> list[dict]
async def get_patient_profile(patient_id) -> dict
async def get_patient_billing(patient_id) -> list[dict]
async def get_patient_appointments(patient_id) -> list[dict]
async def get_patient_prescriptions(patient_id) -> list[dict]
async def start_call(patient_id, *, call_sid=None, direction="outbound", language="en", existing_call_id=None) -> dict   # returns the call row; use ['id']
async def update_call(call_id, **fields) -> dict          # current_node, status, ended_at, transcript_redacted, summary
async def post_recovery(patient_id, call_id, **fields) -> dict
async def post_newborn(patient_id, call_id, newborn_id, **fields) -> dict
async def post_phq(patient_id, call_id, instrument, score, **fields) -> dict
async def post_adherence(patient_id, call_id, **fields) -> dict
async def post_csat(patient_id, call_id, rating, qualitative_summary=None) -> dict
async def post_feedback(patient_id, category, note, *, call_id=None, sentiment="neutral") -> dict
async def post_escalation(patient_id, severity, category, trigger_text, *, call_id=None, trigger_phrase=None, transcript_excerpt=None) -> dict
async def start_eval(persona, *, flow_name="postpartum_v1") -> dict
async def post_eval_result(eval_run_id, criterion, passed, score=None, details=None) -> dict
async def finish_eval(eval_run_id, *, overall_score=None, transcript=None) -> dict
```

Field names must match the zod schemas at `~/Documents/GitHub/timbre_dashboard/src/app/api/v1/.../route.ts`. **Read those route files** — they are the source of truth.

### 4. Build `src/flows/__init__.py` and `src/flows/postpartum.py`

`src/flows/postpartum.py` exports a `def build_postpartum_flow(patient: dict, newborn: dict | None, client: DashboardClient, call_id: str) -> tuple[FlowConfig, dict]` returning the flow config and the initial state dict.

The graph (each is a `NodeConfig`):

```
identity_verify
  ├─[proxy detected]──► proxy_reject_reschedule ──► END
  └─[verified]──► mother_recovery
                      │  edge: if bleeding ∈ {heavy, concerning} OR pain_score ≥ 8
                      │       OR mentions fever/severe-headache/chest-pain/leg-pain
                      │       → call escalate_to_nurse global → escalation_handoff
                      ▼
                  mental_health_phq2
                      │  edge: if PHQ-2 ≥ 3 → phq9_full
                      ▼                          │  edge: if Q9 > 0 OR mentions self-harm → escalate_crisis
                  newborn_health  ◄──────────────┘
                      │  edge: if newborn fever / <6 wet diapers / lethargy / blue lips
                      │       → escalate_pediatric → escalation_handoff
                      │  edge: if feeding issue → lactation_support → medication_adherence
                      ▼
                  medication_adherence
                      │  edge: if barrier ∈ {cost, transport, no_pharmacy} → pharmacy_routing → social_screen
                      ▼
                  social_screen   (food security, postpartum support, IPV)
                      │  edge: if IPV in danger → escalate_crisis → escalation_handoff
                      ▼
                  doula_handoff
                      ▼
                  csat_collection
                      ▼  edge: feedback offered (capture_feedback global may fire)
                  END
```

Per-node spec (each entry needs: prompt key, fields captured, transition rule):

| Node | Prompt key (in prompts.json) | Captures via POST | Transition |
|---|---|---|---|
| `identity_verify` | `postpartum_identity_verify_en` / `_es` | nothing | `transition_to=mother_recovery` if confirmed; `proxy_reject_reschedule` if not |
| `proxy_reject_reschedule` | `postpartum_proxy_reject_en` / `_es` | `post_feedback(category="scheduling", note="proxy answered — rescheduled")` | END |
| `mother_recovery` | `postpartum_recovery_en` / `_es` | `post_recovery(bleeding, pain_score, incision_status, mobility_status, urination_status, emotional_state)` | next: `mental_health_phq2`. If red flag → global `escalate_to_nurse`. |
| `mental_health_phq2` | `postpartum_phq2_en` / `_es` | `post_phq(instrument="phq2", score, responses={q1,q2})` | next: `newborn_health` if score < 3 else `phq9_full` |
| `phq9_full` | `postpartum_phq9_en` / `_es` | `post_phq(instrument="phq9", score, responses, suicidal_ideation)` | next: `newborn_health`. If `suicidal_ideation==True` → global `escalate_crisis` |
| `newborn_health` | `postpartum_newborn_en` / `_es` | `post_newborn(newborn_id, feeding_count_24h, wet_diapers_24h, dirty_diapers_24h, jaundice_observed, fever, fever_temp_f, sleep_pattern)` | next: `medication_adherence`. Red flag → `escalate_pediatric`. Feeding issue → `lactation_support` |
| `lactation_support` | `postpartum_lactation_en` / `_es` | `post_feedback(category="clinical", note=...)` | next: `medication_adherence` |
| `medication_adherence` | `postpartum_meds_en` / `_es` | one `post_adherence` call per active prescription (use `get_patient_prescriptions` at node entry) | next: `social_screen`. Barrier → `pharmacy_routing` |
| `pharmacy_routing` | `postpartum_pharmacy_en` / `_es` | `post_feedback(category="billing" if barrier=="cost" else "scheduling", note=...)` | next: `social_screen` |
| `social_screen` | `postpartum_social_en` / `_es` | `post_feedback(category=...)` per concern | next: `doula_handoff`. IPV active → `escalate_crisis` |
| `doula_handoff` | `postpartum_doula_en` / `_es` | nothing (already shows scheduled visits from `get_patient_appointments`) | next: `csat_collection` |
| `csat_collection` | `postpartum_csat_en` / `_es` | `post_csat(rating, qualitative_summary)`; optionally a final `post_feedback` if open-ended given | next: END |
| `escalation_handoff` | `postpartum_escalation_handoff_en` / `_es` | nothing (the escalation was already POSTed by the global fn) | END |

### 5. Global functions

Defined once on the `FlowManager` so they fire from any node. Each accepts the LLM's structured args, calls `dashboard_client`, and returns a short spoken summary. Schemas:

```python
escalate_to_nurse(severity: "urgent"|"warning", trigger_phrase: str, trigger_text: str)
  → POST /api/v1/escalations (category="maternal", call_id, patient_id)
  → transition_to="escalation_handoff"

escalate_pediatric(severity, trigger_phrase, trigger_text)
  → POST /api/v1/escalations (category="pediatric")
  → transition_to="escalation_handoff"

escalate_crisis(trigger_phrase, trigger_text)
  → POST /api/v1/escalations (severity="urgent", category="crisis")
  → transition_to="escalation_handoff"

lookup_patient_billing(question: str)
  → GET /api/v1/patients/{patient_id}/billing
  → Returns a 1-2 sentence spoken summary of the most relevant bill (status + amount + processing_notes).
  → DOES NOT transition. The LLM continues the current node after answering.

lookup_appointment_history(time_window: "past"|"upcoming"|"all" = "upcoming")
  → GET /api/v1/patients/{patient_id}/appointments
  → 1-sentence summary. No transition.

lookup_prescription_status(medication_hint: str | None)
  → GET /api/v1/patients/{patient_id}/prescriptions
  → 1-sentence summary. No transition.

capture_feedback(category: "clinical"|"billing"|"scheduling"|"facilities"|"staff"|"communication"|"other", note: str, sentiment: "positive"|"neutral"|"negative" = "neutral")
  → POST /api/v1/patients/{patient_id}/feedback
  → 1-sentence "thanks for sharing that". No transition.
```

The agent calls these by issuing function calls with the right schemas. The LLM model is told (in the system message) when each is appropriate.

### 6. Prompts

For every node and every global function, add a key to `prompts/prompts.json`. Naming: `postpartum_<node>_<lang>` and `postpartum_global_<fn>` (language-agnostic system instructions for global tools). Style:

- 1-3 short sentences. Spoken aloud over phone. No markdown, no emoji.
- Explain the node's *one job* in plain English (or Spanish for `_es`).
- Tell the LLM what to ask, in what order, and when to call which function.
- For nodes with edge functions, include the rule explicitly ("if she says any of: heavy bleeding, fever above 100.4, severe headache → call escalate_to_nurse").
- Spanish prompts: write them natively, not translated phrase-by-phrase. "How are you feeling?" is "¿Cómo te sientes?" not "¿Cómo estás sintiendo?".

### 7. Build `src/postpartum_bot.py`

Modeled on `twilio_bot.py` but with these differences:

- At `/twiml`, accept a `?patient_id=...` query param (or echo from a `<Parameter>`). Default to the first `queued` row from `get_call_queue()` if unset (handy for sim/test).
- At `/ws` start:
  1. Call `dashboard_client.get_patient_profile(patient_id)` → grab `patient`, `newborns[0]`, `prescriptions`, etc.
  2. Call `dashboard_client.start_call(patient_id, call_sid=..., language=patient.language)` → capture `call_id`.
  3. Build the FlowConfig via `build_postpartum_flow(patient, newborn, dashboard_client, call_id)`.
  4. Construct a `FlowManager` with: the pipeline's LLM service, the FlowConfig, the global functions list.
  5. Build the Pipeline as in `twilio_bot.py` but place the FlowManager's frame processor where the GoodbyeProcessor was. (Pipecat Flows owns turn-by-turn LLM context now.)
  6. Speak the seeded greeting via `TTSSpeakFrame(greeting, append_to_context=False)` exactly like `twilio_bot.py` does.
- On every node transition, the FlowManager fires a callback → PATCH `/api/v1/calls/{id}` with `current_node`.
- On disconnect, PATCH the call to `status="completed"`, `ended_at=now()`, and `transcript_redacted=...`.
- Keep the `MAX_CALL_SECS` backstop at **900s**.
- Reuse `build_turn_analyzer()` for endpointing.

### 8. Verify with the simulator

```bash
.venv/bin/python scripts/sim_twilio_ws.py --bot postpartum --patient-id 11111111-1111-1111-1111-111111111111
```

If `sim_twilio_ws.py` doesn't support a `--bot` flag yet, add one (small change). Walk the simulator through canned responses for each node and verify:

- `current_node` PATCH lands on `/api/v1/calls/{id}` for each transition.
- Each node's POST lands (e.g. `/api/v1/patients/.../recovery` after `mother_recovery`).
- Triggering "I have a fever of 101" while in `mother_recovery` fires `escalate_to_nurse` and transitions to `escalation_handoff`.
- Triggering "where is my bill?" while in `mother_recovery` fires `lookup_patient_billing`, answers, and stays in `mother_recovery`.

### 9. (Optional, only if Twilio is reachable) live smoke test

Dial yourself once with a small REST dialer (e.g. adapt `deploy/dialout_test.py`) pointed at the postpartum webhook — place a Twilio outbound call whose `/twiml` URL is the running `postpartum_bot:app`.

### 10. Report back

Reply with:
- Files added/modified (with line counts).
- `pip freeze | grep -i flows` showing the version.
- Output of the simulator dry-run (or a paste of the relevant log lines).
- Any rough edges or questions for Kanika.

---

## Constraints (hard rules)

- **Never touch** `src/twilio_bot.py`, `src/m0_local_bot.py`, `src/turn_helpers.py`. `twilio_bot.py` is the inbound voice companion and must keep working; `turn_helpers.py` is shared endpointing.
- **Never commit `.env`** or any file containing API keys.
- **Never** send raw transcript text to the dashboard's `transcript_redacted` field without running `redact()` over it.
- **Never** ask the LLM to invent medical facts. PHQ-2 must use the standard 2 questions verbatim. Red-flag lists must come from the prompts (treated as policy), not the model.
- **Never** keep the call open longer than `MAX_CALL_SECS`. Pipecat Flow stalls do happen; the backstop is the safety net.
- **Never** call a dashboard endpoint without the bearer token header.
- **Synthetic patients only.** All ten patient UUIDs are listed in `~/Documents/GitHub/timbre_dashboard/supabase/seed.sql` — use those, never invent real names.

---

## Out of scope

- HIPAA production hardening (BAA, KMS, audit retention, key rotation). Kanika's separate `docs/hipaa-production-path.md` work.
- **Cekura MCP wiring + persona files + eval-running.** See the boundary table at the top — Cekura lives outside this repo. Hand-offs to Cekura happen via the dashboard's `/api/v1/evals` routes, which Cekura's own runner calls; no code in `timbre_new/` triggers an eval.
- Self-hosting NVIDIA Nemotron, Parakeet, Magpie (W5 roadmap item).
- Replacing or modifying the inbound voice companion (`twilio_bot.py`).
- Frontend / dashboard edits.
- Vercel deploy.

---

## Quick reference

**Dashboard API contract:** `~/Documents/GitHub/timbre_dashboard/README.md` (table near the bottom).

**Payload field names:** `~/Documents/GitHub/timbre_dashboard/src/app/api/v1/<route>/route.ts` — each file has the zod schema defining exact field names.

**Pipecat Flows docs:** https://docs.pipecat.ai/server/utilities/flows/pipecat-flows  (and the README at https://github.com/pipecat-ai/pipecat-flows).

**Twilio trial limitation:** Only dials the `TARGET_PHONE_NUMBER` in `.env`. For multi-patient demo, upgrade Twilio first (Kanika's call).
