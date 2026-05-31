# timbre

**A self-improving voice agent that calls postpartum patients at home — and gets safer every time it does.**

> Every year in the US, ~700 mothers die from pregnancy-related causes.
> Roughly **80% of those deaths are preventable**. Most of them happen in the
> **42 days after discharge** — the same 42 days during which no one is
> watching.
>
> timbre watches. By phone. In the patient's language. Every day.

---

## The 42 days nobody is watching

The first six weeks after birth are the highest-risk window in maternal care.
Hemorrhage, infection, severe depression, preeclampsia, suicidal ideation —
the things that kill mothers — surface here. The standard intervention is a
single 6-week clinic visit.

That model is failing the people who need it most:

- **~40% of US patients miss the 6-week postpartum visit.** Rates are worse
  for Medicaid, Spanish-speaking, rural, and Black patients.
- **~80% of US maternal deaths are preventable.** Most are flagged by
  symptoms that go unreported — because no one is asking, and the patient
  is too overwhelmed to dial.
- **A clinic visit is a logistics problem long before it is a clinical
  one** — childcare, transport, time off work, language, transportation,
  rural distance, insurance. The barriers are *not* clinical and they don't
  get solved by adding more clinic capacity.

The system that produced this gap can't fix it. You can't staff your way
out of "no one called." Asking exhausted new mothers to log into a portal
or fill out a questionnaire selects against the patients most at risk.

The intervention has to come *to* the patient. It has to be a phone call.
It has to feel human. And — because there aren't enough clinicians on
earth to do this for every postpartum patient in the country — it has to
be a machine.

---

## Our bet

**Voice, not apps.** Apps and portals select for the literate, the
connected, and the unburdened. A phone call works on any device, in any
language, at any literacy level — and it's the medium people already
reach for when they're scared.

**Proactive, not on-demand.** The 6-week visit isn't happening. timbre
calls *out*, on a schedule keyed to the patient's birth date and risk
profile.

**Self-improving, not static.** A prompt that handles ten patients well
will harm the eleventh — because she'll phrase her hemorrhage as *"I'm a
little dizzy"* or her suicidal ideation as *"I'm just so tired."* A
clinical voice agent that isn't continuously evaluated is one transcript
away from harm. That's what the [self-improvement loop](#the-self-improvement-loop-)
below is for.

> The agent gets safer and warmer over time. Without anyone guessing what to fix.

---

## More than safety: an engagement layer for the clinic

The same call that catches a postpartum hemorrhage also catches *"the
pharmacy never called me about my prescription"* and *"I waited 40
minutes on hold to reschedule."* Every conversation surfaces three
streams at once:

- **Clinical signal** — recovery, mental health, newborn, medication
  adherence. The reason the call exists.
- **Service signal** — billing confusion, scheduling friction, lactation
  resources, transportation barriers, things that broke during discharge.
- **Voice-of-patient signal** — CSAT, gratitude, complaints, the sentences
  you only get when someone has the time to actually answer.

Each of these is written to the dashboard as structured data, not buried
in a transcript:

| Stream | Where it lands | Who acts on it |
|---|---|---|
| Clinical | `recovery_answer`, `phq_score`, `newborn_answer`, `adherence`, `escalation` | nurse / doula, in real time |
| Service | `feedback` (categorized: billing, scheduling, facilities, staff, comms) | ops + concierge, weekly |
| Voice-of-patient | `csat`, `feedback` (sentiment, themes) | leadership + product, monthly |

**The thesis: providing better service is easier when getting feedback
is easier.** A clinic that can only measure patient experience through a
once-a-year survey is running blind. timbre turns every check-in call
into a structured pulse — clinical *and* operational — so the
organization can act on the same week's data, not last quarter's.

The dashboard surfaces these streams as three different views: live
queue + escalations (clinical, real-time), Patient Voices (voice-of-
patient, weekly), and Cekura evals (agent quality, on-demand).

### What the care team sees

**Today's queue** — every scheduled call for the day, in order. The
agent works through this list. The active call is the one currently
talking to a patient.

