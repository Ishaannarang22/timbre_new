"""
timbre — Pipecat Cloud entrypoint (Twilio Media Streams) running the POSTPARTUM FLOW.

Unlike a self-hosted FastAPI server fronted by a cloudflared tunnel, Pipecat Cloud hosts the
websocket: Twilio points at wss://api.pipecat.daily.co/ws/twilio and the platform invokes the
`bot(runner_args)` coroutine below with a live websocket.

This entrypoint drives the LLM with a Pipecat Flows `FlowManager` running the 7-node postpartum
NodeConfig graph from `flows/postpartum.py` (Maya: identity → recovery → PHQ-2/9 → newborn →
… → wrap-up, with emergency-escalation globals). Personas/tasks come from `prompts/prompts.json`.

Pipeline identity: Deepgram STT → NVIDIA Nemotron LLM → Cartesia TTS, 8kHz μ-law, patient
Smart-Turn v3 endpointing.

PHASE 1 (outbound): no live dashboard required. `build_dashboard_client()` no-ops when
DASHBOARD_API_URL is unset, and we fall back to a generic patient ("there"). Inbound caller-ID
identification + DB lookups are Phase 2 (needs the dashboard/Supabase deployed).
"""

import os
import sys
from pathlib import Path

# The flow modules (flows/, prompts.py, dashboard_client.py) are bundled under ./src so the
# repo's bare imports ("from flows.postpartum import ...", "from prompts import ...") resolve,
# and prompts.py finds ./prompts/prompts.json via its parent.parent path logic.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import asyncio  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from loguru import logger  # noqa: E402

