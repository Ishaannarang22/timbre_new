# Setup — accounts, APIs, permissions, local env

**Decision (2026-05-25):** Start on **NVIDIA's free hosted endpoints** (`build.nvidia.com`).
This needs **one API key and no AWS** — fastest way to learn the pipeline. We move to
AWS (Bedrock / SageMaker JumpStart NIM) and self-hosting later (M5+).

---
## 1. The ONE thing you need now: an NVIDIA API key
1. Go to **https://build.nvidia.com** and sign up (free **NVIDIA Developer Program**).
2. Pick any model (e.g. a Nemotron model) → **"Get API Key"** / generate a key.
3. The key looks like `nvapi-xxxxxxxxxxxx`. Copy it.
4. We'll put it in `.env` as `NVIDIA_API_KEY` (git-ignored — never committed).

Free tier includes credits that are plenty for learning. No credit card needed to start.

> That's the whole prerequisite for M0–M4. Everything below (AWS, Twilio) comes later.

---
## 2. What each NVIDIA service maps to in our pipeline
| Slot | Pipecat class | NVIDIA model (hosted) |
|------|---------------|------------------------|
| STT  | `NvidiaSTTService` (gRPC, cloud) | Nemotron-Speech / Parakeet |
| LLM  | `OpenAILLMService` pointed at `integrate.api.nvidia.com/v1` | Nemotron (Nano/Super) |
| TTS  | `NvidiaTTSService` (gRPC, cloud) | Magpie-TTS |
| VAD  | Silero (local, free) | — |

> **Verified 2026-05-26:** the `nvapi-` key works for the **LLM** (`integrate.api.nvidia.com`)
> but the **speech models** (Magpie TTS / Nemotron ASR on `grpc.nvcf.nvidia.com`) return
> **gRPC UNAUTHENTICATED** — partner-gated for this account. So we keep **Nemotron as the LLM**
> and use another STT/TTS provider until we self-host the speech NIMs (M5/M6).

---
## 3. Twilio (deferred to M1)
When we add phone calls, collect from the Twilio Console:
- **Account SID**, **Auth Token**, and a **Voice-capable phone number** (~$1/mo).
- **ngrok** (free) for a public dev URL.

---
## 4. AWS (deferred to M5+)
Only when we self-host / customize: Bedrock model access or SageMaker JumpStart, plus a
scoped IAM policy. Not needed now — keep the surface small while learning.

---
## 5. Local environment
- **Python 3.10–3.12 recommended** (you have 3.13; we'll use a virtualenv and fall back
  to 3.12 if a Pipecat dependency complains).
- **portaudio** system lib — needed for local mic/speaker (M0). On macOS: `brew install portaudio`.
- Secrets in **`.env`** (git-ignored). Template in `.env.example`.

---
## Status checklist
- [ ] NVIDIA account + `nvapi-` API key   ← **do this now**
- [ ] Project scaffolded (venv, deps)
- [ ] portaudio installed (for local mic)
- [ ] M0 runs locally
- [ ] Twilio account + number (defer to M1)
- [ ] AWS (defer to M5)
