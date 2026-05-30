# M0 — Local mic voice agent

**File:** `src/m0_local_bot.py`
**Goal:** Learn Pipecat's core model (frames → processors → pipeline) by talking to the
agent through your laptop mic/speaker, before any phone/telephony complexity.

## The pipeline
```
mic ─► [VAD] ─► STT ─► user-agg ─► LLM ─► TTS ─► speaker
                          ▲                          │
                          └──── assistant-agg ◄──────┘
```
| Processor | Role | Backed by |
|-----------|------|-----------|
| `transport.input()` | mic audio in + Silero VAD (detects speech) | local machine |
| `DeepgramSTTService` | speech → text | Deepgram (hosted) |
| `aggregator.user()` | add finished transcript to chat context | — |
| `OpenAILLMService` (NVIDIA base_url) | context → reply text | NVIDIA Nemotron (hosted) |
| `CartesiaTTSService` | reply text → speech | Cartesia Sonic (hosted, expressive) |
| `transport.output()` | audio out | speaker |
| `aggregator.assistant()` | add reply to context (memory) | — |

## Key concepts learned here
- **Frames & processors:** everything moving between boxes is a "frame"; each processor
  consumes/emits frames. The list order in `Pipeline([...])` IS the data flow.
- **Streaming:** STT, LLM, and TTS all stream, so audio starts playing before the full
  reply is generated — this is what keeps latency low.
- **Context aggregators:** two halves (user/assistant) that wrap the LLM and maintain
  conversation memory across turns.
- **VAD + interruptions:** `allow_interruptions=True` + Silero VAD lets you talk over
  the bot (barge-in). We tune this properly in M3.
- **LLMRunFrame:** injects a turn without user speech — used here for the opening greeting.

## How to run
1. Ensure `.env` has real keys: `NVIDIA_API_KEY` (LLM), `DEEPGRAM_API_KEY` (STT),
   `CARTESIA_API_KEY` (TTS).
2. `.venv/bin/python src/m0_local_bot.py`
3. Wait for the spoken greeting, then talk. Ctrl-C to quit.

## Config
- `NVIDIA_LLM_MODEL` (env) — default `nvidia/nemotron-3-nano-30b-a3b` (30B MoE, ~3B active),
  run with **thinking disabled** (`enable_thinking: False`, see the latency lesson below).
  Two models were rejected getting here:
  - `llama-3.1-nemotron-nano-8b-v1`: **black-holed ~20% of requests for 60s+** (see
    `scripts/bench_llm_latency.py`).
  - `nemotron-3-nano-omni-30b-a3b-reasoning`: fast (0.38s) and stall-free, but even with
    thinking toggled off it **leaked `<think>` tags and a paraphrase of the system prompt
    into spoken output** (it restates instructions as reasoning).
- Sampling (from NVIDIA playground): `temperature=0.2`, `top_p=0.95`, `max_tokens=16384`
  (a ceiling, not a target — the prompt keeps replies short), plus
  `extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}`.
- `LLM_REQUEST_TIMEOUT_SECS` (env, default 8.0) — hard per-request deadline; a rare safety net.
- `CARTESIA_SPEED` (env, default 0.95) — Cartesia Sonic speaking rate, passed via
  `Settings(generation_config=GenerationConfig(speed=...))`. Range ~0.6 (slow) to ~1.5 (fast);
  0.95 = warm, deliberate coaching pace. Tune by ear.
- `SMART_TURN_THRESHOLD` (env, default 0.7) — confidence the Smart Turn model needs to END
  your turn. The main "don't interrupt me" dial; see endpointing below.
- `VAD_STOP_SECS` (env, default 0.2) — silence before we *ask* the turn model. Keep short.
- `SMART_TURN_STOP_SECS` (env, default 2.0) — hard silence ceiling; force-ends the turn after this
  much continuous silence even if the model is unsure. Worst-case dead air; see below.

## Endpointing: who decides you're done talking
Two cooperating layers, and getting this right is what makes turn-taking feel human:
1. **VAD (Silero)** — pure acoustics. After `VAD_STOP_SECS` (0.2s) of silence it says "speech
   paused." This is only a *trigger to ask the question*, not the answer. We keep it short:
   making it long is the dumb-timeout approach (adds delay to every turn).
2. **Smart Turn v3** (`LocalSmartTurnAnalyzerV3`, a local ONNX prosody model) — the brain. On
   each pause it reads your intonation/rhythm and predicts complete vs. incomplete. Trail off
   mid-sentence → it says *incomplete* and your turn stays open across the pause. This is what
   makes a 0.2s VAD safe: the pause doesn't end your turn, the model's verdict does.

**The bug & fix:** upstream hardcodes the verdict at `probability > 0.5`, so a borderline pause
(model 51% sure you're done) ends your turn mid-thought. `PatientSmartTurnV3` (in `m0_local_bot.py`)
keeps the same model but makes that cutoff a knob — at `SMART_TURN_THRESHOLD=0.7` the model must be
*quite* sure you've finished before ending your turn. Every verdict is logged (`🛑 endpoint: prob_done=…`)
so you can tune by ear.

**Dynamics (important):** the model runs *once per pause* (when VAD fires) and does **not** re-score
itself as silence drags on. The score only changes if you keep talking (more speech = new evidence).
So a high threshold has a cost: if you finish but sound ambiguous (model says e.g. 0.6 < 0.7), it keeps
the turn open waiting for speech that never comes, and only the hard ceiling `SMART_TURN_STOP_SECS` ends
it. That ceiling is your worst-case dead air, so we lower it to 2s. **Two knobs working together:**
`SMART_TURN_THRESHOLD` high = don't interrupt me; `SMART_TURN_STOP_SECS` low = but don't stall if I'm done.

## Lesson: the LLM was secretly reasoning (the real latency culprit)
We thought `nemotron-3-nano-30b-a3b` was a plain chat model. It isn't — left alone it streams
**chain-of-thought into `reasoning_content`** before emitting any `content`. Pipecat only reads
`content`, so the spoken text *looked* clean and we never saw the thinking — but it ran on every
turn, and the first *spoken* word couldn't start until the model finished thinking. That hidden
reasoning, not the network, was what made replies slow and erratic.

Probed directly (`scripts/bench_llm_latency.py` + a raw streaming probe):
- thinking ON: `content` stayed empty while `reasoning_content` filled; with a small `max_tokens`
  the model spent the entire budget thinking and `finish_reason='length'` with no answer at all.
- thinking OFF (`enable_thinking: False`): `reasoning_content` empty, first `content` token in
  **~0.4–0.6s**, output still clean.

Fix: pass `extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}` to the LLM
service (Pipecat merges `extra` into the request body). **General rule:** before blaming the
network, check whether "first token" means first *content* token or first *reasoning* token — a
model can be streaming fast tokens you never see while your user hears silence.

(We briefly built a "filler words" processor to mask the latency, then removed it once the real
cause was found — masking a self-inflicted delay is worse than deleting the delay. Recoverable
from git history if a perceived-latency cover is ever wanted for genuinely slow self-hosted models.)

## Troubleshooting
- **Auth/401:** key wrong or not saved in `.env`.
- **Model not found (404):** the `NVIDIA_LLM_MODEL` id isn't available to your account —
  pick a Nemotron model id shown on build.nvidia.com.
- **No mic/garbled audio:** macOS may prompt for mic permission for your terminal; grant it.