from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402
from pipecat.frames.frames import EndFrame, TTSSpeakFrame  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineParams, PipelineTask  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments  # noqa: E402
from pipecat.runner.utils import parse_telephony_websocket  # noqa: E402
from pipecat.serializers.twilio import TwilioFrameSerializer  # noqa: E402
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig  # noqa: E402
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.transports.websocket.fastapi import (  # noqa: E402
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy  # noqa: E402
from pipecat.turns.user_turn_strategies import UserTurnStrategies  # noqa: E402
from pipecat_flows import FlowManager  # noqa: E402

from dashboard_client import build_dashboard_client, redact  # noqa: E402
from flows.postpartum import (  # noqa: E402
    build_global_functions,
    build_mother_recovery_node,
    drain_writes,
    initial_node,
    set_flow_context,
)

load_dotenv(override=True)

# --- Config (values come from the Pipecat Cloud secret set) -------------------------------
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID") or "e07c00bc-4134-4eae-9ea4-1a55fb45746b"
CARTESIA_SPEED = float(os.getenv("CARTESIA_SPEED", "0.95"))
LLM_MODEL = os.getenv("NVIDIA_LLM_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
MAX_CALL_SECS = float(os.getenv("POSTPARTUM_MAX_CALL_SECS", "900"))  # postpartum calls run long
SR = 8000

VAD_STOP_SECS = float(os.getenv("VAD_STOP_SECS", "0.4"))
# ENDPOINTING: Smart-Turn v3's ONNX model is NOT present in the Pipecat Cloud image (confirmed:
# it never loads / produces 0 predictions in cloud logs), which left user turns unable to STOP —
# the agent "couldn't listen." So in the cloud we endpoint on Silero VAD via a speech-timeout
# stop strategy (VAD loads fine here). user_speech_timeout = silence after speech that ends the
# turn; higher = more patient (won't cut someone off mid-thought) but adds response latency.
USER_SPEECH_TIMEOUT = float(os.getenv("USER_SPEECH_TIMEOUT", "0.8"))


def build_greeting(preferred: str, language: str) -> str:
    if language == "es":
        return (
            f"Hola {preferred}, soy Maya de Raya Memorial llamando para ver cómo "
            "estás tú y el bebé. ¿Es un buen momento?"
        )
    return (
        f"Hi {preferred}, this is Maya from Raya Memorial calling to check in "
        "on you and the baby. Is this a good time?"
    )


async def run_postpartum(transport: FastAPIWebsocketTransport, handle_sigint: bool, *,
                         patient: dict, newborn: dict | None, language: str,
                         call_sid: str, dashboard, greeting: str,
                         had_profile: bool, call_id: str,
                         billing: list | None = None,
                         appointments: list | None = None,
                         prescriptions: list | None = None) -> None:
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

    # Pipecat Flows owns the LLM context across node transitions, so we hand it an empty
    # LLMContext and let FlowManager.initialize set up the first node's system + tool schema.
    context = LLMContext()
    aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=USER_SPEECH_TIMEOUT)]
            )
        ),
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
        # Prefetched at call start — lets the mid-call lookup_* tools answer from
        # memory with no network round trip (see flows.postpartum.set_flow_context).
        billing=billing,
        appointments=appointments,
        prescriptions=prescriptions,
    )

    # Identity verification needs a loaded patient record (a DOB to match against). With no
    # record (Phase-1 test / no dashboard), that node can't resolve and the model loops asking
    # name+DOB forever — so we skip straight to the recovery check-in. With a real profile we
    # start at identity_verify as designed.
    start_node = initial_node(flow_manager) if had_profile else build_mother_recovery_node(flow_manager)

    @transport.event_handler("on_client_connected")
    async def _greet(_t, _c):
        await asyncio.sleep(0.3)
        await task.queue_frames([TTSSpeakFrame(greeting, append_to_context=True)])
        await flow_manager.initialize(start_node)

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
        # Flush any still-in-flight background writes (e.g. the final CSAT) before
        # we close the connection, so non-blocking POSTs aren't lost on teardown.
        try:
            await drain_writes(flow_manager)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"drain_writes failed: {e}")
        try:
            from datetime import datetime, timezone
            await dashboard.update_call(
                call_id,
                status="completed",
                ended_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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

    runner = PipelineRunner(handle_sigint=handle_sigint)
    guard = asyncio.create_task(_max_duration_guard())
    try:
        await runner.run(task)
    finally:
        guard.cancel()


async def bot(runner_args: RunnerArguments):
    """Pipecat Cloud entrypoint. The platform hands us a live Twilio Media Streams websocket."""
    _transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)
    body = call_data.get("body", {}) or {}
    direction = str(body.get("direction", "inbound")).lower()
    call_sid = call_data.get("call_id", "") or ""

    # PHASE 1: patient comes from the dialout <Parameter>s (or a fallback). Inbound caller-ID
    # → DB identification is Phase 2 (needs the dashboard live).
    dashboard = build_dashboard_client()
    patient_id = str(body.get("patient_id", "") or "")
    preferred = str(body.get("preferred_name", "") or "").strip() or "there"
    language = str(body.get("language", "en") or "en").lower()
    if language != "es":
        language = "en"

    profile = {}
    if patient_id:
        try:
            profile = await dashboard.get_patient_profile(patient_id) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"get_patient_profile({patient_id}) failed; using fallback: {e}")
    had_profile = bool(profile.get("patient"))
    patient = profile.get("patient") or {
        "id": patient_id or "unknown",
        "preferred_name": preferred,
        "language": language,
    }
    newborns = profile.get("newborns") or []
    newborn = newborns[0] if newborns else None
    # Prefetched companions to the profile (one round trip already returned them):
    # passed into flow state so mid-call lookups need no further network calls.
    billing = profile.get("billing")
    appointments = profile.get("appointments")
    prescriptions = profile.get("prescriptions")
    preferred = patient.get("preferred_name") or (patient.get("name", "there").split() or ["there"])[0]
    greeting = build_greeting(preferred, language)

    # Create the call row up front so EVERY per-node write references a real call_id
    # (without this, writes carry a fake "cloud-…" id and orphan / FK-fail). Only when
    # we have a real loaded profile + live dashboard; otherwise keep the placeholder so
    # the Phase-1 no-dashboard test path still runs.
    call_dir = "inbound" if direction == "inbound" else "outbound"
    call_id = f"cloud-{call_sid[-8:]}" if call_sid else "cloud-unknown"
    if had_profile:
        try:
            call_row = await dashboard.start_call(
                patient["id"], call_sid=call_sid, direction=call_dir,
                language=language, flow_name="postpartum_v1",
            )
            if isinstance(call_row, dict) and call_row.get("id"):
                call_id = call_row["id"]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"start_call failed; using placeholder call_id {call_id}: {e}")

    logger.info(f"bot() postpartum start — direction={direction} call_sid={call_sid} "
                f"patient_id={patient_id or '(none)'} language={language} call_id={call_id}")

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
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
    await run_postpartum(
        transport, runner_args.handle_sigint,
        patient=patient, newborn=newborn, language=language,
        call_sid=call_sid, dashboard=dashboard, greeting=greeting,
        had_profile=had_profile, call_id=call_id,
        billing=billing, appointments=appointments, prescriptions=prescriptions,
    )


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
