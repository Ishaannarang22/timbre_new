"""
Postpartum voice agent over Twilio Media Streams.

This is the postpartum cousin of `twilio_bot.py`. Same Pipecat pipeline shape —
Twilio audio ↔ Deepgram STT ↔ Nemotron LLM ↔ Cartesia TTS — but the LLM is
driven by a Pipecat Flows `FlowManager` running the 7-node postpartum
NodeConfig graph defined in `src/flows/postpartum.py`. All node answers POST
to the timbre_dashboard `/api/v1/*` routes in real time.

We deliberately do NOT modify `twilio_bot.py`: the morning-quote bot must keep
working unchanged for the 7 AM call. Endpointing (Smart Turn v3) and the
preloaded ONNX session are reused via `build_turn_analyzer()`.

Flow lifecycle (one call):
  /twiml  ─►  mint per-call ws token, pre-fetch call queue if no patient_id
  /ws     ─►  hand off audio to Pipecat, fetch patient profile, POST /calls,
              construct FlowManager, initialize at identity_verify, seed greeting
  …        ─►  every node transition PATCHes /calls/{id} with current_node
  hangup  ─►  PATCH /calls/{id} status=completed + transcript_redacted
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat_flows import FlowManager

load_dotenv()

# Patient endpointing — prosody-based "is the caller done?" model. The PRD
# suggests reusing twilio_bot.build_turn_analyzer() to avoid double-loading
# the ONNX session, but that helper's __new__/replay trick references
# `_feature_extractor` which is no longer an attribute on current Pipecat's
# LocalSmartTurnAnalyzerV3. To stay within the PRD's "do not touch
# twilio_bot.py" rule we construct a fresh PatientSmartTurnV3 per call here;
# the per-call ONNX session load is in C++ and only blocks ~0.5s, which is
# acceptable for a postpartum check-in that runs minutes per call.
from turn_helpers import PatientSmartTurnV3  # noqa: E402
from dashboard_client import build_dashboard_client, redact  # noqa: E402
from flows.postpartum import (  # noqa: E402
    build_global_functions,
    initial_node,
    set_flow_context,
)

# ---------------------------------------------------------------------------
# Config (env-driven, mirrors twilio_bot.py where it matters)
# ---------------------------------------------------------------------------
CARTESIA_VOICE_ID = (
    os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"
)  # Brooke
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
SR = 8000  # 8 kHz μ-law, end-to-end
# Postpartum calls are LONGER than the 150s morning quote. 15 minutes is the
# documented backstop in the PRD; nothing in the flow should run that long but
# Pipecat stalls do happen, so we keep a safety net.
MAX_CALL_SECS = float(os.getenv("POSTPARTUM_MAX_CALL_SECS", "900"))

VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.4"))
SMART_TURN_THRESHOLD = float(os.getenv("SMART_TURN_THRESHOLD", "0.7"))
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS", "2.5"))


def build_turn_analyzer() -> PatientSmartTurnV3:
    """Fresh PatientSmartTurnV3 for one call. Constructs a new ONNX session;
    safe to call inside /ws. See the module-level note for why we don't reuse
    twilio_bot's warm-replay helper."""
    return PatientSmartTurnV3(
        completion_threshold=SMART_TURN_THRESHOLD,
        params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS),
    )

# ---------------------------------------------------------------------------
# Per-call state (token store, queued patient lookup cache)
# ---------------------------------------------------------------------------
WS_TOKENS: dict[str, tuple[str, float]] = {}
TWIML_PATIENTS: dict[str, tuple[str | None, float]] = {}  # CallSid -> patient_id
TOKEN_TTL_SECS = float(os.getenv("POSTPARTUM_TOKEN_TTL", "600"))


