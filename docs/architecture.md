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
- **Per-call security:** `/twiml` mints a one-time token (delivered via `<Stream><Parameter>`
  and the ws URL); `/ws` rejects any connection without a matching token, so a stranger who
  finds the public endpoint can't open a session and burn STT/LLM/TTS credits.

## Conversation control (what makes it usable, not just a demo)
Implemented in `src/twilio_bot.py`:
- **Deterministic greeting.** The opening line is spoken once as a fixed `TTSSpeakFrame` and
  seeded into context as the assistant's first turn — so a barge-in can't trigger a
  regeneration that loops the intro.
- **Patient endpointing.** Silero VAD only *triggers* the turn decision; a prosody model
  (Smart-Turn v3, preloaded once at startup) makes the real call, so the agent waits out
  mid-sentence pauses instead of cutting the caller off.
- **Goodbye → auto-hangup.** A processor watches the agent's *completed* turns for a genuine
  sign-off and, once the conversation has actually run its course, queues an `EndFrame` after
  the farewell finishes playing. A max-duration guard is the backstop.

## Why Pipecat (vs LiveKit vs raw WebSockets)
- **Pipecat** = the orchestration brain. Vendor-neutral; swap any component in one line.
- **LiveKit** = WebRTC *transport* infra. Use its transport when the client is a
  **browser/app** over the open internet. Not needed for phone.
- **Raw WebSockets** = we'd rebuild interruptions/streaming ourselves. Pipecat wraps it.

## Model strategy (managed now → customized later)
| Slot | Start (works today) | Later (self-host / customize) |
|------|---------------------|-------------------------------|
| STT  | **Deepgram** (`DeepgramSTTService`) — NVIDIA speech gated for our key | NVIDIA Nemotron-Speech/Parakeet NIM on AWS GPU |
| LLM  | **Nemotron** via build.nvidia.com (OpenAI-compatible endpoint) | Nemotron NIM on SageMaker JumpStart / Bedrock (fine-tunable) |
| TTS  | **Cartesia Sonic** (`CartesiaTTSService`) — expressive; Aura was too flat | NVIDIA Magpie-TTS NIM on AWS GPU |

> Note: NVIDIA's hosted *speech* models (Magpie/ASR) returned gRPC UNAUTHENTICATED for our
> `nvapi-` key (partner-gated), so STT runs on Deepgram and TTS on Cartesia Sonic. The
> Nemotron **LLM** works fine via the hosted API. We converge on full-NVIDIA speech when we
> self-host.

Pipecat decouples transport from pipeline, so swapping managed→self-hosted is mostly config.

## Wellness data layer (planned — W2/W3)
The agent's reason for being is a person's **wellness record**. The plan:

```
Nemotron (brain) ──tool_call──▶ wellness tool ──▶ DB access layer ──▶ database
                                     │
                  (read record / update record / log a check-in)
```
- A small, **DB-scoped** set of tools — *not* the open-ended Mac harness that was removed.
  Each tool does one well-defined thing against the wellness store (e.g. fetch a patient's
  recent check-ins, record today's reported symptoms, flag a follow-up).
- **Context injection:** before/at the start of a call, the relevant slice of the person's
  record is summarized into the system prompt so the agent opens already knowing who it's
  talking to and what to follow up on.
- **Safety first:** health information is sensitive. Writes are validated and scoped; the
  agent gives wellness *support and check-ins*, not diagnosis. Exact schema, access rules,
  and tool contracts are defined in W2/W3 (the user will provide the prompt + tools).

## Removed (pre-pivot)
The earlier Mac-control system — `src/mac_tools/` registry, the GLM-5.1 tool factory,
`src/mac_actions.py` (osascript), `src/agent_memory/` (cross-call SQLite), and all per-call
confirmation / caller-authorization wiring — has been deleted. timbre is a talk-only agent;
the wellness DB tools are a fresh, narrow surface, not a revival of that harness.
