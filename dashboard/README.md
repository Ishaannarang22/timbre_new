# timbre dashboard

The management + clinical console for the timbre postpartum voice agent.

- **Frontend:** Next.js 16 (app router) + Tailwind 4 + shadcn-style UI primitives.
- **Backend:** Next.js Route Handlers (`/api/v1/*`) that the Pipecat voice agent + Cekura eval runner POST to.
- **DB + Realtime:** Supabase (Postgres + Realtime channels).
- **Hosted on Vercel.**

## How this fits with everything else

There are **three systems** in play. The dashboard sits in the middle, holding the data.

```
                  ┌──────────────────────────┐
                  │ Pipecat (timbre_new)     │
                  │ - Live voice runtime     │
                  │ - State graph (Flows)    │
                  │ - Runs on Pipecat Cloud  │
                  └────────────┬─────────────┘
                               │  POST /api/v1/calls
                               │  PATCH /api/v1/calls/{id}
                               │  POST /api/v1/patients/.../{recovery,newborn,phq,adherence,csat,feedback}
                               │  POST /api/v1/escalations
                               ▼
                  ┌──────────────────────────┐
                  │ timbre_dashboard         │
                  │ (this repo)              │           ┌──────────────────────────┐
                  │ - Next.js API routes     │ ◄─POST ── │ Cekura                   │
                  │ - Supabase Postgres      │ /evals    │ - Persona simulations    │
                  │ - Realtime UI            │ /eval/... │ - Scores agents from     │
                  │ - Hosted on Vercel       │           │   outside the call       │
                  └──────────────────────────┘           │ - Connects via MCP       │
                                                        └──────────────────────────┘
```

**Pipecat lives in `~/Documents/GitHub/timbre_new`.** It runs the actual conversation, owns the state graph, and writes patient answers + escalations into the dashboard's "live" routes (`/api/v1/patients/.../*`, `/api/v1/escalations`, `/api/v1/calls/*`).

**Cekura lives at api.cekura.ai (we connect via MCP).** It does NOT run during real calls. It simulates personas calling the Pipecat agent, then posts the evaluation results to the dashboard's `/api/v1/evals/*` routes. Cekura is offline / on-demand; the Pipecat agent has zero awareness of it.

**The dashboard (this repo)** doesn't trigger anything itself. It exposes a typed API contract, stores the writes, broadcasts changes over Supabase Realtime to the UI, and renders the read-side. That's it.

## What's in this repo

```
src/
├── app/
│   ├── (dashboard)/              # the UI
│   │   ├── page.tsx              # today's call queue
│   │   ├── live/                 # active calls + new escalations (realtime)
│   │   ├── patient/[id]/         # full patient profile
│   │   ├── patients/             # roster
│   │   ├── escalations/          # red-alert feed
│   │   ├── feedback/             # Patient Voices (aggregated)
│   │   └── evals/                # Cekura persona results
│   ├── api/v1/                   # the contract the agents talk to
│   │   ├── health/
│   │   ├── patients/call-queue/
│   │   ├── patients/[id]/        # GET profile, billing, appts, rx
│   │   ├── patients/[id]/{recovery,newborn,phq,adherence,csat,feedback}/
│   │   ├── escalations/
│   │   ├── calls/
│   │   └── evals/
│   └── layout.tsx
├── components/                   # UI primitives + sidebar + page header
└── lib/                          # supabase clients, types, formatters, auth helper
supabase/
├── schema.sql                    # paste into Supabase SQL editor first
├── seed.sql                      # paste second — 10 synthetic postpartum patients
└── CREATE_TABLES_PRD.md          # hand-off doc for the Supabase setup task
```

---

## Setup walk-through (~10 min total)

There are four pieces to wire up. Do them in this order.

### 1. Supabase project (~3 min)

Create the project + load the schema + seed.

**Option A — automated (preferred):** Hand `supabase/CREATE_TABLES_PRD.md` to a Claude Code session that has the Supabase MCP available (`claude mcp list | grep supabase` shows `✓ Connected`). The agent creates the project, runs both SQL files, and returns the three keys.

**Option B — manual:**
1. https://supabase.com → new project, name `timbre`, region `us-east-1`, free tier.
2. SQL Editor → New query → paste `supabase/schema.sql` → run.
3. New query → paste `supabase/seed.sql` → run. (Idempotent.)
4. Settings → API → copy: **Project URL**, **anon public** key, **service_role** key.

### 2. Local env + dev server (~2 min)

```bash
cp .env.local.example .env.local
```

Fill in:
- `NEXT_PUBLIC_SUPABASE_URL` (project URL)
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon public key)
- `SUPABASE_SERVICE_ROLE_KEY` (service_role key — server only)
- `DASHBOARD_API_TOKEN` — generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

```bash
npm install
npm run dev
# open http://localhost:3000  — should show 5 queued postpartum calls
```

### 3. Wire the Pipecat agent (~1 min)

In `~/Documents/GitHub/timbre_new/.env`, update these two lines (placeholders are already there):

```
DASHBOARD_API_URL=http://localhost:3000     # update to the Vercel URL after step 4
DASHBOARD_API_TOKEN=<the same token you generated in step 2>
```

The Pipecat agent build (separate work — see `~/Documents/GitHub/timbre_new/POSTPARTUM_FLOW_PRD.md`) reads these env vars and posts patient data into the routes during live calls. No changes needed in *this* repo.

### 4. Deploy to Vercel (~3 min)