def _evict_stale(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    for store in (WS_TOKENS, TWIML_PATIENTS):
        stale = [k for k, (_v, ts) in store.items() if now - ts > TOKEN_TTL_SECS]
        for k in stale:
            store.pop(k, None)


app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.api_route("/twiml", methods=["GET", "POST"])
async def twiml(request: Request) -> PlainTextResponse:
    """Twilio fetches this on call connect. We respond with <Connect><Stream>
    pointed at /ws, embedding the per-call token + patient_id as <Parameter>s
    (Twilio echoes them in the start frame's customParameters)."""
    try:
        form = await request.form()
        call_sid = form.get("CallSid") or request.query_params.get("CallSid") or "unknown"
    except Exception:  # noqa: BLE001
        call_sid = request.query_params.get("CallSid", "unknown")

    # Patient routing: explicit ?patient_id wins; otherwise pop the head of the
    # dashboard queue (handy for the demo dialer). If the queue is empty or the
    # dashboard is down we let /ws fail loudly — there's no useful default.
    patient_id = request.query_params.get("patient_id")
    if not patient_id:
        try:
            client = build_dashboard_client()
            queue = await client.get_call_queue()
            if hasattr(client, "aclose"):
                await client.aclose()
            for row in queue or []:
                if row.get("status") in {"queued", "in_progress"}:
                    patient_id = row.get("patient_id")
                    break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"call-queue lookup failed at /twiml: {e}")

    _evict_stale()
    now = time.monotonic()
    token = secrets.token_urlsafe(24)
    WS_TOKENS[call_sid] = (token, now)
    TWIML_PATIENTS[call_sid] = (patient_id, now)

    host = request.headers.get("host")
    ws_url = f"wss://{host}/ws"
    logger.info(
        f"/twiml call_sid={call_sid} patient_id={patient_id} -> stream {ws_url}"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Connect><Stream url="{ws_url}">'
        f'<Parameter name="token" value="{token}"/>'
        f'<Parameter name="patient_id" value="{patient_id or ""}"/>'
        "</Stream></Connect></Response>"
    )
    return PlainTextResponse(xml, media_type="application/xml")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()

    # Twilio handshake: 'connected' then 'start'.
    try:
        await websocket.receive_text()
        start = json.loads(await websocket.receive_text())
        stream_sid = start["start"]["streamSid"]
        call_sid = start["start"]["callSid"]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"/ws closed before start handshake: {type(e).__name__}: {e}")
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
        return

    # Token + patient_id, dual-channel (customParameters first, ws query fallback).
    expected = WS_TOKENS.pop(call_sid, (None, 0.0))[0]
    cp = (start.get("start", {}).get("customParameters") or {}) if isinstance(
        start.get("start"), dict
    ) else {}
    qs_token = websocket.query_params.get("token")
    cp_token = cp.get("token")
    if expected is not None:
        ok = any(
            t is not None and secrets.compare_digest(t, expected)
            for t in (qs_token, cp_token)
        )
        if not ok:
            logger.warning(f"/ws rejected: bad/missing token for call_sid={call_sid}")
            try:
                await websocket.close(code=1008)
            except Exception:  # noqa: BLE001
                pass
            return

    patient_id = (
        cp.get("patient_id")
        or websocket.query_params.get("patient_id")
        or (TWIML_PATIENTS.pop(call_sid, (None, 0.0))[0])
    )
    if not patient_id:
        logger.error(f"/ws no patient_id for call_sid={call_sid}; closing")
        try:
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001
            pass
        return

    # Dashboard handshake: fetch profile + POST /calls so we have call_id.
    # Every dashboard read/write is best-effort; if the dashboard is unreachable
    # we still take the call (with a fallback profile) so the voice path doesn't
    # die. The PRD lists this explicitly as a hard requirement: "Tolerate
    # DASHBOARD_API_URL being unset during early dev — log a warning and no-op,
    # never crash the call."
    dashboard = build_dashboard_client()
    try:
        profile = await dashboard.get_patient_profile(patient_id) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_patient_profile({patient_id}) failed; using fallback: {e}")
        profile = {}
    patient = profile.get("patient") or {
        "id": patient_id,
        "preferred_name": "there",
        "language": "en",
    }
    newborns = profile.get("newborns") or []
    newborn = newborns[0] if newborns else None
    language = (patient.get("language") or "en").lower()
    if language != "es":
        language = "en"

    try:
        call_row = await dashboard.start_call(
            patient_id,
            call_sid=call_sid,
            direction="outbound",
            language=language,
            flow_name="postpartum_v1",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"start_call failed; using placeholder call_id: {e}")
        call_row = None
    call_id = call_row.get("id") if isinstance(call_row, dict) else None
    if not call_id:
        # Dashboard might be in no-op stub mode or unreachable. Use a placeholder
        # so handlers can still log; downstream PATCHes will 404 and be swallowed.
        call_id = f"local-{secrets.token_hex(6)}"
        logger.info(f"start_call returned no id; using placeholder {call_id}")

    preferred = patient.get("preferred_name") or patient.get("name", "there").split()[0]
    if language == "es":
        greeting = (
            f"Hola {preferred}, soy Maya de Raya Memorial llamando para ver cómo "
            "estás tú y el bebé. ¿Es un buen momento?"
        )
    else:
        greeting = (
            f"Hi {preferred}, this is Maya from Raya Memorial calling to check in "
            "on you and the baby. Is this a good time?"
        )

    logger.info(
        f"/ws connected stream_sid={stream_sid} call_sid={call_sid} "
        f"patient_id={patient_id} language={language} call_id={call_id}"
    )

    # --- Pipecat pipeline (mirrors twilio_bot.py shape) -------------------
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.environ["TWILIO_ACCOUNT_SID"],
        auth_token=os.environ["TWILIO_AUTH_TOKEN"],
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
            temperature=0.2,
            top_p=0.95,
            max_tokens=4096,
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )
    llm._client = llm._client.with_options(timeout=8.0, max_retries=2)
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice_id=CARTESIA_VOICE_ID,
        sample_rate=SR,
        settings=CartesiaTTSService.Settings(
            generation_config=GenerationConfig(speed=CARTESIA_SPEED)
        ),
    )

    # Pipecat Flows owns the LLM context across node transitions, so we hand
    # it an empty LLMContext and let FlowManager.initialize set up the first
    # node's system + tool schema.
    context = LLMContext()
    turn_strategies = UserTurnStrategies(
        stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=build_turn_analyzer())]
    )
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=turn_strategies),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregator.user(),
            llm,
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

    # FlowManager wires the LLM + context aggregator + transport together, and
    # registers our 7 globals so they're available in every node.
    flow_manager = FlowManager(
        worker=task,
        llm=llm,
        context_aggregator=aggregator,
        transport=transport,
        global_functions=build_global_functions(),
    )
    set_flow_context(
        flow_manager,
        client=dashboard,
        patient=patient,
        newborn=newborn,
        call_id=call_id,
        language=language,
    )

    # Greet the moment audio is live (mirrors twilio_bot.py). After the
    # greeting frame we initialize FlowManager at identity_verify; from there
    # the model + Flows take over.
    @transport.event_handler("on_client_connected")
    async def _greet(_t, _c):
        await asyncio.sleep(0.3)
        await task.queue_frames([TTSSpeakFrame(greeting, append_to_context=True)])
        await flow_manager.initialize(initial_node(flow_manager))

    # The transcript buffer is appended to by the assistant aggregator; we
    # never read raw frames here. Instead, on disconnect we read the context
    # snapshot, redact, and PATCH the call row to completed.
    @transport.event_handler("on_client_disconnected")
    async def _bye(_t, _c):
        try:
            messages = flow_manager.get_current_context() or []
            transcript = "\n".join(
                f"{m.get('role','?')}: {m.get('content','')}"
                for m in messages
                if isinstance(m, dict) and m.get("content")
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"transcript dump failed: {e}")
            transcript = ""
        try:
            await dashboard.update_call(
                call_id,
                status="completed",
                ended_at=__import__("datetime")
                .datetime.utcnow()
                .isoformat(timespec="seconds")
                + "Z",
                transcript_redacted=redact(transcript)[:200_000],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"final update_call failed: {e}")
        if hasattr(dashboard, "aclose"):
            try:
                await dashboard.aclose()
            except Exception:  # noqa: BLE001
                pass
        await task.cancel()

    async def _max_duration_guard() -> None:
        await asyncio.sleep(MAX_CALL_SECS)
        logger.info("postpartum max call duration reached — ending call")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=False)
    guard = asyncio.create_task(_max_duration_guard())
    try:
        await runner.run(task)
    finally:
        guard.cancel()
