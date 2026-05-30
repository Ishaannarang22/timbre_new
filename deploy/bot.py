"""
timbre — Pipecat Cloud entrypoint (Twilio Media Streams).

This is the CLOUD form of the agent. Unlike src/twilio_bot.py (a self-hosted FastAPI server
fronted by a cloudflared tunnel, where WE own /twiml and /ws), Pipecat Cloud hosts the
websocket. Twilio points at wss://api.pipecat.daily.co/ws/twilio and the platform invokes the
`bot(runner_args)` coroutine below with a live websocket. Works for BOTH:

  • inbound  — someone dials the Twilio number → TwiML Bin → Pipecat Cloud → bot()
  • outbound — deploy/dialout_test.py places a Twilio call whose TwiML streams to the same agent

Pipeline (unchanged identity): Deepgram STT → NVIDIA Nemotron LLM → Cartesia TTS, at 8kHz μ-law.

v1 NOTE: endpointing here is Silero VAD only. The self-hosted bot uses a patient Smart-Turn v3
prosody model; that ONNX dependency is omitted from the cloud image for a lean, reliable first
build and is a planned fast-follow.

WELLNESS SEAM: the personas below are warm placeholders. The real wellness/health-checkup system
prompt (roadmap W1) and DB-backed tools (W3) plug in at `system_prompt()` / `run_bot()`.
"""

import os
import re

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext, NOT_GIVEN
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

load_dotenv(override=True)

# --- Config (same knobs as the self-hosted bot; values come from the secret set) ----------
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"  # Brooke
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
MAX_CALL_SECS = float(os.getenv("MAX_CALL_SECS", "300"))
SR = 8000

# --- ENDPOINTING -------------------------------------------------------------------------
# VAD is just the TRIGGER: a short silence wakes the turn decision. Smart-Turn v3 (a prosody
# model) makes the real call, so the agent waits out mid-sentence pauses instead of barging in
# — important for wellness calls, where people pause to think or get emotional.
#
# In Pipecat 1.3.0 the patience knob is SmartTurnParams.confidence_threshold (NOT the old
# completion_threshold). Source: `prediction = 1 if probability >= confidence_threshold else 0`,
# where probability = "the turn is complete". So a HIGHER threshold = MORE patient (needs more
# certainty before ending the turn). Default is 0.5; we raise it to be deliberately patient.
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.4"))           # short trigger
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS", "2.5"))   # hard ceiling
SMART_TURN_CONFIDENCE = float(os.getenv("SMART_TURN_CONFIDENCE", "0.7"))  # >0.5 = more patient


class PatientSmartTurnV3(LocalSmartTurnAnalyzerV3):
    """Smart Turn v3 with a tunable "are you done?" confidence threshold.

    NOTE on the API: SmartTurnParams exposes only stop_secs / pre_speech_ms / max_duration_secs
    — there is NO confidence/threshold parameter. Upstream hardcodes the complete/incomplete
    decision at probability > 0.5, so a borderline pause (model only 51% sure you've finished)
    ends the turn mid-thought. We keep the exact ONNX model + audio handling and only re-apply
    the cutoff here: HIGHER threshold = MORE patient (the model must be quite sure you're done,
    so mid-sentence pauses stay INCOMPLETE). On a wellness call this is what stops the agent
    from talking over someone who is still gathering their thoughts. (Mirrors the self-hosted
    bot's src/turn_helpers.py.)
    """

    def __init__(self, *, completion_threshold: float = 0.7, **kwargs):
        super().__init__(**kwargs)
        self._completion_threshold = completion_threshold

    def _predict_endpoint(self, audio_array):
        result = super()._predict_endpoint(audio_array)  # run the model unchanged
        result["prediction"] = 1 if result["probability"] > self._completion_threshold else 0
        return result

INBOUND_GREETING = "Hi there, thanks for calling. How are you feeling today?"
OUTBOUND_GREETING = "Hi, this is a follow-up call from your care team, just checking in on how you've been since your visit. How are you feeling?"

# Genuine sign-offs (matched on the agent's COMPLETED turn → auto-hangup).
GOODBYE_RE = re.compile(
    r"\b(good ?bye|bye now|bye bye|talk (to you )?soon|"
    r"take care(\s*,?\s*\w+)?\s*[.!]|"
    r"have a (great|wonderful|good) (day|one)\b)",
    re.IGNORECASE,
)
GOODBYE_TAIL_RE = re.compile(r"(good ?bye|bye)\s*[.!]*\s*$", re.IGNORECASE)


def system_prompt() -> str:
    # WELLNESS SEAM (W1): replace this with the real health-checkup persona + context.
    return (
        "You are a warm, attentive wellness companion on a short phone call. Everything you say "
        "is spoken aloud over a phone, so keep every turn to 1-3 short, natural sentences. No "
        "markdown, no lists, no emoji, no stage directions.\n\n"
        "The call is ALREADY in progress. You have already greeted the person and asked how they "
        "are feeling. Respond ONLY to their most recent message. Do NOT greet them again or repeat "
        "yourself. Gently check in on how they're doing — their mood, sleep, energy, anything on "
        "their mind. Be supportive and unhurried; you offer wellness check-ins and encouragement, "
        "not diagnosis. When they're done, warmly say a clear goodbye."
    )


