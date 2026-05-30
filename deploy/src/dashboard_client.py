"""
Async HTTP client for the timbre_dashboard `/api/v1/*` routes.

Why this module exists
----------------------
Every postpartum NodeConfig handler and every global function (`escalate_*`,
`lookup_*`, `capture_feedback`) needs to write to the dashboard during the
call: create a call row at /ws connect, PATCH `current_node` on every Flow
transition, POST per-node answers (recovery / newborn / phq / adherence / csat),
POST escalations, POST feedback. The Pipecat agent never blocks on these — they
fire-and-forget — so this client is async + non-fatal.

Safety properties (relied on by the voice path)
-----------------------------------------------
- **No-op stub when env is unset.** If `DASHBOARD_API_URL` or
  `DASHBOARD_API_TOKEN` is missing we log a single WARN and return a stub. Every
  method on the stub is a no-op that returns `None` so the call still completes.
  This matters because the dashboard is sibling work and may not be live yet —
  the inbound voice companion (twilio_bot.py) still runs against this venv and
  must not regress.
- **5s timeout + 2 retries on TimeoutException only.** Other 4xx/5xx are raised
  as `DashboardError` so node handlers can decide; voice path callers ignore the
  error (logged) and continue. We never retry on non-timeout 5xx because the DB
  is the source of truth and a retry of e.g. a recovery POST would double-insert.
- **`redact()` for PII.** Phone / email / SSN stripped with conservative regex
  before any `transcript_redacted` field goes over the wire. Medical notes and
  prompts are NEVER passed through redact (would corrupt clinical content).
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
from loguru import logger

_REQUEST_TIMEOUT = 5.0
_MAX_RETRIES = 2

# --- PII redaction --------------------------------------------------------
# Demo-grade regex. Presidio would be the right tool for prod; for the demo
# the goal is "no obvious raw PII flows into transcript_redacted". The
# substitutions intentionally do not normalize spacing/punctuation around the
# match, so the surrounding sentence remains readable on the dashboard.
_PHONE_RE = re.compile(r"\+?\d{1,2}[\s.\-]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")


def redact(text: str | None) -> str:
    """Strip phone numbers, emails, SSN-shaped strings. Returns "" for None."""
    if not text:
        return ""
    out = _PHONE_RE.sub("<REDACTED:phone>", text)
    out = _EMAIL_RE.sub("<REDACTED:email>", out)
    out = _SSN_RE.sub("<REDACTED:ssn>", out)
    return out


class DashboardError(RuntimeError):
    """Raised when the dashboard returns a non-2xx response (after retries)."""

    def __init__(self, method: str, path: str, status: int, body: str):
        super().__init__(f"{method} {path} -> {status}: {body[:300]}")
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class _NoopDashboardClient:
    """Stub returned when env is unset. Every coroutine returns None.

    We use __getattr__ so the surface area auto-matches the real client — no
    risk of a new method being missed here when DashboardClient adds one.
    """

    _warned = False

    def __init__(self) -> None:
        if not _NoopDashboardClient._warned:
            logger.warning(
                "DASHBOARD_API_URL or DASHBOARD_API_TOKEN unset — dashboard writes "
                "are no-ops. The voice call still runs; nothing is persisted."
            )
            _NoopDashboardClient._warned = True

    def __getattr__(self, name: str):
        async def _noop(*_args: Any, **_kwargs: Any) -> None:
            logger.debug(f"dashboard noop: {name}")
            return None

        return _noop

    async def aclose(self) -> None:
        return None


class DashboardClient:
    """Async client over `DASHBOARD_API_URL`. One method per route in README."""

    def __init__(self, base_url: str, token: str, *, timeout: float = _REQUEST_TIMEOUT):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a single request with TimeoutException retries.

        Returns the parsed `data` field on success (the dashboard's `ok()`
        wraps every response as `{ok: true, data: ...}`). Raises
        `DashboardError` on non-2xx after retries.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                r = await self._client.request(method, path, **kwargs)
                if r.status_code >= 400:
                    raise DashboardError(method, path, r.status_code, r.text)
                if not r.content:
                    return None
                payload = r.json()
                # The dashboard's withAgent / ok() wraps as {ok, data}. We hand back data.
                if isinstance(payload, dict) and "data" in payload:
                    return payload["data"]
                return payload
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    backoff = 0.25 * (2**attempt)
                    logger.warning(
                        f"dashboard {method} {path} timed out (attempt {attempt+1}); "
                        f"retrying in {backoff:.2f}s"
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise
        # unreachable; satisfies type-checker
        raise last_exc if last_exc else DashboardError(method, path, 0, "unknown")

    # ---- queue + profile --------------------------------------------------

    async def get_call_queue(self) -> list[dict]:
        return await self._request("GET", "/api/v1/patients/call-queue") or []

    async def get_patient_profile(self, patient_id: str) -> dict:
        """Full profile bundle: patient + newborns + billing + appointments + prescriptions."""
        return await self._request("GET", f"/api/v1/patients/{patient_id}") or {}

    async def get_patient_billing(self, patient_id: str) -> list[dict]:
        return await self._request("GET", f"/api/v1/patients/{patient_id}/billing") or []

    async def get_patient_appointments(self, patient_id: str) -> list[dict]:
        return await self._request("GET", f"/api/v1/patients/{patient_id}/appointments") or []

    async def get_patient_prescriptions(self, patient_id: str) -> list[dict]:
        return await self._request("GET", f"/api/v1/patients/{patient_id}/prescriptions") or []

    # ---- call lifecycle ---------------------------------------------------

    async def start_call(
        self,
        patient_id: str,
        *,
        call_sid: str | None = None,
        direction: str = "outbound",
        language: str = "en",
        flow_name: str = "postpartum_v1",
        existing_call_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "patient_id": patient_id,
            "direction": direction,
            "language": language,
            "flow_name": flow_name,
        }
        if call_sid:
            body["call_sid"] = call_sid
        if existing_call_id:
            body["existing_call_id"] = existing_call_id
        return await self._request("POST", "/api/v1/calls", json=body) or {}

    async def update_call(self, call_id: str, **fields: Any) -> dict:
        """PATCH /api/v1/calls/:id with any of: status, current_node, ended_at,
        transcript_redacted, summary. Callers should ALWAYS pass `transcript_redacted`
        through `redact()` first."""
        return await self._request("PATCH", f"/api/v1/calls/{call_id}", json=fields) or {}

    # ---- per-node POSTs ---------------------------------------------------

    async def post_recovery(self, patient_id: str, call_id: str, **fields: Any) -> dict:
        body = {"call_id": call_id, **{k: v for k, v in fields.items() if v is not None}}
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/recovery", json=body
        ) or {}

    async def post_newborn(
        self, patient_id: str, call_id: str, newborn_id: str, **fields: Any
    ) -> dict:
        body = {
            "call_id": call_id,
            "newborn_id": newborn_id,
            **{k: v for k, v in fields.items() if v is not None},
        }
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/newborn", json=body
        ) or {}

    async def post_phq(
        self,
        patient_id: str,
        call_id: str,
        instrument: str,
        score: int,
        **fields: Any,
    ) -> dict:
        body = {
            "call_id": call_id,
            "instrument": instrument,
            "score": score,
            **{k: v for k, v in fields.items() if v is not None},
        }
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/phq", json=body
        ) or {}

    async def post_adherence(self, patient_id: str, call_id: str, **fields: Any) -> dict:
        body = {"call_id": call_id, **{k: v for k, v in fields.items() if v is not None}}
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/adherence", json=body
        ) or {}

    async def post_csat(
        self,
        patient_id: str,
        call_id: str,
        rating: int,
        qualitative_summary: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"call_id": call_id, "rating": rating}
        if qualitative_summary:
            body["qualitative_summary"] = qualitative_summary
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/csat", json=body
        ) or {}

    async def post_feedback(
        self,
        patient_id: str,
        category: str,
        note: str,
        *,
        call_id: str | None = None,
        sentiment: str = "neutral",
    ) -> dict:
        body: dict[str, Any] = {
            "category": category,
            "note": note,
            "sentiment": sentiment,
        }
        if call_id:
            body["call_id"] = call_id
        return await self._request(
            "POST", f"/api/v1/patients/{patient_id}/feedback", json=body
        ) or {}

    async def post_escalation(
        self,
        patient_id: str,
        severity: str,
        category: str,
        trigger_text: str,
        *,
        call_id: str | None = None,
        trigger_phrase: str | None = None,
        transcript_excerpt: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "patient_id": patient_id,
            "severity": severity,
            "category": category,
            "trigger_text": trigger_text,
        }
        if call_id:
            body["call_id"] = call_id
        if trigger_phrase:
            body["trigger_phrase"] = trigger_phrase
        if transcript_excerpt:
            body["transcript_excerpt"] = transcript_excerpt
        return await self._request("POST", "/api/v1/escalations", json=body) or {}

    # ---- eval routes (NOT called from the live voice path) ----------------
    # These exist on the client because an external runner (Cekura) may share
    # this module. The live Pipecat agent never calls them — see the PRD's
    # boundary table.

    async def start_eval(
        self, persona: str, *, flow_name: str = "postpartum_v1"
    ) -> dict:
        body = {"persona": persona, "flow_name": flow_name}
        return await self._request("POST", "/api/v1/evals", json=body) or {}

    async def post_eval_result(
        self,
        eval_run_id: str,
        criterion: str,
        passed: bool,
        *,
        score: float | None = None,
        details: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {"criterion": criterion, "passed": passed}
        if score is not None:
            body["score"] = score
        if details is not None:
            body["details"] = details
        return await self._request(
            "POST", f"/api/v1/evals/{eval_run_id}/results", json=body
        ) or {}

    async def finish_eval(
        self,
        eval_run_id: str,
        *,
        overall_score: float | None = None,
        transcript: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"status": "completed"}
        if overall_score is not None:
            body["overall_score"] = overall_score
        if transcript is not None:
            body["transcript"] = transcript
        return await self._request(
            "PATCH", f"/api/v1/evals/{eval_run_id}", json=body
        ) or {}


def build_dashboard_client() -> DashboardClient | _NoopDashboardClient:
    """Construct the real client from env, or a no-op stub if env is incomplete."""
    base_url = os.environ.get("DASHBOARD_API_URL", "").strip()
    token = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not base_url or not token:
        return _NoopDashboardClient()
    logger.info(f"dashboard client -> {base_url}")
    return DashboardClient(base_url, token)
