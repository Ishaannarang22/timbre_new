# Architecture

## The voice agent loop
A voice agent is a streaming pipeline that runs many times per second:

```
Mic/Phone audio → [VAD] → [STT] → [LLM] → [TTS] → Speaker/Phone audio
                    ↑                                      │
                    └────────── interruption (barge-in) ───┘
```

- **VAD** (Voice Activity Detection): when did the user start/stop talking? (Silero)
- **STT** (Speech-to-Text): audio → text. Streams partial results for low latency.
- **LLM**: text → response text. Streams tokens.
- **TTS** (Text-to-Speech): text → audio. Streams audio chunks.
- **Transport**: how audio moves between caller and our server.

The hard part isn't wiring it once — it's **latency** (target < ~800ms response),
**interruptions**, and **turn-taking**. Pipecat handles this plumbing for us.

## Phone path (Twilio)
```
Caller ─PSTN─▶ Twilio ──webhook──▶ our server returns TwiML:
                  │                 <Connect><Stream url="wss://us/ws"/>
                  └── opens WebSocket, streams 8kHz μ-law audio ──▶ Pipecat
```
- Twilio Media Streams delivers call audio over a **WebSocket** (8kHz μ-law).
- Pipecat transport: `FastAPIWebsocketTransport` + `TwilioFrameSerializer`
  (handles encoding + resampling to/from what the models want).
- The fragile internet "last mile" is between caller and Twilio — **Twilio owns it**,
  so a plain WebSocket to our server is fine (no WebRTC needed for phone).

## Why Pipecat (vs LiveKit vs raw WebSockets)
- **Pipecat** = the orchestration brain. Vendor-neutral; swap any component in one line.
- **LiveKit** = WebRTC *transport* infra (+ its own agents framework). Use its transport
  when the client is a **browser/app** over the open internet (WebRTC's packet-loss
  resilience + echo cancellation earn their keep there). Not needed for phone.
- **Raw WebSockets** = we'd rebuild interruptions/streaming ourselves. Pipecat wraps it.

## Model strategy (managed now → customized later)
| Slot | Start (works today) | Later (self-host / customize) |
|------|---------------------|-------------------------------|
| STT  | **Deepgram** (`DeepgramSTTService`) — NVIDIA speech gated for our key | NVIDIA Nemotron-Speech/Parakeet NIM on AWS GPU |
| LLM  | **Nemotron** via build.nvidia.com (OpenAI-compatible endpoint) | Nemotron NIM on SageMaker JumpStart / Bedrock (fine-tunable) |
| TTS  | **Cartesia Sonic** (`CartesiaTTSService`) — expressive; Aura was too flat | NVIDIA Magpie-TTS NIM on AWS GPU |

> Note: NVIDIA's hosted *speech* models (Magpie/ASR) returned gRPC UNAUTHENTICATED for our
> `nvapi-` key (partner-gated), so STT runs on Deepgram and TTS on Cartesia Sonic (chosen
> over Deepgram Aura, which sounded flat and ignores prosody tags). The Nemotron **LLM** works fine via the hosted API. We converge on
> full-NVIDIA speech when we self-host (M5/M6).

Pipecat decouples transport from pipeline, so swapping managed→self-hosted is mostly config.

## Voice-controlled Mac tools (M4)
The agent can act on the host Mac via a tool **registry** (`src/mac_tools/`). Each tool is a
small, audited function (osascript/shell, injection-safe via `on run argv`) that is
self-describing to the LLM through a Pipecat `FunctionSchema`. Binding spec:
`docs/tooling/CONTRACT.md`.

```
Nemotron (brain) ──tool_call──▶ dispatch() ──▶ runner (osascript/shell) ──▶ macOS
                                   │
                  CONFIRM-class ───┴──▶ ConfirmationBroker (read-back → confirm_action → run)
```
- **Categories:** media, system, display, apps, windows, files, clipboard, screen, web,
  notifications, productivity, messaging, input, network, power.
- **Safety (enforced server-side, not trusted to the LLM):** risky actions (send / delete /
  disruptive) are CONFIRM-gated — staged, read back aloud, and run only after a
  `confirm_action` tool fires. Deletion is Trash-only. Tools are offered ONLY to an authorized
  caller (owner's number); secrets (Keychain / passwords / SSH / `.env`) are a hard carve-out.

## Dynamic tool factory (GLM-5.1)
When the agent lacks a tool, it calls `request_new_tool`; `src/mac_tools/factory.py` asks
**Z.AI GLM-5.1** (reserved for this — never in the voice hot path) to author one module, runs
it through `validator.py` (AST + deny-patterns), writes it to `generated/`, and **hot-registers
it into the live call** (append schema → `context.set_tools(...)` → `llm.register_function`) so
the same call uses it with no daemon restart. GLM gets a live system prompt rendered from the
current registry (kept current in `docs/tooling/glm_factory_prompt.md`).

## Agent memory (cross-call)
`src/agent_memory/` persists calls, turns, tool invocations, and durable facts in local SQLite
(`data/`, git-ignored). A summarizer (Nemotron) compresses each finished call; `recall()`
injects "what to remember about this caller" into the next call's system prompt, and the agent
can query memory live via `recall_memory` / save a fact via `remember_this`.
```
```

## Cekura observability
Completed Pipecat/Twilio calls are exported to Cekura from `src/cekura_observability.py`
when `CEKURA_API_KEY` and `CEKURA_AGENT_ID` (or `CEKURA_ASSISTANT_ID`) are configured.
The exporter sends the scrubbed user/assistant transcript and call metadata in a background
worker, so analytics failures cannot delay or break a live call.

Audio analysis is separately opt-in with `CEKURA_RECORD_CALLS=true`. When enabled, `/twiml`
starts a Twilio dual-channel recording, `/recording-status` receives Twilio's completion
callback, and the exporter downloads the authenticated WAV from Twilio before uploading it
to Cekura. Do not enable recording until the caller disclosure, consent, and retention policy
are appropriate for every jurisdiction where the agent is used.
