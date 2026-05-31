"""
The REAL voice engine over the phone (Twilio Media Streams + Pipecat).

This is the project's own pipeline — the same brain/ears/voice as m0_local_bot.py — but
the transport is a Twilio phone call instead of your laptop mic:

    Twilio call ─► /twiml (<Connect><Stream>) ─► wss://…/ws ─┐
       caller audio (8k μ-law) ─► Deepgram STT ─► Nemotron LLM ─► Cartesia TTS ─► caller
                                  (ears)          (brain)          (OUR voice: Brooke)

Twilio does NONE of the speaking here — every spoken word is Cartesia Sonic (CARTESIA_VOICE_ID),
exactly the voice configured in .env.

This is a PURE voice pipeline: STT → LLM → TTS. The earlier Mac-control harness (the tool
registry, the GLM tool factory, per-call confirmation, caller authorization, and cross-call
memory) has been removed — this agent talks, it does not operate the computer. Database-backed
tools for the wellness/health-checkup direction will be added back deliberately, later.

The persona is a warm, concise phone companion: someone dials the number and the agent picks
up and chats. Launched behind a cloudflared tunnel (Twilio needs a public wss URL).
"""

import asyncio
import json
import os
import re
import secrets
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from loguru import logger

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
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

load_dotenv()

from turn_helpers import PatientSmartTurnV3  # noqa: E402  (shared patient endpointer)

# --- The voice. THIS is "our voice" — Cartesia Sonic, same id as the local bot. --------
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"  # Brooke
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
TARGET_NAME = os.getenv("TARGET_NAME", "Ishaan")
# Hard cap so an unattended call can't run forever ("a couple of minutes"). This is a
# BACKSTOP only — the call normally ends itself when the agent says goodbye.
MAX_CALL_SECS = float(os.getenv("MAX_CALL_SECS", "150"))
# Telephony audio is 8 kHz μ-law; run the whole pipeline at 8k so nothing has to guess.
SR = 8000

# The opening line, spoken VERBATIM and exactly ONCE. See BUG 1 fix below: we no longer
# let the LLM *generate* the open (an LLMRunFrame re-runs on every interruption and loops
# the intro). A fixed TTSSpeakFrame can't be regenerated, so the call can only move forward.
GREETING = f"Hey {TARGET_NAME}! What can I do for you?"


def _normalize_e164(num: str | None) -> str:
    """Normalize a phone number to DIGITS ONLY — drop the '+', spaces, and punctuation so
    '+1 (415) 555-0100', '+14155550100', and '14155550100' all compare equal. Kept as a small
    utility for logging/identifying the caller; no longer gates any capability."""
    if not num:
        return ""
    return re.sub(r"\D", "", str(num))


# --- ENDPOINTING on the phone -------------------------------------------------
# A short VAD pause only *triggers* the turn decision; PatientSmartTurnV3 (a prosody
# model) makes the real call, so the agent waits out mid-sentence pauses instead of
# barging in. THRESHOLD high = "don't interrupt me"; STOP_SECS = hard ceiling so we
# still respond promptly when he's genuinely done. Mirrors the local-mic bot's tuning,
# nudged a touch more conservative for telephony jitter.
VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.4"))
SMART_TURN_THRESHOLD = float(os.getenv("SMART_TURN_THRESHOLD", "0.7"))
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS", "2.5"))

# P0-3: phrases that mark the AGENT's turn as a GENUINE closing. Matched on the
# assistant's completed message once the LLM finishes (see GoodbyeProcessor). Tightened
# toward real sign-offs: a bare "bye" plus the warm wrap-ups the prompt asks for. We
# deliberately DROP loose matches like "have a good one" mid-sentence and only arm AFTER
# at least one completed assistant turn, so a stray farewell-ish phrase (e.g. "take care
# of yourself" said mid-chat) can't hang up early.
GOODBYE_RE = re.compile(
    r"\b(good ?bye|bye now|bye bye|talk (to you )?soon|"
    r"take care(\s*,?\s*\w+)?\s*[.!]|"
    r"have a (great|wonderful|good) (day|one)\b)",
    re.IGNORECASE,
)
# A plain trailing "bye" / "goodbye" at the very end of the turn also counts as a close.
GOODBYE_TAIL_RE = re.compile(r"(good ?bye|bye)\s*[.!]*\s*$", re.IGNORECASE)

# P0-3: minimum completed assistant turns before a farewell may arm the hangup. The
# greeting is turn 0 (seeded, not generated); we require at least this many GENERATED
# turns so a stray farewell-ish phrase in the model's very first reply can't drop the
# call instantly.
MIN_ASSISTANT_TURNS_BEFORE_GOODBYE = int(os.getenv("MIN_GOODBYE_TURNS", "1"))

app = FastAPI()

