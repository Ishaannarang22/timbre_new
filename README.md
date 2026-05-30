# timbre

Voice agents that run over a Twilio phone call. Two bots share the pipeline:

- **`twilio_bot.py`** — the original morning-quote companion (Ishaan's 7 AM call).
- **`postpartum_bot.py`** — a postpartum maternal check-in agent driven by [Pipecat Flows](https://github.com/pipecat-ai/pipecat-flows). Walks a structured clinical conversation and POSTs answers to the timbre_dashboard `/api/v1/*` routes in real time.

Both bots share the same audio stack: Twilio Media Streams ↔ Deepgram STT ↔ NVIDIA Nemotron LLM ↔ Cartesia TTS, with `PatientSmartTurnV3` prosody endpointing.

See `CLAUDE.md` for the project's broader direction and `docs/` for architecture / roadmap / setup.

---

## Postpartum flow diagram

```
                         ┌──────────────────────────┐
   /twiml mints token, ──▶│       identity_verify     │
   pulls patient_id        └──────────────┬───────────┘
   from queue                              │ verified
                                           │
                       proxy detected ◀────┤
                              │            │
                              ▼            │
                ┌─────────────────────┐    │
                │ proxy_reject_       │    │
                │  reschedule          │    │
                └──────────┬───────────┘    │
                           ▼                 ▼
                          END    ┌──────────────────────────┐
                                  │      mother_recovery     │──► escalate_to_nurse (red flag)
                                  └──────────────┬───────────┘         │
                                                 │                       ▼
                                                 ▼              ┌─────────────────────┐
                                  ┌──────────────────────────┐  │ escalation_handoff  │──► END
                                  │   mental_health_phq2     │  └─────────────────────┘
                                  └──────────────┬───────────┘
                                                 │ score >= 3?
                                                 │
                                  ┌──────yes─────┴──────no──────┐
                                  ▼                             │
                       ┌──────────────────────┐                 │
                       │     phq9_full         │── Q9>0 ───────► escalate_crisis
                       └──────────┬───────────┘                 │
                                  │                             │
                                  ▼                             ▼
                                  └──────► ┌──────────────────────────┐
                                            │     newborn_health       │──► escalate_pediatric
                                            └──────────────┬───────────┘    (red flag)
                                                           │
                                              feeding issue │
                                              (no red flag) │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │   lactation_support       │
                                            └──────────────┬───────────┘
                                                           │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │   medication_adherence   │
                                            │  (one entry per Rx)       │
                                            └──────────────┬───────────┘
                                                           │
                                              barrier ∈ { cost,
                                              transport,
                                              no_pharmacy }
                                                           │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │   pharmacy_routing        │
                                            └──────────────┬───────────┘
                                                           │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │      social_screen        │──► escalate_crisis
                                            └──────────────┬───────────┘   (IPV active danger)
                                                           │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │      doula_handoff        │
                                            └──────────────┬───────────┘
                                                           │
                                                           ▼
                                            ┌──────────────────────────┐
                                            │     csat_collection       │
                                            └──────────────┬───────────┘
                                                           ▼
                                                          END
```

### Global functions (available at every node)

These are registered once on the `FlowManager` and can fire from any node.

| Function | Effect | Transitions? |
|---|---|---|
| `escalate_to_nurse` | POST `/escalations` (category=maternal) | → `escalation_handoff` |
| `escalate_pediatric` | POST `/escalations` (category=pediatric) | → `escalation_handoff` |
| `escalate_crisis` | POST `/escalations` (category=crisis, severity=urgent) | → `escalation_handoff` |
| `lookup_patient_billing` | GET `/patients/:id/billing`, 1-sentence answer | No — resumes current node |
| `lookup_appointment_history` | GET `/patients/:id/appointments` | No |
| `lookup_prescription_status` | GET `/patients/:id/prescriptions` | No |
| `capture_feedback` | POST `/patients/:id/feedback` | No |

### What each node POSTs to the dashboard

| Node | Endpoint(s) hit | Dashboard table |
|---|---|---|
| `identity_verify` | (none on success) | – |
| `proxy_reject_reschedule` | POST `/patients/:id/feedback` (scheduling) | `feedback` |
| `mother_recovery` | POST `/patients/:id/recovery` | `recovery_answer` |
| `mental_health_phq2` | POST `/patients/:id/phq` (`instrument=phq2`) | `phq_score` |
| `phq9_full` | POST `/patients/:id/phq` (`instrument=phq9`), POST `/escalations` if suicidal | `phq_score`, `escalation` |
| `newborn_health` | POST `/patients/:id/newborn` | `newborn_answer` |
| `lactation_support` | POST `/patients/:id/feedback` (clinical) | `feedback` |
| `medication_adherence` | POST `/patients/:id/adherence` per Rx | `adherence` |
| `pharmacy_routing` | POST `/patients/:id/feedback` (billing / scheduling) | `feedback` |
| `social_screen` | POST `/patients/:id/feedback` (food/support), POST `/escalations` if IPV danger | `feedback`, `escalation` |
| `doula_handoff` | (none) | – |
| `csat_collection` | POST `/patients/:id/csat` (+ optional feedback) | `csat`, `feedback` |
| `escalation_handoff` | (none — escalation already POSTed) | – |
| every transition | PATCH `/calls/:id` `current_node=…` | `call` |
| call close | PATCH `/calls/:id` `status=completed`, `transcript_redacted`, `ended_at` | `call` |

All transcript text written to `transcript_redacted` goes through `dashboard_client.redact()` first (phones, emails, SSNs masked).

---

## Repo layout

```
src/
├── twilio_bot.py         # morning-quote bot (do not modify)
├── postpartum_bot.py     # postpartum flow bot — FastAPI app on /twiml + /ws
├── flows/
│   ├── __init__.py
│   └── postpartum.py     # NodeConfig graph + global functions
├── dashboard_client.py   # async httpx client over /api/v1/* (no-op stub if env unset)
├── prompts.py            # tiny JSON loader
├── turn_helpers.py       # PatientSmartTurnV3 prosody endpointer
├── m0_local_bot.py       # local-mic dev variant
├── run_morning_call.py   # 7 AM outbound dialer (for twilio_bot)
└── call_me.py            # generate_quote() helper

prompts/prompts.json      # one entry per node, EN + ES, + per-global instructions
scripts/sim_twilio_ws.py  # offline Twilio Media Streams simulator; --bot {twilio,postpartum}
```

---

## Quickstart

```bash
# Install deps (Python 3.11+; macOS needs portaudio: `brew install portaudio`)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Fill .env (NVIDIA, Deepgram, Cartesia, Twilio, DASHBOARD_API_URL, DASHBOARD_API_TOKEN).

# Smoke-test the postpartum bot end-to-end without placing a real phone call:
.venv/bin/python scripts/sim_twilio_ws.py \
    --bot postpartum \
    --patient-id 11111111-1111-1111-1111-111111111111
```

Successful output:
```
[PASS] ws handshake (ws://127.0.0.1:8080/ws)
[PASS] greeting audio frames: ...
[PASS] clean teardown
```

---

## Dashboard wiring

`postpartum_bot.py` reads `DASHBOARD_API_URL` + `DASHBOARD_API_TOKEN` from `.env`. If either is unset or the dashboard is unreachable, the agent still takes the call — every dashboard write is best-effort and logs a warning.

Full API contract for the receiving end lives in `~/Documents/GitHub/timbre_dashboard/README.md`.
