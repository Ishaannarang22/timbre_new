# Cekura — timbre postpartum eval loop (operator guide)

Companion to `PERSONAS_PRD.md`. Everything below reflects what was actually built
in the Cekura console; see the PRD for *why*.

## What's deployed

**Cekura workspace:** organisation 4824, project 5858 ("kanika gupta Project").

**Agent under test:** `timbre_postpartum_v1` — Cekura agent ID **18053**.
Self-hosted provider, inbound to `+18667690754` (the Pipecat agent's Twilio
number). Pipecat-side dropoff/topic nodes were auto-populated from the agent
description on first save; auto-update is now turned OFF so manual edits stick.

**Personas / scenarios:** four, all attached to agent 18053 and wired to all
six metrics. Run any one with `mcp__cekura__scenarios_run_voice scenarios=[<id>]`.

| Slug | Scenario ID | Tags |
|---|---|---|
| `the_contradiction` | **272851** | clinical-safety, incision, escalation, bilingual |
| `cost_blocker` | **272858** | cost-barrier, pharmacy-routing, no-escalation |
| `proxy_responder` | **272859** | identity-rejection, hipaa, no-clinical-data |
| `ambiguous_healer` | **272860** | endpointing, context-stability, low-energy |

Use the `persona:<slug>` tag to select via the `tags=` param.

**Metrics (criterion enum):** six, project-scoped, all `llm_judge` except as noted.

| Slug (== criterion in dashboard) | Metric ID | eval_type |
|---|---|---|
| `node_transition_accuracy` | **147795** | binary_qualitative |
| `context_strategy` | **147796** | binary_qualitative |
| `tool_call_latency_ms` | **147797** | numeric (worst latency in ms; pass ≤1500) |
| `global_function_reliability` | **147798** | binary_qualitative |
| `pii_redaction` | **147799** | binary_qualitative |
| `escalation_correctness` | **147800** | binary_qualitative |

A run passes overall only when **all six** are green; `overall_score` is the
percentage of green criteria (matches the PRD's locked rule).

**Personality (voice / TTS):** all four scenarios use Cekura's global default
personality **693 ("Normal Male", American)**. Custom personalities — which is
where per-persona accent, language code, and background-noise audio would live
— were blocked by a 403 on `personalities_create`, and `enabled_personalities`
silently reverts to `[693]` on partial-update. This is an org-tier restriction;
to lift it, upgrade the Cekura plan and either (a) create the four custom
personalities described in `PERSONAS_PRD.md §3` or (b) enable the global
personalities that already match (see "Future voice upgrade" below).

Accent, language switching, and background-noise direction are instead baked
into each scenario's `instructions` text under the `VOICE & ENVIRONMENT`
section, so the simulator LLM colours its word choice accordingly — but the TTS
voice itself is the same "Normal Male" across all four runs until the tier
restriction is lifted.

## Running an eval

### From this Claude Code session (Cekura MCP)

```python
# Single persona
mcp__cekura__scenarios_run_voice(
    agent_id=18053,
    scenarios=[272851],            # the_contradiction
    name="smoke: the_contradiction",
    concurrency_limit=1,
)

# All four, serially (trial Twilio = one concurrent call)
mcp__cekura__scenarios_run_voice(
    agent_id=18053,
    tags=["persona:the_contradiction","persona:cost_blocker",
          "persona:proxy_responder","persona:ambiguous_healer"],
    name="smoke: full sweep",
    concurrency_limit=1,
)
```

### From the Cekura web console

Project 5858 → Agents → `timbre_postpartum_v1` → Evaluators → select scenarios
→ Run. Use concurrency 1.

## Interpreting a failure on the dashboard

When the dashboard at `<vercel-url>/evals` shows a red criterion badge, click
through to the run detail. The mapping back to root cause:

| Red criterion | Likely cause |
|---|---|
| `node_transition_accuracy` | Pipecat Flow graph took an unexpected branch — check `current_node` PATCH log on the `call` row. |
| `context_strategy` | LLM looped the same question 3+ times, or leaked proxy-answer state into a clinical node. Re-read the transcript around the loop point. |
| `tool_call_latency_ms` | NVIDIA Nemotron stall (known intermittent 5-30s pause). One slow call shouldn't fail this on a multi-run; if it fails on a single run, retry once before reporting. |
| `global_function_reliability` | Either a missed escalation (worst: clinical) or a missed `lookup_*` when the patient explicitly asked. Check the function-call list in the run detail. |
| `pii_redaction` | The agent echoed a raw phone number, email, SSN, or street address into a dashboard write. Grep the run transcript for the leaked pattern. |
| `escalation_correctness` | Wrong severity, wrong category (`clinical` instead of `maternal` is the common one), or escalation on a non-emergency. |

## Known-false-positive guard rails

- **Re-phrasing a question once is expected, not a failure** — the
  `context_strategy` rubric explicitly allows it. If you see this flagged on
  ambiguous_healer, re-read the rubric in metric 147796.
- **Latency ≤1500 ms p95** — single-call outliers happen because Nemotron
  stalls. Run 3× per persona and take p95 across the 3.
- **Maria's CSAT being absent is not a missing data point** — it's a *pass*
  signal (`escalation_correctness` requires CSAT to NOT be collected when an
  escalation fires).

## Smoke-run prerequisites (read before you trigger anything)

Before any `scenarios_run_voice` call actually completes end-to-end, all three
of these must be true:

1. **Dashboard deployed** at a public Vercel URL with `DASHBOARD_API_TOKEN`
   set in Vercel env. Cekura needs to be able to POST eval results to
   `<vercel-url>/api/v1/evals*`. As of this commit, `.vercel/` does not exist
   in `timbre_dashboard/` — deploy is pending.
2. **Pipecat agent live** on Pipecat Cloud, answering inbound on the Twilio
   number `+18667690754`. Verify by placing a manual test call.
3. **Twilio account funded** (or at least: the Cekura simulator's outbound
   Twilio can reach `+18667690754`). The PRD's trial-Twilio warning was about
   the Pipecat *agent's* outbound; for these inbound test runs, Cekura's own
   Twilio plan is what matters.

Until (1) is true, eval results have nowhere to go — the run completes inside
Cekura but the dashboard `/evals` page stays empty. Until (2) is true, the
call dials and dies on a busy signal.

## Future voice upgrade — turning on real accent + bg noise

Once the org has personality entitlement, the global personalities below match
each persona well enough to use without custom-creating:

| Persona | Suggested global personality | id |
|---|---|---|
| `the_contradiction` | "Normal (Bg Noise People talking)" Spanish | **4715** |
| `cost_blocker` | "Normal (Bg Noise People talking)" American | **4257** |
| `proxy_responder` | "Normal Male - Indian" | **441** |
| `ambiguous_healer` | "Slow Speaker - Pauses" American | **730** |

After enabling, swap each scenario's `personality` field with the matching id
and re-run.

## Reference

- PRD: `~/Documents/GitHub/timbre_dashboard/cekura/PERSONAS_PRD.md`
- Voice flow under test: `~/Documents/GitHub/timbre_new/POSTPARTUM_FLOW_PRD.md`
- Synthetic patient seed: `~/Documents/GitHub/timbre_dashboard/supabase/seed.sql`
- Eval table schema: `~/Documents/GitHub/timbre_dashboard/supabase/schema.sql`
- Cekura org: 4824 — project: 5858
