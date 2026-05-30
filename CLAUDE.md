# timbre — Project Guide

## What this project is
A **wellness & health check-up voice agent**: a phone companion that calls people (or takes
their calls), has a warm spoken conversation, and reads/updates their wellness records in a
**database**. Built on NVIDIA open-weight reasoning models (Nemotron family) fronted by a
**Pipecat + Twilio** phone pipeline. This is a structured **learning** project as much as a build.

> **Pivot note (2026-05-30):** timbre began as a Mac-control voice assistant. That entire
> harness — the AppleScript tool registry, the GLM tool factory, per-call confirmation,
> cross-call Mac memory — has been **removed**. The agent no longer controls a computer. It
> talks, and (next) it works against a wellness database. The voice + telephony infra is
> unchanged. The pre-pivot version is archived in the original `voice_fun` working copy.

## Working agreement (how Claude should work here)
- **Teach while building.** Narrate the reasoning behind each choice.
- **Synopsis after every command and step** — 1–3 sentences: what happened + why it matters.
- **Document as we go** in the `docs/` folder. `docs/` is the source of truth for design.
- Prefer **small, runnable increments** over large drops.
- Update `docs/roadmap.md` as milestones complete.
- **Continuously improve the prompt & context.** The agent's quality lives in its system
  prompt and the wellness context we feed it — treat both as first-class, iterated artifacts.

## The stack
- **Orchestration:** Pipecat (Python) — the STT→LLM→TTS pipeline.
- **Telephony:** Twilio Media Streams (WebSocket transport, 8kHz μ-law).
- **Models (start on NVIDIA's free hosted NIMs, self-host/customize later):**
  - LLM: NVIDIA **Nemotron** via `build.nvidia.com` (OpenAI-compatible endpoint) → AWS/self-host later.
  - STT: **Deepgram** today (NVIDIA speech is partner-gated for our key) → NVIDIA Parakeet/Nemotron-Speech when self-hosted.
  - TTS: **Cartesia Sonic** today → NVIDIA Magpie-TTS when self-hosted.
- **Data:** a wellness records database the agent reads from and updates mid-call (schema +
  tools TBD — see roadmap W3). Tools are added back deliberately, DB-scoped — *not* the old
  open-ended Mac harness.
- **Compute:** NVIDIA hosted endpoints now (just an `nvapi-` key); graduate to AWS when self-hosting.

## Key engineering tension to keep in mind
High **reasoning** (thinking tokens) fights low **latency** (voice needs sub-second responses).
Design around it: tiered models, filler phrases, streaming, TensorRT acceleration. For wellness,
add a second tension: **accuracy & safety of health information** vs. a natural, unhurried chat.

## Roadmap (see docs/roadmap.md for detail)
- W0  Pure voice pipeline (STT→LLM→TTS over Twilio) — **done** (stripped from the Mac agent)
- W1  Wellness persona & prompt (the check-up conversation)
- W2  Wellness data model (what we store about a person's health)
- W3  DB-backed tools (read record, update record, log a check-in) wired into the call
- W4  Continuous prompt/context improvement loop (eval-driven)
- W5  Self-host the LLM, then STT+TTS (NVIDIA NIMs on AWS)
- W6  Accelerate & measure (TensorRT, latency profiling)
- W7  Production deploy

## Docs index
- `docs/architecture.md` — how the pieces fit
- `docs/roadmap.md` — milestone detail + status
- `docs/setup.md` — accounts, APIs, local env
