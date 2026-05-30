# Roadmap

Status legend: ⬜ not started · 🟦 in progress · ✅ done

## M0 — Local mic pipeline 🟦
Run a Pipecat pipeline on the laptop mic/speaker. Goal: understand frames, processors,
and the pipeline object before any telephony. Hosted/managed models so there's no GPU.
**Teaches:** Pipecat's core model.
- Code: `src/m0_local_bot.py` · Notes: `docs/milestones/m0-local-mic.md`
- Stack: Deepgram (STT) + Nemotron via build.nvidia.com (LLM) + Cartesia Sonic (TTS).
  NVIDIA speech is partner-gated for our key → deferred to self-host (M5/M6).
- Status: code written & compiles; **awaiting DEEPGRAM_API_KEY in `.env`
  (NVIDIA LLM key already set & verified).**

## M1 — Twilio "hello" call ✅
FastAPI server + TwiML webhook + WebSocket. Twilio calls in, agent says one hardcoded
line, hangs up. cloudflared for the public URL. **Teaches:** Twilio↔Pipecat wiring, the serializer.
- Code: `src/twilio_bot.py` · `src/run_morning_call.py` · Notes: `docs/milestones/m1-twilio-phone.md`
- Status: **done** — outbound call flow, `/twiml` → `<Connect><Stream>` → WebSocket → Pipecat working.
  cloudflared quick-tunnel replaces ngrok; 90s edge-warmup polling prevents silent fallback.

## M2 — Real conversation 🟦
Add STT + Nemotron LLM + TTS. Actually talk to it. **Teaches:** streaming the loop, latency.
- Status: **substantially done** — Deepgram STT + Nemotron LLM + Cartesia Sonic (Brooke) TTS all
  wired at 8kHz μ-law; two-way conversation works; 7 AM daily cron scheduled. Remaining: conversation
  control (stop re-greeting, hang up on goodbye — M3 territory).

## M3 — Make it human ⬜
Tune VAD/endpointing, enable interruptions (barge-in), system prompt + greeting.
**Teaches:** what separates a demo from something usable.

## M4 — Tools / function calling 🟦
Agent takes actions on the Mac mid-call. **Teaches:** tools in real-time, safe action design.
- A `src/mac_tools/` **registry** of audited osascript/shell tools across ~15 categories
  (media, system, display, apps, windows, files, clipboard, screen, web, notifications,
  productivity, messaging, input, network, power) — self-describing via Pipecat `FunctionSchema`.
- **Dynamic factory:** `request_new_tool` → GLM-5.1 authors a tool → validator → hot-registered
  into the live call with no daemon restart (`src/mac_tools/factory.py`, `validator.py`).
- **Agent memory:** cross-call SQLite store + Nemotron summarizer + `recall()` injection
  (`src/agent_memory/`), plus live `recall_memory` / `remember_this` tools.
- **Safety:** CONFIRM-gated risky actions (voice read-back), Trash-only deletes, owner-caller
  authorization, hard secrets carve-out. See `docs/tooling/CONTRACT.md`.
- Status: core framework + tool categories + factory + memory built (parallel agents);
  Twilio integration + QA/security gates in progress on branch `feat/mac-tools-factory-memory`.

## M5 — Self-host the LLM ⬜
Deploy a Nemotron NIM on SageMaker JumpStart; point Pipecat at it.
**Teaches:** NIM, managed GPU endpoints, OpenAI-compatible endpoints.

## M6 — Self-host STT + TTS ⬜
Parakeet/Nemotron-Speech + Magpie as NIMs. Whole engine is ours. **Teaches:** multi-model serving.

## M7 — Accelerate & measure ⬜
TensorRT-LLM, per-stage latency profiling, reasoning-vs-latency tradeoffs.
**Teaches:** the actual "engine" craft.

## M8 — Production deploy ⬜
Orchestration, autoscaling, cost control. **Teaches:** what breaks at scale.

---
## Learning log
_(notes appended as milestones complete)_