# Per-call /ws auth tokens, keyed by CallSid. Values are (token, created_monotonic) so a
# call that's set up at /twiml but never reaches /ws (caller never answers) can be evicted
# instead of leaking forever.
WS_TOKENS: dict[str, tuple[str, float]] = {}
# How long a minted token may sit unclaimed before we evict it, and a hard cap on dict
# size as a belt-and-suspenders against a flood of /twiml hits.
TOKEN_TTL_SECS = float(os.getenv("TOKEN_TTL_SECS", "600"))  # 10 min
TOKEN_MAX_ENTRIES = int(os.getenv("TOKEN_MAX_ENTRIES", "256"))


def _evict_stale(now: float | None = None) -> None:
    """P1-4: drop token entries older than the TTL, and hard-cap dict size.

    Called on every /twiml so abandoned calls (no matching /ws) can't grow WS_TOKENS
    without bound. Cheap: a single pass over what is normally a tiny dict.
    """
    now = time.monotonic() if now is None else now
    stale = [k for k, (_v, ts) in WS_TOKENS.items() if now - ts > TOKEN_TTL_SECS]
    for k in stale:
        WS_TOKENS.pop(k, None)
    # Hard size cap: if still oversized, evict oldest first.
    if len(WS_TOKENS) > TOKEN_MAX_ENTRIES:
        for k, _ in sorted(WS_TOKENS.items(), key=lambda kv: kv[1][1])[
            : len(WS_TOKENS) - TOKEN_MAX_ENTRIES
        ]:
            WS_TOKENS.pop(k, None)
    if stale:
        logger.info(f"evicted {len(stale)} stale token entries")


def build_turn_analyzer() -> "PatientSmartTurnV3":
    """Construct a FRESH PatientSmartTurnV3 (and its own ONNX session) for one call.

    We deliberately do NOT reuse a shared "warm" instance via a __new__/attribute-graft
    trick anymore: that trick referenced `_feature_extractor`, which is no longer an
    attribute on current Pipecat's LocalSmartTurnAnalyzerV3 — so it raised
    `AttributeError` inside /ws and dropped every inbound call immediately. Building a new
    analyzer per call sidesteps that entirely; the ONNX load is in C++ and only blocks
    ~0.5s, which is fine for a phone call. (postpartum_bot.py does the same — for the same
    reason.)"""
    return PatientSmartTurnV3(
        completion_threshold=SMART_TURN_THRESHOLD,
        params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS),
    )


def system_prompt() -> str:
    # The companion persona: someone dialed the number and the agent picked up. A warm,
    # concise phone companion. The opening line (GREETING) is spoken deterministically once
    # and seeded as the assistant's first turn, so the model must NOT greet again.
    #
    # BUG 1 fix: this prompt must NOT instruct the model to greet or open the call. The
    # opening line is spoken once (GREETING) and inserted into the context as the assistant's
    # first turn, so the model's view of history is that it has ALREADY greeted. Telling it to
    # greet here is exactly what made every regeneration (each barge-in while the caller talked
    # over the open) restart the intro.
    return (
        f"You are {TARGET_NAME}'s warm, concise personal phone companion. He just CALLED you. "
        "Everything you say is spoken aloud over a phone, so keep every turn to 1-3 short, natural "
        "sentences. No markdown, no lists, no emoji, no stage directions.\n\n"
        f"The call is ALREADY in progress. You have already greeted {TARGET_NAME} and asked what "
        "he needs. Respond ONLY to his most recent message. Do NOT greet him again, do NOT say hi, "
        "hello, or hey again, and do NOT repeat anything you have already said. Talk with him "
        "naturally and warmly. When he's done and says goodbye, warmly say a clear goodbye back."
    )


def detect_goodbye(text: str) -> bool:
    """P0-3: True only for a GENUINE closing in the agent's turn. Pulled out as a free
    function so it can be unit-tested offline."""
    cleaned = text.strip()
    return bool(GOODBYE_RE.search(cleaned) or GOODBYE_TAIL_RE.search(cleaned))


