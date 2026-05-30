# timbre dashboard

The clinical console for the timbre postpartum voice agent. Nurses and doulas use it to see who's been called, what was said, and who needs help.

- **Stack:** Next.js 16 (app router) · Tailwind 4 · Supabase (Postgres + Realtime) · Vercel
- **Design:** see [`DESIGN.md`](./DESIGN.md) — "Editorial Warm" (Fraunces + Inter on cream, terracotta accent)

## How it fits together

There are three systems. The dashboard is the one in the middle — it holds the data, but doesn't *do* anything on its own.

```
┌────────────────────┐   writes patient answers   ┌──────────────────┐   writes eval results   ┌─────────┐
│ Pipecat            │ ─────────────────────────► │ this dashboard   │ ◄───────────────────── │ Cekura  │
│ (the voice agent)  │      via /api/v1/...       │ (Next.js + DB)   │     via /api/v1/evals  │ (evals) │
└────────────────────┘                            └──────────────────┘                         └─────────┘
        live calls                                    read + render                              offline
```

- **Pipecat** (`~/Documents/GitHub/timbre_new`) runs the real conversation. During a call it POSTs everything it learns into our API routes.
- **This dashboard** stores those writes, streams them to the UI over Supabase Realtime, and renders the queue, transcripts, and escalations.
- **Cekura** (api.cekura.ai, via MCP) simulates fake patients calling the agent *offline*, then POSTs scores into our `/evals` routes. It never runs during real calls.

The dashboard exposes a typed API surface. Both systems write into it. That's the entire contract.

## Quick start

```bash
cp .env.local.example .env.local   # fill in Supabase keys + an API token (see below)
npm install
npm run dev                        # http://localhost:3000
```

You should see today's queued postpartum calls. If the page is empty, the seed didn't run — see step 1 below.

## Full setup (~10 min)

### 1. Supabase project

Either:

- **Automated:** Hand `supabase/CREATE_TABLES_PRD.md` to a Claude Code session with the Supabase MCP enabled. It'll create the project, run both SQL files, and hand back keys.
- **Manual:**
  1. supabase.com → new project (`timbre`, us-east-1, free tier)
  2. SQL Editor → paste `supabase/schema.sql` → run
  3. SQL Editor → paste `supabase/seed.sql` → run (idempotent; 10 synthetic patients)
  4. Settings → API → copy the **Project URL**, **anon public** key, **service_role** key

### 2. Local env

Fill `.env.local`:

| Variable | Where it comes from |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | same page, **anon public** |
| `SUPABASE_SERVICE_ROLE_KEY` | same page, **service_role** (server-only, never expose) |
| `DASHBOARD_API_TOKEN` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |

### 3. Wire the Pipecat agent

In `~/Documents/GitHub/timbre_new/.env`:

```
DASHBOARD_API_URL=http://localhost:3000    # swap to your Vercel URL after step 4
DASHBOARD_API_TOKEN=<same token as step 2>
```

The Pipecat side is built separately (`~/Documents/GitHub/timbre_new/POSTPARTUM_FLOW_PRD.md`). Nothing changes in this repo.

### 4. Deploy

```bash
npx vercel                # follow prompts
# Then vercel.com → Settings → Environment Variables
# → add all 4 vars to Production + Preview + Development → redeploy
```

When the URL is live, update `DASHBOARD_API_URL` in `timbre_new/.env` to point at it.

### 5. Wire Cekura — only when you want eval demos

Independent of steps 1–4. Pipecat + dashboard work without Cekura.