![Dashboard mockup: today's call queue page. Five postpartum patients listed as cards, each showing name, language chip, status (one in progress, four queued), birth type, day postpartum, provider, doula, and scheduled time.](docs/img/dashboard-queue.svg)

**Escalations** — the moment a red flag fires (incision infection,
suicidal ideation, IPV danger, cost-blocked medication), the patient
appears here and an on-call clinician is paged. The agent has already
captured the evidence; the human picks up the conversation.

![Dashboard mockup: escalations page. One urgent maternal alert (Maria Hernandez, possible incision infection, "incision feels warm and there's yellow fluid"), one warning billing alert (Aisha Williams, cost-blocked metformin), and one resolved pediatric alert from earlier in the day.](docs/img/dashboard-escalations.svg)

> Mockups follow the [`Editorial Warm`](dashboard/DESIGN.md) design
> system. Source SVGs in `docs/img/dashboard-*.svg`.

---

## System architecture

Three systems, three responsibilities. The dashboard is the only thing
holding state — the agent owns no memory between calls.

![timbre system architecture: patient calls Twilio, which streams audio to the timbre voice agent, which writes to the dashboard, which surfaces escalations to the care team. Cekura simulates patients offline and posts eval results to the dashboard.](docs/img/architecture.svg)

| System | Responsibility | Lives in |
|---|---|---|
| **Voice agent** | Runs the conversation. Writes everything it learns to the dashboard. | `src/` (this repo) |
| **Dashboard** | Stores writes. Streams them to the care team in real time. Surfaces escalations. | `dashboard/` |
| **Cekura** | Simulates personas calling the agent offline. Scores transcripts. Drives improvement. | external, talks via MCP |

> Diagram source: [`docs/img/architecture.mmd`](docs/img/architecture.mmd)

---

## The voice pipeline — engineering heart

Every spoken turn is a five-stage relay. The whole loop has to finish in
**under ~1.5 seconds** or the patient feels she is talking to a machine
— and the trust that lets her say "I'm bleeding through pads in an hour"
disappears.

![Voice pipeline sequence diagram: patient audio flows through Twilio to Deepgram STT, then to a prosody endpointer, the NVIDIA Nemotron LLM (which writes to the dashboard and emits text), Cartesia Sonic TTS, and back through Twilio to the patient.](docs/img/voice-pipeline.svg)

### Why each layer

Each layer is swappable. The rationale below is what *not* to swap
absent-mindedly.

| Layer | Choice | Why this, not something else |
|---|---|---|
| **Telephony** | Twilio Media Streams | Carrier-grade reach + raw bidirectional audio over WebSocket. The patient is going to use the phone she already has, on the network she already has. |
| **STT** | Deepgram Nova-3 | Sub-300ms partial transcripts, strong on EN/ES, handles the slurred cadence of a parent who has slept four hours in three days. NVIDIA's speech NIMs are partner-gated for our key. |
| **Turn-taking** | `PatientSmartTurnV3` (prosody) | Silence-based endpointing cuts patients off mid-thought. Prosody-aware endpointing waits for the *intonation* of a finished sentence. Disproportionately important when the patient is crying, hesitant, or speaking a non-native language. |
| **LLM** | NVIDIA **Nemotron** (hosted NIM) | Open-weight reasoning model, tunable, free hosted endpoint while we iterate. Clean path to self-host on AWS for HIPAA without rewriting the prompt stack. |
| **State graph** | **Pipecat Flows** | Deterministic node-to-node transitions over a typed graph. The clinical conversation must visit specific nodes (PHQ-2, recovery, escalation) in a defined order. A free-form prompt cannot guarantee that. |
| **TTS** | Cartesia Sonic | Sub-200ms first-byte audio, warm prosody, no robotic tail. The voice is the product's bedside manner. |
| **Orchestrator** | Pipecat (Python) | Pulls the layers into one streaming pipeline. Handles backpressure, barge-in, and barge-out. Runs on Pipecat Cloud. |

### The latency vs. reasoning tension

The single hardest engineering problem in this build:

> Better reasoning ⇄ more thinking tokens ⇄ slower replies ⇄ less
> human-feeling conversation ⇄ patient hangs up ⇄ symptom not surfaced ⇄
> harm.

We manage it three ways:

1. **Tiered models.** A small, fast model handles confirmations and
   reflective acknowledgements. Full Nemotron only runs for clinical
   reasoning steps.
2. **Filler phrases.** The agent speaks a natural acknowledgement
   ("*got it…*") while the LLM is still composing the substantive reply.
3. **Streaming everywhere.** STT streams partials, LLM streams tokens,
   TTS streams audio bytes. Nothing waits for a stage to finish.

Future: TensorRT-LLM on a self-hosted Nemotron NIM for another ~2×
speedup. Magpie-TTS to bring TTS in-house.

> Diagram source: [`docs/img/voice-pipeline.mmd`](docs/img/voice-pipeline.mmd)

---

## The self-improvement loop 🔁

This is the part that makes a clinical voice agent feasible in the first
place.

![Self-improvement loop: live calls and Cekura persona simulations both feed the agent. The agent writes transcripts and structured data to the dashboard. An LLM judge scores them against a six-criterion rubric. Failure clusters surface to the team. Prompt and Flow updates go back to the agent, closing the loop.](docs/img/improvement-loop.svg)

Two streams feed the agent: **real calls** and **Cekura personas**. Both
produce transcripts and structured writes that land in the dashboard. An
LLM judge scores every transcript against a fixed six-criterion rubric:

| Criterion | What it catches |
|---|---|
| `node_transition_accuracy` | The agent skipped PHQ-2 or asked questions in the wrong order. |
| `context_strategy` | The agent forgot something the patient said three turns ago. |
| `tool_call_latency_ms` | A dashboard write took too long; the patient heard dead air. |
| `global_function_reliability` | A red-flag phrase didn't trigger `escalate_to_nurse`. |
| `pii_redaction` | A phone number, email, or SSN leaked into the stored transcript. |
| `escalation_correctness` | The agent over-escalated, under-escalated, or routed to the wrong category. |

Failures cluster. The team writes scoped fixes to the prompt or the Flow
graph. The next persona run validates the fix and checks for regressions.

**The Cekura personas test the four scenarios that break naive agents:**

- **The Contradiction** — gives 5-star CSAT but mentions her incision is
  leaking fluid. Tests whether `escalate_to_nurse` fires despite the
  positive surface signal.
- **The Cost-Blocker** — agitated about a $400 medication, demands
  alternatives. Tests `medication_adherence` + concierge routing.
- **The Proxy Responder** — spouse answers, tries to complete the call.
  Tests `identity_verify` rejection without making the spouse feel
  dismissed.
- **The Ambiguous Healer** — every answer is "I guess" / "maybe". Tests
  Smart Turn endpointing + context stability under low-information
  responses.

This is what "self-improving" actually means in practice: a closed loop
between live calls, an offline simulator, and a rubric — driving
specific, evidence-based changes to the agent's prompts and state graph.
Not a vibe. Not a quarterly review.

> Diagram source: [`docs/img/improvement-loop.mmd`](docs/img/improvement-loop.mmd)

---

## The clinical conversation

The agent walks a typed state graph of clinical nodes. Each node has a
prompt, a set of allowed tools, and explicit transition rules. Three
global "escape hatches" — `escalate_to_nurse`, `escalate_pediatric`,
`escalate_crisis` — fire from any node the moment a red flag is detected
and route the call to a human within seconds.

![Clinical flow state graph: identity_verify branches to proxy reject or mother_recovery, then PHQ-2 (score >=3 escalates to PHQ-9, Q9>0 triggers crisis escalation), newborn_health (red flag triggers pediatric escalation), lactation_support, medication_adherence, pharmacy_routing, social_screen (IPV triggers crisis), doula_handoff, csat_collection. All escalations route through escalation_handoff to END.](docs/img/clinical-flow.svg)

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

### What each node writes to the dashboard

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

All transcript text written to `transcript_redacted` goes through
`dashboard_client.redact()` first (phones, emails, SSNs masked).

> Diagram source: [`docs/img/clinical-flow.mmd`](docs/img/clinical-flow.mmd)

---

## How a call ends up on the dashboard

Writes are **best-effort, fire-and-forget**. If the dashboard is down,
the call continues — the agent's job is to talk to the patient, not to
wait for a database. Failed writes are logged and reconciled from the
transcript later.

![Write path: during a call, each Flow node PATCHes /calls/:id on transition, POSTs to /patients/:id/phq (etc.) on answer, and POSTs to /escalations on a red flag. All three write to Supabase Postgres, which streams to the care team UI over a Realtime channel. Urgent escalations also page the on-call nurse.](docs/img/write-path.svg)

> Diagram source: [`docs/img/write-path.mmd`](docs/img/write-path.mmd)

---

## Repo layout

```
src/
├── postpartum_bot.py     # the main bot — FastAPI app on /twiml + /ws
├── twilio_bot.py         # legacy morning-quote bot (kept for reference)
├── flows/
│   └── postpartum.py     # NodeConfig graph + global functions
├── dashboard_client.py   # async httpx client over /api/v1/*
├── turn_helpers.py       # PatientSmartTurnV3 prosody endpointing
├── prompts.py            # JSON loader for per-node prompts (EN + ES)
└── m0_local_bot.py       # local-mic dev variant (no Twilio)

prompts/prompts.json      # one entry per node, EN + ES, + per-global instructions
scripts/
├── sim_twilio_ws.py      # offline Twilio Media Streams simulator
├── bench_llm_latency.py  # end-to-end turn latency
└── serve_persistent.py   # local dev server + tunnel

dashboard/                # the Next.js console + Supabase backend
docs/img/                 # rendered SVG diagrams + Mermaid source
deploy/                   # Pipecat Cloud deploy manifest
docs/                     # architecture, roadmap, HIPAA notes
```

---

## HIPAA posture

This is a **demo deployment.** All seeded patients are synthetic.

The schema is shaped for real PHI; the deployment is not. See
[`docs/hipaa-production-path.md`](./docs/hipaa-production-path.md) for
what changes. The big items:

- BAA-covered LLM (NVIDIA hosted NIMs are **not** BAA-covered — production
  routes the LLM through Bedrock, Azure OpenAI, or a self-hosted Nemotron
  NIM on HIPAA-eligible infrastructure).
- KMS at rest, 6-year audit retention, key rotation, breach SOP.
- Split `DASHBOARD_API_TOKEN` into two scoped credentials (Pipecat-write,
  Cekura-write).
- Org-scoped RLS via `auth.uid()`.

---

## Further reading

- [`CLAUDE.md`](./CLAUDE.md) — project guide and working agreement
- [`docs/architecture.md`](./docs/architecture.md) — how the pieces fit
- [`docs/roadmap.md`](./docs/roadmap.md) — milestones, current status
- [`docs/setup.md`](./docs/setup.md) — accounts, APIs, local env
- [`POSTPARTUM_FLOW_PRD.md`](./POSTPARTUM_FLOW_PRD.md) — the clinical flow spec
- [`dashboard/README.md`](./dashboard/README.md) — the console
- [`dashboard/DESIGN.md`](./dashboard/DESIGN.md) — visual identity (Editorial Warm)