class GoodbyeProcessor(FrameProcessor):
    """P0-3 fix: end the call shortly after the AGENT says a GENUINE goodbye — but only once
    at least one real exchange has happened, so a stray farewell-ish phrase can't hang up
    mid-conversation.

    This processor sits AFTER the LLM in the pipeline and watches the agent's own output:

      - It accumulates the streamed assistant text (LLMTextFrame) for the current turn.
      - On LLMFullResponseEndFrame (the turn is complete) it:
          * counts the completed assistant turn,
          * and only ARMS the hangup if at least MIN_ASSISTANT_TURNS_BEFORE_GOODBYE
            completed turns have happened AND the turn text matches a genuine closing.
      - On the next BotStoppedSpeakingFrame (the farewell has finished playing out of the
        TTS) it queues an EndFrame. The Twilio serializer's auto_hang_up then drops the real
        phone call. MAX_CALL_SECS stays as a backstop.

    We match on the *completed* turn (not mid-stream) and wait for bot-stopped-speaking so we
    never cut the agent off mid-farewell.
    """

    def __init__(self):
        super().__init__()
        self.task: PipelineTask | None = None  # set after the task is created
        self._buffer = ""          # assistant text for the in-flight turn
        self._turns = 0            # completed GENERATED assistant turns so far
        self._armed = False        # farewell detected, waiting for speech to finish
        self._ending = False       # EndFrame already queued (one-shot)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            self._buffer += frame.text
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = self._buffer.strip()
            self._buffer = ""
            if text:
                self._turns += 1
                # Require at least one completed generated exchange so a stray farewell-ish
                # phrase in the model's very first reply can't drop the call instantly.
                gate_ok = self._turns >= MIN_ASSISTANT_TURNS_BEFORE_GOODBYE
                if not self._armed and gate_ok and detect_goodbye(text):
                    logger.info(
                        f"genuine goodbye detected (turn {self._turns}) -> will hang up: {text!r}"
                    )
                    self._armed = True
                elif not self._armed and detect_goodbye(text):
                    logger.info(
                        f"farewell-ish phrase ignored (gate not met: turns={self._turns}): {text!r}"
                    )
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


