# voice_fun — Project Guide

## What this project is
Building & customizing **high-reasoning voice engines**: NVIDIA-accelerated open-weight
models (Nemotron family) running on AWS, fronted by a **Pipecat + Twilio** phone agent.
This is a structured **learning** project as much as a build.

## Working agreement (how Claude should work here)
- **Teach while building.** Narrate the reasoning behind each choice.
- **Synopsis after every command and step** — 1–3 sentences: what happened + why it matters.
- **Document as we go** in the `docs/` folder. `docs/` is the source of truth for design.
- Prefer **small, runnable increments** over large drops.
- Update `docs/roadmap.md` as milestones complete.

## The stack
- **Orchestration:** Pipecat (Python) — the STT→LLM→TTS pipeline.
- **Telephony:** Twilio Media Streams (WebSocket transport, 8kHz μ-law).
- **Models (start on NVIDIA's free hosted NIMs, self-host/customize later):**
  - LLM: NVIDIA **Nemotron** via `build.nvidia.com` (OpenAI-compatible endpoint) → AWS/self-host later.
  - STT: **Nemotron-Speech / Parakeet** via `build.nvidia.com` (`NvidiaSTTService`).
  - TTS: **Magpie-TTS** via `build.nvidia.com` (`NvidiaTTSService`).
- **Compute:** Start with NVIDIA's hosted endpoints (just an `nvapi-` key — no AWS, no GPU bill).
  Graduate to AWS (Bedrock / SageMaker JumpStart NIM, then GPU instances) when customizing — M5+.

## Key engineering tension to keep in mind
High **reasoning** (thinking tokens) fights low **latency** (voice needs sub-second responses).
Design around it: tiered models, filler phrases, streaming, TensorRT acceleration.

## Roadmap (see docs/roadmap.md for detail)
- M0  Local mic pipeline (learn Pipecat) — no telephony
- M1  Twilio "hello" call (wire the WebSocket transport)
- M2  Real conversation (STT + LLM + TTS)
- M3  Make it human (interruptions, endpointing, prompt)
- M4  Tools / function calling
- M5  Self-host the LLM (SageMaker JumpStart NIM)
- M6  Self-host STT + TTS
- M7  Accelerate & measure (TensorRT, latency profiling)
- M8  Production deploy

## Docs index
- `docs/architecture.md` — how the pieces fit
- `docs/roadmap.md` — milestone detail + status
- `docs/setup.md` — accounts, APIs, IAM permissions, local env
