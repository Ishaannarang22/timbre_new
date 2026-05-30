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
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
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

load_dotenv(override=True)

# --- Config (same knobs as the self-hosted bot; values come from the secret set) ----------
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"  # Brooke
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
MAX_CALL_SECS = float(os.getenv("MAX_CALL_SECS", "300"))
SR = 8000

INBOUND_GREETING = "Hi there, thanks for calling. How are you feeling today?"
OUTBOUND_GREETING = "Hi, this is your wellness companion checking in. How are you feeling today?"

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
    aggregator = LLMContextAggregatorPair(context)

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
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
            serializer=serializer,
        ),
    )
    await run_bot(transport, runner_args.handle_sigint, greeting)


if __name__ == "__main__":
    # Local dev: `python bot.py --transport twilio` runs Pipecat's dev runner (FastAPI on :7860).
    from pipecat.runner.run import main

    main()