1. **Auth the Cekura MCP.** `claude mcp list | grep cekura` should show `! Needs authentication`. Invoke any `mcp__cekura__*` tool to trigger OAuth.
2. **Point Cekura at the agent.** In the Cekura console, create an agent target with the Twilio number from `timbre_new/.env`, the shared `DASHBOARD_API_TOKEN`, and the dashboard URL as the result sink.
3. **Load four personas** — already defined, but if not, see the [persona spec](#cekura-personas) below.
4. **Configure criteria** (matches the `eval_criterion` enum): `node_transition_accuracy`, `context_strategy`, `tool_call_latency_ms`, `global_function_reliability`, `pii_redaction`, `escalation_correctness`.
5. **Trigger a run** from the MCP. Watch `/evals` page populate in real time.

## Repo layout

```
src/
├── app/
│   ├── (dashboard)/          # the UI
│   │   ├── page.tsx          #   today's queue
│   │   ├── live/             #   active calls + escalations (realtime)
│   │   ├── escalations/      #   red-alert feed
│   │   ├── patients/         #   roster
│   │   ├── patient/[id]/     #   profile view
│   │   ├── feedback/         #   Patient Voices
│   │   └── evals/            #   Cekura results
│   ├── api/v1/               # the API surface Pipecat + Cekura POST to
│   └── layout.tsx
├── components/               # primitives + sidebar + page header
└── lib/                      # supabase clients, types, formatters, auth
supabase/
├── schema.sql                # paste first
├── seed.sql                  # paste second
└── CREATE_TABLES_PRD.md      # MCP hand-off
DESIGN.md                     # visual system (google-labs-code/design.md format)
```

## API contract

All routes require `Authorization: Bearer $DASHBOARD_API_TOKEN`.

**Pipecat writes during live calls:**

| Method | Path | Triggered by |
| --- | --- | --- |
| GET | `/api/v1/health` | startup |
| GET | `/api/v1/patients/call-queue` | dial-next on Pipecat Cloud |
| GET | `/api/v1/patients/[id]` | `lookup_patient_profile` |
| GET | `/api/v1/patients/[id]/billing` | "where is my bill?" |
| GET | `/api/v1/patients/[id]/appointments` | `lookup_appointment_history` |
| GET | `/api/v1/patients/[id]/prescriptions` | `lookup_prescription_status` |
| POST | `/api/v1/calls` | call start |
| PATCH | `/api/v1/calls/[id]` | every Flow node transition |
| POST | `/api/v1/patients/[id]/recovery` | `mother_recovery` |
| POST | `/api/v1/patients/[id]/newborn` | `newborn_health` |
| POST | `/api/v1/patients/[id]/phq` | `mental_health_phq2` / `phq9_full` |
| POST | `/api/v1/patients/[id]/adherence` | `medication_adherence` |
| POST | `/api/v1/patients/[id]/csat` | `csat_collection` |
| POST | `/api/v1/patients/[id]/feedback` | `capture_feedback` |
| POST | `/api/v1/escalations` | any `escalate_*` global |

**Cekura writes during eval runs (never during real calls):**

| Method | Path | Triggered by |
| --- | --- | --- |
| POST | `/api/v1/evals` | start of run |
| POST | `/api/v1/evals/[id]/results` | one row per criterion |
| PATCH | `/api/v1/evals/[id]` | finalize (`overall_score`, transcript) |

Pipecat must never call `/evals`; Cekura must never call the patient/call routes. The demo enforces this by convention only — same shared token, no per-system scopes. Production would split the token.

## Cekura personas

The four scenarios the agent should survive:

- **The Contradiction** — gives 5-star CSAT but mentions her incision is leaking fluid. Tests `escalate_to_nurse`.
- **The Cost-Blocker** — agitated about a $400 medication, demands alternatives. Tests `medication_adherence` + concierge routing.
- **The Proxy Responder** — spouse answers and tries to complete the call. Tests `identity_verify` rejection.
- **The Ambiguous Healer** — every answer is "I guess" / "maybe." Tests Smart Turn endpointing + context stability.

## HIPAA posture

**This is a demo.** Every patient in the seed is synthetic.

The schema is shaped for real PHI; the deployment isn't. To go to production, see `../timbre_new/docs/hipaa-production-path.md`. The big items:

- BAA-covered LLM (NVIDIA hosted NIMs are **not** BAA-covered — route through Bedrock or Azure OpenAI)
- KMS at rest, 6-year audit retention, key rotation, breach SOP
- Split `DASHBOARD_API_TOKEN` into two scoped credentials (Pipecat-write, Cekura-write)
- Org-scoped RLS via `auth.uid()`