def detect_goodbye(text: str) -> bool:
    return bool(GOODBYE_RE.search(text) or GOODBYE_TAIL_RE.search(text))


class GoodbyeProcessor(FrameProcessor):
    """Ends the call shortly after the AGENT says a genuine goodbye, but only after at least one
    real generated exchange (so a stray farewell-ish phrase in the first reply can't drop the
    call). Sits after the LLM; queues an EndFrame once the farewell has finished playing — the
    Twilio serializer's auto_hang_up then drops the real call. MAX_CALL_SECS is the backstop."""

    def __init__(self):
        super().__init__()
        self.task: PipelineTask | None = None
        self._buffer = ""
        self._turns = 0
        self._armed = False
        self._ending = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self._buffer += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = self._buffer.strip()
            self._buffer = ""
            if text:
                self._turns += 1
                if not self._armed and self._turns >= 1 and detect_goodbye(text):
                    logger.info(f"genuine goodbye detected (turn {self._turns}) -> will hang up: {text!r}")
                    self._armed = True
        elif (
            isinstance(frame, BotStoppedSpeakingFrame)
            and self._armed
            and not self._ending
            and self.task is not None
        ):
            self._ending = True
            logger.info("farewell finished speaking -> queueing EndFrame (auto hang up)")
            await self.task.queue_frame(EndFrame())
        await self.push_frame(frame, direction)


async def run_bot(transport: FastAPIWebsocketTransport, handle_sigint: bool, greeting: str):
    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"], sample_rate=SR)
    llm = OpenAILLMService(
        api_key=os.environ["NVIDIA_API_KEY"],
        base_url="https://integrate.api.nvidia.com/v1",
        model=LLM_MODEL,
        params=OpenAILLMService.InputParams(
            temperature=0.3,
            top_p=0.95,
            max_tokens=4096,
            # Nemotron-nano secretly reasons into reasoning_content → slow first word; disable it.
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )
    # Cap the free endpoint's documented tail latency; the SDK retries fresh.
    llm._client = llm._client.with_options(timeout=8.0, max_retries=2)
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice_id=CARTESIA_VOICE_ID,
        sample_rate=SR,
        settings=CartesiaTTSService.Settings(
            generation_config=GenerationConfig(speed=CARTESIA_SPEED)
        ),
    )

    # Seed the context so the model's history shows it has ALREADY greeted (the greeting is
    # spoken once as a fixed line below). Paired with the prompt's "don't greet again", the
    # model just continues the conversation instead of restarting the intro on interruptions.
    context = LLMContext(
        messages=[
            {"role": "system", "content": system_prompt()},
            {"role": "assistant", "content": greeting},
        ],
        tools=NOT_GIVEN,
    )

    # Patient endpointing: a prosody model (not a silence timer) decides when the caller is
    # done. NOTE: each cloud call builds a fresh analyzer, which loads the Smart-Turn v3 ONNX
    # model (~0.5-1s on first turn). The self-hosted bot warm-loads it once at process startup;
    # there's no equivalent cross-call warm cache in a per-call cloud worker, so we accept the
    # one-time per-call load here.
    turn_analyzer = PatientSmartTurnV3(
        completion_threshold=SMART_TURN_CONFIDENCE,
        params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS),
    )
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=turn_analyzer)]
            )
        ),
    )

    goodbye = GoodbyeProcessor()
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregator.user(),
            llm,
            goodbye,
            tts,
            transport.output(),
            aggregator.assistant(),
        ]
    )
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=SR,
            audio_out_sample_rate=SR,
        ),
    )
    goodbye.task = task

    @transport.event_handler("on_client_connected")
    async def _greet(_t, _c):
        # Speak the opening as a FIXED line exactly once (not an LLM run), so an interruption
        # can't trigger a regeneration that loops the intro. Already in context above, so we
        # don't append it a second time.
        await task.queue_frames([TTSSpeakFrame(greeting, append_to_context=False)])

    @transport.event_handler("on_client_disconnected")
    async def _bye(_t, _c):
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Pipecat Cloud entrypoint. The platform hands us a live Twilio Media Streams websocket."""
    _transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)

    # Custom <Parameter>s from the TwiML land in call_data["body"]. Our outbound dialout TwiML
    # sets direction=outbound; inbound calls carry nothing, so we default to the inbound persona.
    body = call_data.get("body", {}) or {}
    direction = str(body.get("direction", "inbound")).lower()
    greeting = OUTBOUND_GREETING if direction == "outbound" else INBOUND_GREETING
    logger.info(f"bot() starting — direction={direction} call_sid={call_data.get('call_id')}")

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),  # lets Pipecat hang up at the end
    )
    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=SR,
            audio_out_sample_rate=SR,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
            serializer=serializer,
        ),
    )
    await run_bot(transport, runner_args.handle_sigint, greeting)


if __name__ == "__main__":
    # Local dev: `python bot.py --transport twilio` runs Pipecat's dev runner (FastAPI on :7860).
    from pipecat.runner.run import main

    main()
