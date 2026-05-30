"""Best-effort Cekura observability export for completed phone calls.

Transcript export is enabled when Cekura credentials are configured. Audio upload is a
separate opt-in because recording phone calls has consent and retention implications.
Nothing in this module may raise into the live voice path.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from loguru import logger

from agent_memory.recorder import scrub

_OBSERVE_URL = "https://api.cekura.ai/observability/v1/observe/"
_PENDING_TTL_SECS = 3600
_PENDING_MAX_ENTRIES = 1024
_LOCK = threading.RLock()


@dataclass
class _PendingCall:
    transcript: list[dict[str, str]] | None = None
    recording_url: str = ""
    caller: str = ""
    mode: str = ""
    authorized: bool = False
    created_at: float = 0.0


_PENDING: dict[str, _PendingCall] = {}


def enabled() -> bool:
    """Return whether the minimum Cekura ingestion credentials are configured."""
    return bool(
        os.getenv("CEKURA_API_KEY")
        and (os.getenv("CEKURA_AGENT_ID") or os.getenv("CEKURA_ASSISTANT_ID"))
    )


def record_calls() -> bool:
    """Return whether Twilio audio recording and upload were explicitly enabled."""
    return enabled() and os.getenv("CEKURA_RECORD_CALLS", "").lower() in {"1", "true", "yes"}


def _evict_stale() -> None:
    cutoff = time.time() - _PENDING_TTL_SECS
    for call_sid, pending in list(_PENDING.items()):
        if pending.created_at < cutoff:
            _PENDING.pop(call_sid, None)
    while len(_PENDING) > _PENDING_MAX_ENTRIES:
        oldest = min(_PENDING, key=lambda call_sid: _PENDING[call_sid].created_at)
        _PENDING.pop(oldest, None)


def _clean_transcript(messages: list[dict] | None) -> list[dict[str, str]]:
    transcript = []
    for message in messages or []:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        clean = scrub(content)
        if clean:
            transcript.append({"role": role, "content": clean})
    return transcript


def _identity() -> dict[str, Any]:
    agent_id = os.getenv("CEKURA_AGENT_ID")
    if agent_id:
        return {"agent": int(agent_id) if agent_id.isdigit() else agent_id}
    return {"assistant_id": os.environ["CEKURA_ASSISTANT_ID"]}


def _twilio_recording_wav_url(recording_url: str) -> str:
    """Return a Twilio media URL without ever forwarding credentials to another host."""
    parts = urlsplit(recording_url)
    if (
        parts.scheme != "https"
        or parts.hostname != "api.twilio.com"
        or not parts.path.startswith("/2010-04-01/Accounts/")
        or "/Recordings/RE" not in parts.path
    ):
        raise ValueError("unexpected Twilio recording URL")
    return recording_url if recording_url.endswith(".wav") else f"{recording_url}.wav"


def _submit(call_sid: str, pending: _PendingCall) -> None:
    payload: dict[str, Any] = {
        "call_id": call_sid,
        **_identity(),
        "transcript_type": "cekura",
        "transcript_json": pending.transcript or [],
        "customer_number": pending.caller,
        "metadata": {
            "provider": "twilio",
            "mode": pending.mode,
            "authorized": pending.authorized,
        },
    }
    headers = {"X-CEKURA-API-KEY": os.environ["CEKURA_API_KEY"]}

    if pending.recording_url:
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        recording = requests.get(
            _twilio_recording_wav_url(pending.recording_url),
            auth=(account_sid, auth_token),
            timeout=20,
        )
        recording.raise_for_status()
        data = {
            key: json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            for key, value in payload.items()
        }
        response = requests.post(
            _OBSERVE_URL,
            headers=headers,
            data=data,
            files={"voice_recording": (f"{call_sid}.wav", recording.content, "audio/wav")},
            timeout=30,
        )
    else:
        response = requests.post(_OBSERVE_URL, headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    logger.info(f"Cekura observability exported call_sid={call_sid}")


def _submit_if_ready(call_sid: str) -> None:
    if not enabled():
        return
    with _LOCK:
        _evict_stale()
        pending = _PENDING.get(call_sid)
        if pending is None or pending.transcript is None:
            return
        if record_calls() and not pending.recording_url:
            return
        _PENDING.pop(call_sid, None)
    try:
        _submit(call_sid, pending)
    except Exception as exc:  # noqa: BLE001 - observability must never break calls
        logger.warning(f"Cekura export failed for call_sid={call_sid}: {type(exc).__name__}: {exc}")


def export_transcript(
    call_sid: str,
    messages: list[dict] | None,
    *,
    caller: str = "",
    mode: str = "",
    authorized: bool = False,
) -> None:
    """Store a scrubbed transcript and submit it when any requested audio is available."""
    if not enabled():
        return
    with _LOCK:
        _evict_stale()
        pending = _PENDING.setdefault(call_sid, _PendingCall(created_at=time.time()))
        pending.transcript = _clean_transcript(messages)
        pending.caller = caller
        pending.mode = mode
        pending.authorized = authorized
    _submit_if_ready(call_sid)


def recording_completed(call_sid: str, recording_url: str) -> None:
    """Attach Twilio's completed recording URL and submit once the transcript is ready."""
    if not record_calls() or not call_sid or not recording_url:
        return
    try:
        _twilio_recording_wav_url(recording_url)
    except ValueError:
        logger.warning(f"ignored unexpected Twilio recording URL for call_sid={call_sid}")
        return
    with _LOCK:
        _evict_stale()
        pending = _PENDING.setdefault(call_sid, _PendingCall(created_at=time.time()))
        pending.recording_url = recording_url
    _submit_if_ready(call_sid)