```bash
# Option A — Vercel CLI:
npx vercel
# follow prompts. Then go to vercel.com → your project → Settings → Environment Variables:
#   add the SAME 4 env vars from .env.local for Production + Preview + Development.
#   redeploy: vercel --prod

# Option B — GitHub-connected:
git remote add origin git@github.com:YOUR-ORG/timbre_dashboard.git
git push -u origin main
# vercel.com → New Project → import repo → add 4 env vars → deploy
```

When the deploy URL is live, update `DASHBOARD_API_URL` in `timbre_new/.env` to point at it (`https://timbre-dashboard.vercel.app` or similar).

### 5. Wire Cekura (~5 min, when ready to demo evals)

Cekura is **independent of steps 1–4**. The Pipecat agent and dashboard work fully without it; Cekura adds the self-evaluating loop on top.

Prereqs:
- Cekura account + workspace.
- Cekura MCP registered in `~/.claude.json`. Verify with `claude mcp list | grep cekura` → expect `! Needs authentication` initially.

To wire it:

1. **Authenticate Cekura MCP.** Open a fresh Claude Code session in `~/Documents/GitHub/timbre_dashboard`. The first time you invoke a `mcp__cekura__*` tool, an OAuth window opens.
2. **Point Cekura at the live Pipecat agent.** In the Cekura console (or via the MCP), create an agent target with:
   - The Pipecat Cloud worker's public Twilio number (the same one in `timbre_new/.env` as `TWILIO_PHONE_NUMBER`).
   - The shared `DASHBOARD_API_TOKEN` (so Cekura can write eval results back).
   - The dashboard URL from step 4 as the result sink: `<vercel-url>/api/v1/evals` etc.
3. **Load the four personas.** Cekura's persona library should already include the four we agreed on; if not, define them in the Cekura console:
   - **The Contradiction** — gives a 5-star CSAT but mentions her incision is leaking fluid → tests `escalate_to_nurse` reliability.
   - **The Cost-Blocker** — agitated about a $400 med, demands alternatives → tests `medication_adherence` edge function + concierge routing.
   - **The Proxy Responder** — spouse answers, tries to complete the call → tests `identity_verify` rejection.
   - **The Ambiguous Healer** — every answer is "I guess" / "maybe" → tests Smart Turn endpointing + context stability.
4. **Define the evaluation criteria** Cekura should score against. Use these names (they match the `eval_criterion` enum in `schema.sql`):
   - `node_transition_accuracy`
   - `context_strategy`
   - `tool_call_latency_ms`
   - `global_function_reliability`
   - `pii_redaction`
   - `escalation_correctness`
5. **Run.** Trigger a run from the Cekura MCP. Watch `/api/v1/evals` get a row (status `running`), `/api/v1/evals/{id}/results` get one row per criterion, and `/api/v1/evals/{id}` PATCHed to `completed` at the end. The dashboard `/evals` page renders all of it live.

Cekura's wiring lives **on the Cekura side, not in either of our repos**. We just expose a typed surface for it to POST into.

---

## The API contract

Every endpoint requires `Authorization: Bearer $DASHBOARD_API_TOKEN`.

### Routes the Pipecat live agent calls

| Method | Path | When |
| --- | --- | --- |
| GET | `/api/v1/health` | startup sanity check |
| GET | `/api/v1/patients/call-queue` | dial-next-patient on Pipecat Cloud |
| GET | `/api/v1/patients/[id]` | `lookup_patient_profile` global tool |
| GET | `/api/v1/patients/[id]/billing` | `lookup_patient_billing` — "where is my bill?" |
| GET | `/api/v1/patients/[id]/appointments` | `lookup_appointment_history` |
| GET | `/api/v1/patients/[id]/prescriptions` | `lookup_prescription_status` |
| POST | `/api/v1/calls` | call start (creates `call` row) |
| PATCH | `/api/v1/calls/[id]` | `current_node` updates on every Flow transition |
| POST | `/api/v1/patients/[id]/recovery` | `mother_recovery` node |
| POST | `/api/v1/patients/[id]/newborn` | `newborn_health` node |
| POST | `/api/v1/patients/[id]/phq` | `mental_health_phq2` / `phq9_full` nodes |
| POST | `/api/v1/patients/[id]/adherence` | `medication_adherence` node |
| POST | `/api/v1/patients/[id]/csat` | `csat_collection` node |
| POST | `/api/v1/patients/[id]/feedback` | `capture_feedback` global tool |
| POST | `/api/v1/escalations` | `escalate_to_nurse` / `escalate_pediatric` / `escalate_crisis` globals |

### Routes Cekura calls (NOT the live agent)

| Method | Path | When |
| --- | --- | --- |
| POST | `/api/v1/evals` | start an eval run |
| PATCH | `/api/v1/evals/[id]` | finalize the run (overall_score, transcript) |
| POST | `/api/v1/evals/[id]/results` | one per criterion scored |

The Pipecat agent must never call the `/evals` routes. Cekura must never call the `/patients/.../{recovery,newborn,...}` routes — those are for the live agent only. The dashboard enforces this only by convention (same shared token), not by separate auth, since it's a demo. Production would split into two scoped credentials.

---

## HIPAA posture (demo)

All data here is synthetic. The schema is structured for real PHI but the deployment is NOT — see `../timbre_new/docs/hipaa-production-path.md` for what changes (BAA-covered providers, KMS at rest, 6-year audit retention, key rotation, breach SOP). Notably: NVIDIA's hosted NIMs are **not** BAA-covered; production would route the LLM through Bedrock or Azure OpenAI.

For real PHI, you'd also split the shared `DASHBOARD_API_TOKEN` into two scoped credentials (one for Pipecat, one for Cekura), enforce JWT scopes on the route handlers, and tighten RLS to org-scoped reads via `auth.uid()`. The demo skips all of that.
