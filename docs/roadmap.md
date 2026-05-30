# Roadmap

Status legend: ⬜ not started · 🟦 in progress · ✅ done

> **Pivot (2026-05-30):** timbre moved from a Mac-control assistant to a **wellness &
> health-checkup voice agent**. The Mac harness was stripped; the voice + telephony infra
> carried over. Milestones are renumbered W0–W7 for the new direction. The pre-pivot
> milestone notes (M0–M8) live in `docs/milestones/`.

## W0 — Pure voice pipeline ✅
STT→LLM→TTS over Twilio, talk-only. Forked from the Mac agent with the entire control harness
removed. **Teaches:** what the irreducible voice core is.
- Code: `src/twilio_bot.py` (644 lines), `src/run_morning_call.py`, `src/m0_local_bot.py`
- Stack: Deepgram STT + Nemotron LLM (build.nvidia.com) + Cartesia Sonic (Brooke) TTS at 8kHz μ-law.
- Status: **done** — compiles + imports clean; `/twiml`→`<Connect><Stream>`→`/ws`→Pipecat;
  deterministic greeting, patient Smart-Turn endpointing, goodbye→auto-hangup, per-call ws-token auth.

## W1 — Wellness persona & prompt 🟦
Replace the morning-quote persona with a warm **health check-up** conversation: ask how the
person is feeling, follow up on known concerns, keep it natural and unhurried. **Teaches:**
prompt design for a care conversation; the reasoning-vs-warmth tension.
- The system prompt is a first-class artifact — iterate it deliberately (see W4).
- Status: **awaiting the new system prompt** (user to provide).

## W2 — Wellness data model ⬜
Define what timbre stores about a person: identity, baseline, check-in history, flagged
follow-ups, consent/access. **Teaches:** modeling health data responsibly.
- Deliverable: schema + an access layer (`src/wellness/` or similar), DB choice, migration story.
- Privacy: sensitive data stays out of git (gitignored store), scoped reads/writes.

## W3 — DB-backed tools ⬜
Wire a **narrow, DB-scoped** tool surface into the live call: read a person's record, update it,
log a check-in, flag a follow-up. **Teaches:** real-time tools done safely after the lesson of
the over-broad Mac harness.
- Tools are added back deliberately — each does one well-defined DB thing, validated server-side.
- Context injection: summarize the person's record into the prompt at call start.
- Status: **awaiting tool definitions** (user to provide).

## W4 — Continuous prompt/context improvement ⬜
Stand up an eval-driven loop so the persona and context measurably improve over time
(scenario transcripts → scored → prompt/context revisions). **Teaches:** treating prompt +
context as iterated, measured artifacts rather than one-shot text.

## W5 — Self-host the models ⬜
Nemotron LLM on a NIM (SageMaker JumpStart / Bedrock), then Parakeet/Nemotron-Speech + Magpie
for STT/TTS. **Teaches:** NIM, managed GPU endpoints, multi-model serving.

## W6 — Accelerate & measure ⬜
TensorRT-LLM, per-stage latency profiling, reasoning-vs-latency tradeoffs. **Teaches:** the
actual "engine" craft.

## W7 — Production deploy ⬜
Orchestration, autoscaling, cost control, on-call. **Teaches:** what breaks at scale.

---
## Learning log
_(notes appended as milestones complete)_

- **W0 (2026-05-30):** Stripped the Mac-control harness from the timbre agent and pushed the
  talk-only pipeline to a fresh repo (`timbre_new`). Removed `mac_tools/`, `mac_actions.py`,
  `agent_memory/`, and all tool/confirmation/authorization wiring in `twilio_bot.py`
  (1071→644 lines). Kept the full voice + telephony stack. Verified: compiles, imports, routes present.