@app.on_event("startup")
async def _preload_models() -> None:
    # Warm the Smart Turn v3 model once at startup (pull the ONNX file into OS cache and
    # initialize onnxruntime/torch) so the FIRST call's per-call construction is fast. We
    # discard the instance — each call builds its own (see build_turn_analyzer).
    logger.info("warming Smart Turn v3 model at startup…")
    build_turn_analyzer()
    logger.info("Smart Turn v3 model warm")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request) -> PlainTextResponse:
    """Twilio fetches this when the call connects. We hand it back a
    <Connect><Stream> pointed at our websocket, so the call's audio is bridged to Pipecat."""
    # Twilio POSTs form-encoded data; fall back to query params so a missing
    # python-multipart (or a GET-configured webhook) can't 500 the webhook.
    try:
        form = await request.form()
        call_sid = form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    except Exception:  # noqa: BLE001
        call_sid = request.query_params.get("CallSid", "unknown")
    # P1-4: evict abandoned entries first so WS_TOKENS can't grow unbounded.
    _evict_stale()
    now = time.monotonic()
    # Security: mint a per-call token, store it server-side keyed by CallSid, and embed it
    # in the ws URL. /ws rejects any connection whose token doesn't match — so a stranger
    # who finds the public wss endpoint can't open a session and burn STT/LLM/TTS credits.
    token = secrets.token_urlsafe(24)
    WS_TOKENS[call_sid] = (token, now)
    host = request.headers.get("host")
    # Per Twilio docs, <Stream url> does NOT support query-string parameters — they're
    # dropped on the wss upgrade, and a raw "&" between two params is also invalid XML.
    # So the URL is clean, and the per-call token is passed via a <Parameter> child, which
    # Twilio echoes in the 'start' event's start.customParameters — the officially-supported
    # channel that /ws reads.
    # https://www.twilio.com/docs/voice/twiml/stream#custom-parameters
    ws_url = f"wss://{host}/ws"
    logger.info(f"/twiml call_sid={call_sid} -> stream {ws_url} (token via <Parameter>)")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url=\"{ws_url}\">"
        f"<Parameter name=\"token\" value=\"{token}\"/>"
        "</Stream></Connect></Response>"
    )
    return PlainTextResponse(xml, media_type="application/xml")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    # P1-4: harden startup. The caller may not answer, or may hang up before Twilio sends
    # the 'start' frame; receive_text() then raises (disconnect) or returns junk. Wrap both
    # reads so a dropped/early call logs and returns cleanly instead of throwing a 500.
    try:
        await websocket.receive_text()  # 'connected'
        start = json.loads(await websocket.receive_text())  # 'start'
        stream_sid = start["start"]["streamSid"]
        call_sid = start["start"]["callSid"]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"/ws closed before start handshake completed: {type(e).__name__}: {e}")
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
        return

    # Security: per-call token, DUAL-CHANNEL + fail-safe. /twiml minted a token for this
    # CallSid and delivered it BOTH on the ws URL query string AND as a <Stream><Parameter>
    # (which Twilio echoes in start.start.customParameters). We accept a match from EITHER
    # channel, so even if the query string were dropped on the wss upgrade, the real call
    # still passes (customParameters always carries it). We reject (close 1008) ONLY when a
    # token was minted for this CallSid and NEITHER channel presents a matching value — that
    # blocks a no-token scanner without risking a real call.
    expected = WS_TOKENS.pop(call_sid, (None, 0.0))[0]
    if expected is not None:
        qs_token = websocket.query_params.get("token")
        try:
            cp_token = start["start"].get("customParameters", {}).get("token")
        except (AttributeError, TypeError):
            cp_token = None
        ok = any(
            t is not None and secrets.compare_digest(t, expected)
            for t in (qs_token, cp_token)
        )
        if not ok:
            logger.warning(f"/ws rejected: bad/missing token for call_sid={call_sid}")
            try:
                await websocket.close(code=1008)  # policy violation
            except Exception:  # noqa: BLE001
                pass
            return

    logger.info(f"/ws connected stream_sid={stream_sid} call_sid={call_sid}")

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.environ["TWILIO_ACCOUNT_SID"],
        auth_token=os.environ["TWILIO_AUTH_TOKEN"],  # lets Pipecat hang the call up at the end
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
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

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"], sample_rate=SR)
    llm = OpenAILLMService(
        api_key=os.environ["NVIDIA_API_KEY"],
        base_url="https://integrate.api.nvidia.com/v1",
        model=LLM_MODEL,
        params=OpenAILLMService.InputParams(
            # P2-3: match the validated m0_local_bot.py (0.2) — a focused companion that
            # responds directly to the caller wants focus, not creativity. Lower temp also
            # reinforces the "don't re-greet" instruction (see dry-run).
            temperature=0.2,
            top_p=0.95,
            max_tokens=4096,
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )
    # Cap the free endpoint's tail latency (documented to stall 20-30s); SDK retries fresh.
    llm._client = llm._client.with_options(timeout=8.0, max_retries=2)
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice_id=CARTESIA_VOICE_ID,
        sample_rate=SR,
        settings=CartesiaTTSService.Settings(
            generation_config=GenerationConfig(speed=CARTESIA_SPEED)
        ),
    )

    # --- Build the system prompt (pure voice; no tools) ----------------------------------
    # This is a talk-only agent: no tools are offered. NOT_GIVEN (not None) is how pipecat's
    # universal LLMContext expresses "no tools" — passing None raises TypeError.
    prompt = system_prompt()

    # BUG 1 fix: seed the context so the model's history shows it has ALREADY greeted.
    # The exact GREETING goes in as the assistant's first turn — paired with the system
    # prompt's "never greet again", the model has nothing to restart and just continues.
    seeded_messages: list[dict] = [
        {"role": "system", "content": prompt},
        {"role": "assistant", "content": GREETING},
    ]
    context = LLMContext(messages=seeded_messages, tools=NOT_GIVEN)

    # Patient endpointing: a prosody model (not a silence timer) decides when the caller is
    # done, so the agent waits him out instead of cutting him off or dead-airing.
    turn_strategies = UserTurnStrategies(
        stop=[
            # P1-3: reuse the preloaded ONNX model (no blocking reload inside the call).
            TurnAnalyzerUserTurnStopStrategy(turn_analyzer=build_turn_analyzer())
        ]
    )
    # user_params wires the strategy into the user half; the user aggregator still feeds
    # finished caller transcripts into the LLM exactly as before.
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=turn_strategies),
    )

    goodbye = GoodbyeProcessor()  # watches the agent's completed turn for a farewell
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
        params=PipelineParams(allow_interruptions=True, audio_in_sample_rate=SR, audio_out_sample_rate=SR),
    )
    goodbye.task = task  # hand the processor the task so it can queue EndFrame on goodbye

    # BUG 1 fix: speak the opening as a FIXED line exactly once — no LLMRunFrame, so an
    # interruption can never trigger a regeneration that loops the intro. We hold off until
    # the caller's audio stream is live (on_client_connected) so the first word isn't lost.
    @transport.event_handler("on_client_connected")
    async def _greet(_t, _c):
        await asyncio.sleep(0.3)
        # append_to_context=False: the greeting is already in the context above, so we don't
        # want the assistant aggregator to add it a SECOND time.
        await task.queue_frames([TTSSpeakFrame(GREETING, append_to_context=False)])

    @transport.event_handler("on_client_disconnected")
    async def _bye(_t, _c):
        await task.cancel()

    async def _max_duration_guard():
        await asyncio.sleep(MAX_CALL_SECS)
        # P1-2: avoid a double EndFrame. If the goodbye flow already armed/queued the hangup,
        # the call is already finishing — don't pile on a second EndFrame.
        if goodbye._armed or goodbye._ending:
            logger.info("max call duration reached but goodbye already ending the call — skip")
            return
        logger.info("max call duration reached — ending call")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=False)
    guard = asyncio.create_task(_max_duration_guard())
    try:
        await runner.run(task)
    finally:
        guard.cancel()
