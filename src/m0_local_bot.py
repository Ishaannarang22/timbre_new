"""
Milestone 0 — Local mic voice agent (no telephony yet).

Goal: understand Pipecat's core model — frames flowing through a pipeline of
processors — by talking to the agent through your laptop mic/speaker.

The pipeline we build:

    mic ─► [VAD] ─► STT ─► user-aggregator ─► LLM ─► TTS ─► speaker
                                  ▲                                │
                                  └──── assistant-aggregator ◄─────┘

Every arrow carries "frames" (audio frames, text frames, control frames).
Each box below is a Pipecat "processor" that consumes some frames and emits others.
Components: Deepgram (STT) -> Nemotron via build.nvidia.com (LLM) -> Deepgram (TTS).
NVIDIA's hosted *speech* models are partner-gated for our key, so we use Deepgram for both
speech-to-text and text-to-speech (Aura voices) for now, keeping Nemotron as the brain;
we switch to NVIDIA speech NIMs when we self-host.
Needs NVIDIA_API_KEY and DEEPGRAM_API_KEY in .env — no AWS, no GPU.

Run:  .venv/bin/python src/m0_local_bot.py
Quit: Ctrl-C
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

# --- Pipecat building blocks --------------------------------------------------
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)

# System prompts live in prompts/prompts.json — edit there, not here.
from prompts import load_prompt

# Load NVIDIA_API_KEY (and anything else) from .env into the environment.
load_dotenv()

# Which Nemotron model answers. Small + fast = good for voice latency.
# Override in .env with NVIDIA_LLM_MODEL=... to try a bigger reasoning model.
# Nemotron 3 Nano (30B MoE, ~3B active) — the NON-reasoning chat model. We deliberately
# avoid the "-omni-...-reasoning" sibling: even with thinking toggled off it intermittently
# leaked <think> tags and a paraphrase of the system prompt into the SPOKEN output (it
# restates instructions as reasoning). A non-reasoning model can't do that. Slower TTFT
# (~1.3s vs 0.38s) but correct and stable — and it had no stalls like the old 8B did.
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")

# Hard per-request deadline for the LLM HTTP client. Caps the free endpoint's stalls
# (the SDK retries fresh on timeout). Lower = snappier failover, but too low cuts off
# legitimately slow first tokens. Override with LLM_REQUEST_TIMEOUT_SECS in .env.
LLM_REQUEST_TIMEOUT_SECS = float(os.getenv("LLM_REQUEST_TIMEOUT_SECS", "8.0"))

# Cartesia (Sonic) voice — expressive TTS with natural cadence/emotion. Override with
# CARTESIA_VOICE_ID in .env. Browse/clone voices at https://play.cartesia.ai
# `or` (not a default arg) so a blank CARTESIA_VOICE_ID= in .env falls back to the default
# instead of passing an empty voice id to Cartesia.
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"  # Brooke – Big Sister

# Speaking rate multiplier sent to Cartesia's Sonic-3 generation_config. Slightly under 1.0
# gives a warmer, more deliberate coaching pace — not sluggish, just unhurried. Range is
# roughly 0.6 (very slow) to 1.5 (very fast); 1.0 is Cartesia's neural default. Tune by ear:
# bump toward 1.1–1.2 for a punchier energy, drop toward 0.8 for a calm instructional feel.
# Override in .env with CARTESIA_SPEED=<float> without restarting the whole setup.
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))

# --- ENDPOINTING (when is the user *done* talking?) --------------------------
# This is the heart of natural turn-taking. Two layers cooperate:
#
#   1. VAD (Silero) — pure acoustics. After VAD_STOP_SECS of silence it says
#      "speech paused." That's just a TRIGGER to ask the real question, not the
#      answer. Keep it short/responsive — making it long would be the dumb
#      "timeout" approach (it'd add that delay to EVERY turn, even when you're
#      clearly finished).
#   2. Smart Turn v3 (a local ONNX prosody model) — the BRAIN. Each time VAD
#      pauses, it looks at your intonation/rhythm and predicts complete vs.
#      incomplete. If you trail off mid-sentence ("I feel stuck… in my…"), it
#      should say INCOMPLETE and keep your turn open across the pause. THIS is
#      what makes a 0.2s VAD safe — the pause doesn't end your turn, the model's
#      verdict does.
#
# The bug we hit: the model's complete/incomplete cutoff is hardcoded at
# probability > 0.5, so a borderline pause (model only 51% sure you're done)
# ends your turn mid-thought. PatientSmartTurnV3 below makes that cutoff a
# tunable knob: at 0.7 the model must be *quite* confident you've finished
# before it ends your turn — biasing toward "let him keep talking."
#
# IMPORTANT interaction: the model runs ONCE per pause (when VAD fires), and does
# NOT re-score itself as silence drags on. So if you finish but your prosody was
# ambiguous (model says 0.6 < threshold), it keeps the turn open waiting for more
# speech that never comes — and the only thing that ends it is the hard silence
# ceiling SMART_TURN_STOP_SECS. That ceiling is therefore your worst-case dead-air
# when you ARE done but sounded unsure. We lower it from the 3s default to 2s so a
# high threshold doesn't cost you a long stall. The two knobs work together:
# THRESHOLD high = don't interrupt me; STOP_SECS low = but don't stall if I'm done.
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.2"))           # silence before we ASK the model
SMART_TURN_THRESHOLD = float(os.getenv("SMART_TURN_THRESHOLD", "0.7"))  # confidence needed to END the turn
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS", "2.0"))  # hard ceiling: force-end after this much continuous silence


class PatientSmartTurnV3(LocalSmartTurnAnalyzerV3):
    """Smart Turn v3, but you choose how confident it must be to end your turn.

    Upstream hardcodes the decision at `probability > 0.5` (see local_smart_turn_v3.py).
    We keep the exact same ONNX model and audio handling, and only re-apply the
    complete/incomplete cutoff using `completion_threshold`. Higher = more patient
    (the model must be more sure you're finished), so mid-sentence pauses stay
    INCOMPLETE and your turn isn't cut off. Lower = snappier but more interruptive.
    """

    def __init__(self, *, completion_threshold: float = 0.7, **kwargs):
        super().__init__(**kwargs)
        self._completion_threshold = completion_threshold

    def _predict_endpoint(self, audio_array):
        result = super()._predict_endpoint(audio_array)  # run the model unchanged
        # Re-derive the verdict with OUR threshold instead of the hardcoded 0.5.
        result["prediction"] = 1 if result["probability"] > self._completion_threshold else 0
        # Surface every verdict so you can tune the threshold by ear: prob is how
        # sure the model is you're DONE; we end the turn only if prob > threshold.
        verdict = "END TURN" if result["prediction"] == 1 else "keep listening"
        logger.info(
            f"🛑 endpoint: prob_done={result['probability']:.2f} "
            f"(thr {self._completion_threshold:.2f}) → {verdict}"
        )
        return result


# Loaded from prompts/prompts.json by name. Change the prompt there, not in code.
# Naming convention: <milestone>_<agent>_system_prompt, and the JSON key matches the agent.
M0_LOCAL_MIC_VOICE_AGENT_SYSTEM_PROMPT = load_prompt("m0_local_mic_voice_agent")


async def main() -> None:
    nvidia_key = os.getenv("NVIDIA_API_KEY")
    deepgram_key = os.getenv("DEEPGRAM_API_KEY")
    cartesia_key = os.getenv("CARTESIA_API_KEY")
    missing = [
        name
        for name, val in [
            ("NVIDIA_API_KEY", nvidia_key),
            ("DEEPGRAM_API_KEY", deepgram_key),
            ("CARTESIA_API_KEY", cartesia_key),
        ]
        if not val or val.endswith("REPLACE_ME")
    ]
    if missing:
        raise SystemExit(
            "Missing keys in .env: " + ", ".join(missing) + "\n"
            "  NVIDIA   -> https://build.nvidia.com         (LLM)\n"
            "  Deepgram -> https://console.deepgram.com      (STT)\n"
            "  Cartesia -> https://play.cartesia.ai          (TTS)"
        )

    # 1) TRANSPORT — where audio enters and leaves. Here it's your local machine.
    #    VAD (Silero) runs on the *input* so the pipeline knows when you're speaking.
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Keep VAD responsive (short stop_secs). It only *triggers* the turn
            # decision; Smart Turn (below) makes the actual call.
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        )
    )

    # 2) STT — your speech -> text. Deepgram streaming (low-latency, telephony-grade).
    stt = DeepgramSTTService(api_key=deepgram_key)

    # 3) LLM — text -> reply text. OpenAILLMService is just an OpenAI-compatible
    #    client; we point its base_url at NVIDIA so it talks to Nemotron (the brain).
    # Sampling params from NVIDIA's playground. temperature=0.2 + top_p=0.95 keep replies
    # focused (less rambling) — good for voice. max_tokens is a *ceiling*, not a target; the
    # prompt keeps replies short, so this just bounds a worst-case runaway reply. Stream is
    # always on in Pipecat.
    #
    # CRITICAL: enable_thinking=False. We discovered this model is NOT actually non-reasoning —
    # left alone it emits silent chain-of-thought into `reasoning_content` (which Pipecat
    # ignores, so the spoken text looked clean) BEFORE producing any `content`. That hidden
    # thinking is what made the first *spoken* word slow and erratic: TTFT-to-content was
    # effectively unbounded while it "thought." Probed directly: thinking ON → content empty
    # for many tokens; thinking OFF → first content token in ~0.4-0.6s, output still clean.
    # Pipecat forwards `extra` straight into the request body (params.update(settings.extra)),
    # so this becomes extra_body={'chat_template_kwargs': {'enable_thinking': False}} on the call.
    llm = OpenAILLMService(
        api_key=nvidia_key,
        base_url="https://integrate.api.nvidia.com/v1",
        model=LLM_MODEL,
        params=OpenAILLMService.InputParams(
            temperature=0.2,
            top_p=0.95,
            max_tokens=16384,
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )
    # The free hosted endpoint intermittently STALLS for 20-30s+ (we've logged 21.4s and
    # 33.7s TTFB). Pipecat's own retry_on_timeout is useless here: it bounds only the FIRST
    # attempt, then retries with NO timeout — so the retry hung ~30s. Instead we put a HARD
    # deadline on the HTTP client itself: every request (and every SDK retry) is capped at
    # LLM_REQUEST_TIMEOUT_SECS, and the OpenAI SDK auto-retries on timeout with backoff.
    # A fresh request empirically returns in ~0.4s, so this turns a 30s hang into a few
    # seconds worst-case. (Reaching into _client is the only injection point Pipecat exposes.)
    # The real fix for the stalls is M5: self-host the Nemotron NIM on a dedicated GPU.
    llm._client = llm._client.with_options(
        timeout=LLM_REQUEST_TIMEOUT_SECS, max_retries=2
    )

    # 4) TTS — reply text -> speech audio. Cartesia Sonic: expressive, low-latency,
    #    far more natural cadence/emotion than Aura (which ignored prosody tags).
    #    speed lives inside generation_config (Sonic-3's structured guidance block);
    #    we pass it via settings= (the canonical v0.0.105+ API) rather than the
    #    deprecated params= path to avoid a deprecation warning at startup.
    tts = CartesiaTTSService(
        api_key=cartesia_key,
        voice_id=CARTESIA_VOICE_ID,
        settings=CartesiaTTSService.Settings(
            generation_config=GenerationConfig(speed=CARTESIA_SPEED)
        ),
    )

    # 5) CONTEXT — the running chat history. The aggregator pair has two halves:
    #    .user() collects finished user transcripts INTO the context (before the LLM),
    #    .assistant() collects the LLM's reply back into the context (after the LLM),
    #    so the agent remembers the conversation turn to turn.
    context = LLMContext(
        messages=[{"role": "system", "content": M0_LOCAL_MIC_VOICE_AGENT_SYSTEM_PROMPT}]
    )
    # Endpointing strategy: explicitly use our PatientSmartTurnV3 as the turn-STOP
    # detector. (Pipecat already defaults to Smart Turn v3, but we wire it
    # explicitly so the confidence threshold is OURS and it's obvious in the code
    # that semantic endpointing — not a silence timer — decides when you're done.)
    turn_strategies = UserTurnStrategies(
        stop=[
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=PatientSmartTurnV3(
                    completion_threshold=SMART_TURN_THRESHOLD,
                    params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS),
                )
            )
        ]
    )
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=turn_strategies),
    )

    # 6) PIPELINE — order matters; this *is* the flow diagram at the top of the file.
    pipeline = Pipeline(
        [
            transport.input(),     # mic audio in
            stt,                   # audio -> text
            aggregator.user(),     # add user's text to context
            llm,                   # context -> reply text (streamed)
            tts,                   # reply text -> audio (streamed)
            transport.output(),    # audio out to speaker
            aggregator.assistant() # add reply to context (for memory)
        ]
    )

    # --- METRICS -------------------------------------------------------------
    # MetricsLogObserver: per-service TTFB + processing time (📊 lines).
    # UserBotLatencyObserver: THE number that matters for voice — the gap between
    # you finishing speaking and the bot starting to speak — plus a per-stage breakdown.
    # We attach handlers to log each measurement and keep running session stats.
    metrics_obs = MetricsLogObserver()
    latency_obs = UserBotLatencyObserver()

    turn_latencies: list[float] = []  # response latency per turn, for session stats

    @latency_obs.event_handler("on_latency_measured")
    async def _on_latency(_obs, latency: float) -> None:
        turn_latencies.append(latency)
        avg = sum(turn_latencies) / len(turn_latencies)
        logger.info(
            f"⏱️  RESPONSE LATENCY (you stopped → bot speaks): {latency:.3f}s  "
            f"[turn #{len(turn_latencies)} | avg {avg:.3f}s | "
            f"min {min(turn_latencies):.3f}s | max {max(turn_latencies):.3f}s]"
        )

    @latency_obs.event_handler("on_latency_breakdown")
    async def _on_breakdown(_obs, breakdown) -> None:
        stages = " | ".join(
            f"{b.processor.split('#')[0]} {b.duration_secs:.3f}s" for b in breakdown.ttfb
        )
        logger.info(
            f"⏱️  breakdown — you spoke {breakdown.user_turn_secs:.2f}s, then: {stages}"
        )

    # A PipelineTask runs the pipeline. allow_interruptions=True lets you talk over
    # the bot (barge-in) — VAD detects your voice and cuts off its speech.
    # enable_metrics makes each service emit TTFB/processing-time, surfaced by the observers.
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,           # TTFB + processing-time per stage
            enable_usage_metrics=False,    # off: it logged token counts per chunk (spam)
            report_only_initial_ttfb=True,  # log TTFB once per turn, not per token
        ),
        observers=[metrics_obs, latency_obs],
    )

    # Kick off a greeting once the pipeline is live: an LLMRunFrame tells the LLM to
    # generate from the current context (just the system prompt) without waiting for
    # user speech. We delay slightly so the pipeline is fully running first.
    async def greet() -> None:
        await asyncio.sleep(1.0)
        await task.queue_frames([LLMRunFrame()])

    runner = PipelineRunner(handle_sigint=True)
    logger.info("Starting M0 local voice agent — speak after the greeting. Ctrl-C to quit.")
    try:
        await asyncio.gather(runner.run(task), greet())
    finally:
        # Session summary — aggregate response-latency stats over the whole conversation.
        if turn_latencies:
            s = sorted(turn_latencies)
            mean = sum(s) / len(s)
            p50 = s[len(s) // 2]
            p90 = s[min(len(s) - 1, int(0.9 * (len(s) - 1)))]
            logger.info(
                "📈 SESSION SUMMARY — response latency over {n} turns: "
                "mean {mean:.3f}s | p50 {p50:.3f}s | p90 {p90:.3f}s | "
                "min {mn:.3f}s | max {mx:.3f}s".format(
                    n=len(s), mean=mean, p50=p50, p90=p90, mn=s[0], mx=s[-1]
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
