"""
Postpartum maternal check-in flow (Pipecat Flows).

Graph
-----
  identity_verify
    ├─[proxy detected]──► proxy_reject_reschedule ──► END
    └─[verified]──► mother_recovery
                       │   red flag → global escalate_to_nurse → escalation_handoff → END
                       ▼
                   mental_health_phq2
                       │   PHQ-2 >= 3 → phq9_full
                       ▼            │   Q9 > 0 OR self-harm → escalate_crisis → handoff → END
                   newborn_health  ◄┘
                       │   newborn red flag → escalate_pediatric → handoff → END
                       │   feeding issue → lactation_support → medication_adherence
                       ▼
                   medication_adherence
                       │   barrier ∈ {cost, transport, no_pharmacy} → pharmacy_routing → social_screen
                       ▼
                   social_screen
                       │   IPV active danger → escalate_crisis → handoff → END
                       ▼
                   doula_handoff ──► csat_collection ──► END

Each node POSTs its results to the dashboard via DashboardClient as part of the
function handler. Pipecat Flows handles the LLM tool-call wiring; this module
only describes the graph + side effects.

Globals (available at every node, see register_global_functions)
- escalate_to_nurse / escalate_pediatric / escalate_crisis — terminal, jump to handoff
- lookup_patient_billing / lookup_appointment_history / lookup_prescription_status — non-terminal
- capture_feedback — non-terminal

A handler returns (result_dict, next_node_or_None). Returning None for the next
node keeps the LLM in the current node — Pipecat preserves context.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from loguru import logger
from pipecat_flows import (
    FlowArgs,
    FlowManager,
    FlowsFunctionSchema,
    NodeConfig,
)

from dashboard_client import DashboardClient, redact
from prompts import load_prompt


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
#
# We stash the DashboardClient, call_id, patient and newborn on `flow_manager.state`
# so every handler can reach them. set_flow_context() is called once from the
# bot entrypoint right after FlowManager construction.


def set_flow_context(
    fm: FlowManager,
    *,
    client: DashboardClient,
    patient: dict,
    newborn: dict | None,
    call_id: str,
    language: str,
    billing: list[dict] | None = None,
    appointments: list[dict] | None = None,
    prescriptions: list[dict] | None = None,
) -> None:
    fm.state["client"] = client
    fm.state["patient"] = patient
    fm.state["newborn"] = newborn
    fm.state["call_id"] = call_id
    fm.state["language"] = language
    # LATENCY: the /api/v1/patients/{id} profile call already returns billing +
    # appointments + prescriptions in ONE round trip. We stash them here so the
    # mid-call lookup_* tools answer from memory (zero network) instead of making
    # a fresh HTTP call while the patient is waiting on the line. None means "not
    # prefetched" — the lookups fall back to a live fetch in that case.
    fm.state["billing"] = billing
    fm.state["appointments"] = appointments
    fm.state["prescriptions"] = prescriptions
    # Background DB writes (see _spawn). Per-node answer POSTs fire into here so a
    # node transition never blocks on Vercel; drain_writes() awaits them at call end
    # so nothing is lost when the websocket closes.
    fm.state["_bg_writes"] = set()
    # Track scoring so phq9 knows the phq2 score, and so we can decide
    # whether a recovery red flag warrants escalation rather than recording.
    fm.state["phq2_score"] = None
    fm.state["phq9_score"] = None


def _spawn(fm: FlowManager, coro) -> None:
    """Fire a best-effort DB write WITHOUT blocking the node transition.

    Per-node answer POSTs (recovery, phq, newborn, adherence, csat, feedback) are
    not needed to decide the next node, so awaiting them inline just adds the
    Vercel round trip to the gap before the agent's next sentence. We schedule them
    as background tasks instead, keep a reference so they aren't GC'd mid-flight,
    and log (never raise) on failure. Life-safety escalations are deliberately NOT
    routed through here — those stay awaited at their call sites.
    """
    bg: set = fm.state.setdefault("_bg_writes", set())
    task = asyncio.ensure_future(coro)
    bg.add(task)

    def _done(t: asyncio.Task) -> None:
        bg.discard(t)
        exc = t.exception() if not t.cancelled() else None
        if exc is not None:
            logger.warning(f"background DB write failed (non-fatal): {exc}")

    task.add_done_callback(_done)


async def drain_writes(fm: FlowManager, timeout: float = 4.0) -> None:
    """Await any still-pending background writes (call the bot's disconnect handler
    here before task.cancel()). Bounded so a hung Vercel write can't wedge teardown."""
    bg = list(fm.state.get("_bg_writes") or [])
    if not bg:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*bg, return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        logger.warning(f"drain_writes: {len(bg)} write(s) still pending after {timeout}s")


def _ctx(fm: FlowManager) -> tuple[DashboardClient, dict, dict | None, str, str]:
    return (
        fm.state["client"],
        fm.state["patient"],
        fm.state.get("newborn"),
        fm.state["call_id"],
        fm.state.get("language", "en"),
    )


def _lang_key(base: str, lang: str) -> str:
    return f"{base}_{'es' if lang == 'es' else 'en'}"


async def _patch_current_node(fm: FlowManager, node_name: str) -> None:
    """PATCH /api/v1/calls/{id} with current_node. Pure telemetry for the dashboard's
    live call view — not on the decision path — so it fires in the background and the
    transition never waits on it. Kept `async` so call sites are unchanged."""
    client, _, _, call_id, _ = _ctx(fm)
    _spawn(fm, client.update_call(call_id, current_node=node_name))


# ---------------------------------------------------------------------------
# Per-node handlers
# ---------------------------------------------------------------------------
#
# Each handler returns (result_dict_for_llm, next_node_or_None). The LLM uses
# the result_dict as the function-call response; the next NodeConfig is what
# Pipecat Flows transitions to (None = stay in current node, used by globals).


async def verify_identity(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    verified = bool(args.get("verified"))
    proxy = bool(args.get("proxy"))
    if proxy or not verified:
        return {"verified": False, "proxy": True}, build_proxy_reject_node(fm)
    await _patch_current_node(fm, "mother_recovery")
    return {"verified": True}, build_mother_recovery_node(fm)


async def end_proxy(_args: FlowArgs, fm: FlowManager) -> tuple[dict, NodeConfig | None]:
    client, patient, _, _, _ = _ctx(fm)
    _spawn(fm, client.post_feedback(
        patient["id"],
        category="scheduling",
        note="Proxy answered the postpartum check-in call; rescheduling needed.",
        sentiment="neutral",
        call_id=fm.state["call_id"],
    ))
    await _patch_current_node(fm, "END")
    return {"ok": True}, build_end_node(fm, reason="proxy_reschedule")


async def record_recovery(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    # MEDIUM TRIM: skip mental_health_phq2 / phq9 / newborn. Recovery now flows directly to
    # medication_adherence. Mood is folded into recovery's emotional_state field; suicidal
    # ideation is caught by the escalate_crisis global from anywhere.
    _spawn(fm, client.post_recovery(
        patient["id"],
        call_id,
        bleeding=args.get("bleeding"),
        pain_score=args.get("pain_score"),
        incision_status=args.get("incision_status"),
        mobility_status=args.get("mobility_status"),
        urination_status=args.get("urination_status"),
        emotional_state=args.get("emotional_state"),
        notes=args.get("notes"),
    ))
    await _patch_current_node(fm, "medication_adherence")
    return {"recorded": True}, build_medication_adherence_node(fm)


async def record_phq2(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    q1 = int(args.get("q1", 0))
    q2 = int(args.get("q2", 0))
    score = q1 + q2
    fm.state["phq2_score"] = score
    _spawn(fm, client.post_phq(
        patient["id"],
        call_id,
        instrument="phq2",
        score=score,
        responses={"q1": q1, "q2": q2},
    ))
    if score >= 3:
        await _patch_current_node(fm, "phq9_full")
        return {"score": score, "elevated": True}, build_phq9_node(fm)
    await _patch_current_node(fm, "newborn_health")
    return {"score": score, "elevated": False}, build_newborn_node(fm)


async def record_phq9(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    score = int(args.get("score", 0))
    suicidal = bool(args.get("suicidal_ideation"))
    fm.state["phq9_score"] = score
    _spawn(fm, client.post_phq(
        patient["id"],
        call_id,
        instrument="phq9",
        score=score,
        responses=args.get("responses") or {},
        suicidal_ideation=suicidal,
    ))
    if suicidal:
        # Auto-fire the crisis escalation; the model is also instructed to call
        # escalate_crisis but we don't trust the LLM with a life-safety branch.
        try:
            await client.post_escalation(
                patient["id"],
                severity="urgent",
                category="crisis",
                trigger_phrase="phq9 suicidal_ideation",
                trigger_text=f"PHQ-9 score {score} with positive Q9 (self-harm/suicidality).",
                call_id=call_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"post_escalation (crisis from phq9) failed: {e}")
        await _patch_current_node(fm, "escalation_handoff")
        return {"score": score, "suicidal_ideation": True}, build_escalation_handoff_node(
            fm, severity="urgent", category="crisis"
        )
    await _patch_current_node(fm, "newborn_health")
    return {"score": score, "suicidal_ideation": False}, build_newborn_node(fm)


async def record_newborn(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, newborn, call_id, _ = _ctx(fm)
    if not newborn:
        logger.warning("record_newborn called but no newborn in state; skipping POST")
        await _patch_current_node(fm, "medication_adherence")
        return {"recorded": False}, build_medication_adherence_node(fm)
    _spawn(fm, client.post_newborn(
        patient["id"],
        call_id,
        newborn["id"],
        feeding_count_24h=args.get("feeding_count_24h"),
        wet_diapers_24h=args.get("wet_diapers_24h"),
        dirty_diapers_24h=args.get("dirty_diapers_24h"),
        jaundice_observed=args.get("jaundice_observed"),
        fever=args.get("fever"),
        fever_temp_f=args.get("fever_temp_f"),
        sleep_pattern=args.get("sleep_pattern"),
        notes=args.get("notes"),
    ))

    # Route based on what the LLM detected — feeding_issue is a soft signal,
    # red_flag is a hard escalation. We trust the LLM here because the prompt
    # is explicit about red-flag criteria; if it ever skips an escalation, the
    # global escalate_pediatric is also available and instrumented in logs.
    if args.get("feeding_issue") and not args.get("red_flag"):
        await _patch_current_node(fm, "lactation_support")
        return {"recorded": True, "next": "lactation"}, build_lactation_node(fm)
    await _patch_current_node(fm, "medication_adherence")
    return {"recorded": True, "next": "meds"}, build_medication_adherence_node(fm)


async def record_lactation(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    _spawn(fm, client.post_feedback(
        patient["id"],
        category="clinical",
        note=f"Lactation support discussed: {args.get('note', 'feeding struggle')}",
        sentiment="neutral",
        call_id=call_id,
    ))
    await _patch_current_node(fm, "medication_adherence")
    return {"recorded": True}, build_medication_adherence_node(fm)


async def record_adherence(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    """Called ONCE per prescription. The LLM tells us when it's done with the
    last one via args["last"]=true; we only route forward then."""
    client, patient, _, call_id, _ = _ctx(fm)
    barrier = args.get("barrier") or "none"
    _spawn(fm, client.post_adherence(
        patient["id"],
        call_id,
        medication=args.get("medication"),
        prescription_id=args.get("prescription_id"),
        picked_up=args.get("picked_up"),
        taking_as_prescribed=args.get("taking_as_prescribed"),
        barrier=barrier,
        barrier_notes=args.get("barrier_notes"),
    ))

    routing_barriers = {"cost", "transport", "no_pharmacy"}
    if args.get("last"):
        if barrier in routing_barriers:
            await _patch_current_node(fm, "pharmacy_routing")
            return {"recorded": True, "next": "pharmacy"}, build_pharmacy_node(fm, barrier)
        await _patch_current_node(fm, "social_screen")
        return {"recorded": True, "next": "social"}, build_social_node(fm)
    # Not the last prescription: stay in this node.
    return {"recorded": True, "next": None}, None


async def log_pharmacy_routing(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    barrier = args.get("barrier") or "cost"
    category = "billing" if barrier == "cost" else "scheduling"
    _spawn(fm, client.post_feedback(
        patient["id"],
        category=category,
        note=f"Pharmacy barrier ({barrier}): {args.get('summary', '')}".strip(": "),
        sentiment="negative" if barrier == "cost" else "neutral",
        call_id=call_id,
    ))
    await _patch_current_node(fm, "social_screen")
    return {"logged": True}, build_social_node(fm)


async def record_social(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    ipv = (args.get("ipv_concern") or "none").lower()
    # Non-urgent screen findings: background writes (don't delay the next prompt).
    if not args.get("food_secure", True):
        _spawn(fm, client.post_feedback(
            patient["id"],
            category="other",
            note="Food insecurity reported on social screen.",
            sentiment="negative",
            call_id=call_id,
        ))
    if not args.get("has_support", True):
        _spawn(fm, client.post_feedback(
            patient["id"],
            category="other",
            note="Limited postpartum support at home reported.",
            sentiment="negative",
            call_id=call_id,
        ))
    # Active-danger IPV is life-safety: AWAIT so the escalation is durably posted
    # before we transition (never route this through the background _spawn path).
    if ipv == "current_active_danger":
        try:
            await client.post_escalation(
                patient["id"],
                severity="urgent",
                category="crisis",
                trigger_phrase="ipv active danger",
                trigger_text="Patient reports active intimate-partner violence danger on social screen.",
                call_id=call_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"record_social IPV escalation failed: {e}")
    if ipv == "current_active_danger":
        await _patch_current_node(fm, "escalation_handoff")
        return {"escalated": True}, build_escalation_handoff_node(
            fm, severity="urgent", category="crisis"
        )
    # MEDIUM TRIM: skip doula_handoff; social flows directly into csat_collection.
    await _patch_current_node(fm, "csat_collection")
    return {"recorded": True}, build_csat_node(fm)


async def confirm_doula_visit(
    _args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    await _patch_current_node(fm, "csat_collection")
    return {"confirmed": True}, build_csat_node(fm)


async def record_csat(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    rating = int(args.get("rating", 0)) or 0
    summary = args.get("qualitative_summary") or None
    # CSAT is the last write before END — _spawn + drain_writes() on disconnect
    # makes sure it lands even though we don't block the goodbye on it.
    _spawn(fm, client.post_csat(
        patient["id"], call_id, rating=rating, qualitative_summary=summary
    ))
    await _patch_current_node(fm, "END")
    return {"recorded": True}, build_end_node(fm, reason="csat_complete")


async def end_call(_args: FlowArgs, fm: FlowManager) -> tuple[dict, NodeConfig | None]:
    await _patch_current_node(fm, "END")
    return {"ok": True}, build_end_node(fm, reason="end_call")


# ---------------------------------------------------------------------------
# Global function handlers
# ---------------------------------------------------------------------------
#
# Escalations: terminal — they POST the escalation row and transition to the
# handoff node, which is the only node allowed to wrap the call up after a
# red flag.
#
# Lookups / capture_feedback: non-terminal — return None as next node so the
# LLM continues the current clinical question.


async def escalate_to_nurse(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    severity = args.get("severity") or "urgent"
    try:
        await client.post_escalation(
            patient["id"],
            severity=severity,
            category="maternal",
            trigger_phrase=args.get("trigger_phrase"),
            trigger_text=args.get("trigger_text") or "maternal red flag",
            call_id=call_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"escalate_to_nurse post failed: {e}")
    await _patch_current_node(fm, "escalation_handoff")
    return {"escalated": True, "category": "maternal"}, build_escalation_handoff_node(
        fm, severity=severity, category="maternal"
    )


async def escalate_pediatric(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    severity = args.get("severity") or "urgent"
    try:
        await client.post_escalation(
            patient["id"],
            severity=severity,
            category="pediatric",
            trigger_phrase=args.get("trigger_phrase"),
            trigger_text=args.get("trigger_text") or "newborn red flag",
            call_id=call_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"escalate_pediatric post failed: {e}")
    await _patch_current_node(fm, "escalation_handoff")
    return {"escalated": True, "category": "pediatric"}, build_escalation_handoff_node(
        fm, severity=severity, category="pediatric"
    )


async def escalate_crisis(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    try:
        await client.post_escalation(
            patient["id"],
            severity="urgent",
            category="crisis",
            trigger_phrase=args.get("trigger_phrase"),
            trigger_text=args.get("trigger_text") or "crisis red flag",
            call_id=call_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"escalate_crisis post failed: {e}")
    await _patch_current_node(fm, "escalation_handoff")
    return {"escalated": True, "category": "crisis"}, build_escalation_handoff_node(
        fm, severity="urgent", category="crisis"
    )


# ---- non-terminal globals ------------------------------------------------


def _format_billing(items: list[dict]) -> str:
    if not items:
        return "I don't see any open bills on your account right now."
    top = items[0]
    amount = top.get("amount_cents", 0) / 100.0
    status = top.get("status", "unknown")
    note = top.get("processing_notes") or top.get("service_description") or ""
    if status == "processing":
        return f"Your most recent bill is ${amount:,.2f}, currently being processed by insurance. {note}".strip()
    if status == "paid":
        return f"Your most recent bill of ${amount:,.2f} is paid in full — nothing owed."
    if status == "due":
        return f"You have a balance of ${amount:,.2f} due. {note}".strip()
    if status == "overdue":
        return f"You have an overdue balance of ${amount:,.2f}. {note}".strip()
    return f"Your most recent bill is ${amount:,.2f}, status {status}."


def _format_appointments(items: list[dict], window: str) -> str:
    if not items:
        return "I don't see any appointments on file."
    if window == "past":
        items = [a for a in items if a.get("status") in {"completed", "cancelled"}]
    elif window == "upcoming":
        items = [a for a in items if a.get("status") == "scheduled"]
    if not items:
        return "I don't see any in that window."
    first = items[0]
    when = first.get("scheduled_at", "the scheduled time")
    who = first.get("provider_name", "your provider")
    kind = first.get("appointment_type", "a visit")
    return f"Your next is {kind.replace('_', ' ')} with {who} at {when}."


def _format_prescriptions(items: list[dict], hint: str | None) -> str:
    if not items:
        return "I don't see any active prescriptions on file."
    if hint:
        h = hint.lower()
        items = [p for p in items if h in (p.get("medication") or "").lower()] or items
    p = items[0]
    med = p.get("medication", "your medication")
    status = p.get("pickup_status", "unknown")
    pharmacy = p.get("pharmacy", "your pharmacy")
    if status == "ready":
        return f"{med} is ready for pickup at {pharmacy}."
    if status == "picked_up":
        return f"You've already picked up {med} from {pharmacy}."
    if status == "processing":
        return f"{med} is still processing at {pharmacy} — usually a day or two."
    if status == "not_picked_up":
        return f"{med} is waiting at {pharmacy}, not picked up yet."
    return f"{med} is at {pharmacy}, status {status}."


async def lookup_patient_billing(
    _args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, _, _ = _ctx(fm)
    # LATENCY: served from the profile bundle prefetched at call start — no network
    # round trip while she waits. Falls back to a live fetch only if not prefetched.
    items = fm.state.get("billing")
    if items is None:
        try:
            items = await client.get_patient_billing(patient["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"lookup_patient_billing fetch failed: {e}")
            items = []
    return {"answer": _format_billing(items)}, None


async def lookup_appointment_history(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, _, _ = _ctx(fm)
    window = (args.get("time_window") or "upcoming").lower()
    items = fm.state.get("appointments")
    if items is None:
        try:
            items = await client.get_patient_appointments(patient["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"lookup_appointment_history fetch failed: {e}")
            items = []
    return {"answer": _format_appointments(items, window)}, None


async def lookup_prescription_status(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, _, _ = _ctx(fm)
    hint = args.get("medication_hint")
    items = fm.state.get("prescriptions")
    if items is None:
        try:
            items = await client.get_patient_prescriptions(patient["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"lookup_prescription_status fetch failed: {e}")
            items = []
    return {"answer": _format_prescriptions(items, hint)}, None


async def capture_feedback(
    args: FlowArgs, fm: FlowManager
) -> tuple[dict, NodeConfig | None]:
    client, patient, _, call_id, _ = _ctx(fm)
    category = args.get("category") or "other"
    note = args.get("note") or ""
    sentiment = args.get("sentiment") or "neutral"
    if not note:
        return {"captured": False}, None
    _spawn(fm, client.post_feedback(
        patient["id"],
        category=category,
        note=note,
        sentiment=sentiment,
        call_id=call_id,
    ))
    return {"captured": True}, None


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------
#
# Each builder returns a NodeConfig. They reference the role/task prompts from
# prompts/prompts.json keyed by language. The `_lang()` helper picks the right
# language at build time.


def _lang(fm: FlowManager) -> str:
    return fm.state.get("language", "en")


def _patient_context(fm: FlowManager) -> dict[str, str]:
    """Substitution dict for templated prompts (name, postpartum age, newborn, etc.)."""
    _, patient, newborn, _, _ = _ctx(fm)
    preferred = (
        patient.get("preferred_name")
        or (patient.get("name") or "there").split()[0]
        or "there"
    )
    delivery = (patient.get("birth_type") or "delivery").replace("_", "-")
    bd = patient.get("birth_date") or patient.get("delivery_date")
    days_pp: int | None = None
    if bd:
        try:
            if isinstance(bd, str):
                bd = date.fromisoformat(bd[:10])
            days_pp = (date.today() - bd).days
        except Exception:  # noqa: BLE001
            days_pp = None
    if days_pp is None:
        days_text = "in the early postpartum period"
    elif days_pp <= 7:
        days_text = f"{days_pp} day{'s' if days_pp != 1 else ''} postpartum"
    else:
        weeks = days_pp // 7
        days_text = f"{weeks} week{'s' if weeks != 1 else ''} postpartum"
    newborn_name = (newborn or {}).get("name") or "the baby"
    nb_dob = (newborn or {}).get("dob")
    age_text = "growing fast"
    if nb_dob:
        try:
            if isinstance(nb_dob, str):
                nb_dob = date.fromisoformat(nb_dob[:10])
            wks = (date.today() - nb_dob).days // 7
            age_text = (
                f"{wks} week{'s' if wks != 1 else ''} old"
                if wks > 0
                else "just a few days old"
            )
        except Exception:  # noqa: BLE001
            pass
    return {
        "preferred_name": preferred,
        "delivery_type": delivery,
        "days_postpartum_text": days_text,
        "newborn_name": newborn_name,
        "newborn_age_text": age_text,
    }


def _role_message(fm: FlowManager) -> str:
    template = load_prompt(_lang_key("postpartum_role", _lang(fm)))
    try:
        return template.format(**_patient_context(fm))
    except (KeyError, IndexError) as e:
        logger.warning(f"role template substitution failed: {e}; returning raw")
        return template


def _task(fm: FlowManager, base: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": load_prompt(_lang_key(base, _lang(fm)))}]


def build_identity_verify_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="identity_verify",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_identity_verify"),
        functions=[
            FlowsFunctionSchema(
                name="verify_identity",
                handler=verify_identity,
                description="Record whether identity was verified. Set proxy=true if someone other than the patient answered.",
                properties={
                    "verified": {"type": "boolean"},
                    "proxy": {"type": "boolean"},
                },
                required=["verified"],
            ),
        ],
    )


def build_proxy_reject_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="proxy_reject_reschedule",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_proxy_reject"),
        functions=[
            FlowsFunctionSchema(
                name="end_proxy",
                handler=end_proxy,
                description="End the call after a proxy answered.",
                properties={},
                required=[],
            ),
        ],
    )


def build_mother_recovery_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="mother_recovery",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_recovery"),
        functions=[
            FlowsFunctionSchema(
                name="record_recovery",
                handler=record_recovery,
                description="Record the mother's recovery answers.",
                properties={
                    "bleeding": {
                        "type": "string",
                        "enum": ["none", "spotting", "light", "moderate", "heavy", "concerning"],
                    },
                    "pain_score": {"type": "integer", "minimum": 0, "maximum": 10},
                    "incision_status": {"type": "string"},
                    "mobility_status": {"type": "string"},
                    "urination_status": {"type": "string"},
                    "emotional_state": {"type": "string"},
                    "notes": {"type": "string"},
                },
                required=["bleeding", "pain_score"],
            ),
        ],
    )


def build_phq2_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="mental_health_phq2",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_phq2"),
        functions=[
            FlowsFunctionSchema(
                name="record_phq2",
                handler=record_phq2,
                description="Record PHQ-2 q1 (interest/pleasure) and q2 (depressed/hopeless), each 0-3.",
                properties={
                    "q1": {"type": "integer", "minimum": 0, "maximum": 3},
                    "q2": {"type": "integer", "minimum": 0, "maximum": 3},
                },
                required=["q1", "q2"],
            ),
        ],
    )


def build_phq9_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="phq9_full",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_phq9"),
        functions=[
            FlowsFunctionSchema(
                name="record_phq9",
                handler=record_phq9,
                description="Record full PHQ-9 score (0-27) and suicidal_ideation flag.",
                properties={
                    "score": {"type": "integer", "minimum": 0, "maximum": 27},
                    "suicidal_ideation": {"type": "boolean"},
                    "responses": {"type": "object"},
                },
                required=["score", "suicidal_ideation"],
            ),
        ],
    )


def build_newborn_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="newborn_health",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_newborn"),
        functions=[
            FlowsFunctionSchema(
                name="record_newborn",
                handler=record_newborn,
                description="Record newborn health answers. Set feeding_issue=true if a non-emergency feeding struggle was described; set red_flag=true if criteria like fever, blue lips, lethargy, <6 wet diapers were mentioned (also call escalate_pediatric in that case).",
                properties={
                    "feeding_count_24h": {"type": "integer", "minimum": 0, "maximum": 30},
                    "wet_diapers_24h": {"type": "integer", "minimum": 0, "maximum": 30},
                    "dirty_diapers_24h": {"type": "integer", "minimum": 0, "maximum": 30},
                    "jaundice_observed": {"type": "boolean"},
                    "fever": {"type": "boolean"},
                    "fever_temp_f": {"type": "number"},
                    "sleep_pattern": {"type": "string"},
                    "notes": {"type": "string"},
                    "feeding_issue": {"type": "boolean"},
                    "red_flag": {"type": "boolean"},
                },
                required=[],
            ),
        ],
    )


def build_lactation_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="lactation_support",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_lactation"),
        functions=[
            FlowsFunctionSchema(
                name="record_lactation",
                handler=record_lactation,
                description="Log the lactation conversation and route to medication adherence.",
                properties={"note": {"type": "string"}},
                required=["note"],
            ),
        ],
    )


def build_medication_adherence_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="medication_adherence",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_meds"),
        functions=[
            FlowsFunctionSchema(
                name="record_adherence",
                handler=record_adherence,
                description="Record adherence for ONE prescription. Set last=true on the final prescription so we can route forward.",
                properties={
                    "medication": {"type": "string"},
                    "prescription_id": {"type": "string"},
                    "picked_up": {"type": "boolean"},
                    "taking_as_prescribed": {"type": "boolean"},
                    "barrier": {
                        "type": "string",
                        "enum": [
                            "cost",
                            "transport",
                            "side_effects",
                            "forgot",
                            "no_pharmacy",
                            "concerns",
                            "other",
                            "none",
                        ],
                    },
                    "barrier_notes": {"type": "string"},
                    "last": {"type": "boolean"},
                },
                required=["medication", "last"],
            ),
        ],
    )


def build_pharmacy_node(fm: FlowManager, barrier: str) -> NodeConfig:
    return NodeConfig(
        name="pharmacy_routing",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_pharmacy"),
        functions=[
            FlowsFunctionSchema(
                name="log_pharmacy_routing",
                handler=log_pharmacy_routing,
                description=f"Log the pharmacy routing for barrier={barrier}.",
                properties={
                    "barrier": {
                        "type": "string",
                        "enum": ["cost", "transport", "no_pharmacy"],
                    },
                    "summary": {"type": "string"},
                },
                required=["barrier"],
            ),
        ],
    )


def build_social_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="social_screen",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_social"),
        functions=[
            FlowsFunctionSchema(
                name="record_social",
                handler=record_social,
                description="Record social screen answers.",
                properties={
                    "food_secure": {"type": "boolean"},
                    "has_support": {"type": "boolean"},
                    "ipv_concern": {
                        "type": "string",
                        "enum": ["none", "past", "current_safe", "current_active_danger"],
                    },
                },
                required=["food_secure", "has_support", "ipv_concern"],
            ),
        ],
    )


def build_doula_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="doula_handoff",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_doula"),
        functions=[
            FlowsFunctionSchema(
                name="confirm_doula_visit",
                handler=confirm_doula_visit,
                description="Confirm the next doula visit and move on.",
                properties={},
                required=[],
            ),
        ],
    )


def build_csat_node(fm: FlowManager) -> NodeConfig:
    return NodeConfig(
        name="csat_collection",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_csat"),
        functions=[
            FlowsFunctionSchema(
                name="record_csat",
                handler=record_csat,
                description="Record CSAT rating (1-5) + 1-2 sentence summary.",
                properties={
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "qualitative_summary": {"type": "string"},
                },
                required=["rating"],
            ),
            FlowsFunctionSchema(
                name="end_call",
                handler=end_call,
                description="End the call after thanking the patient.",
                properties={},
                required=[],
            ),
        ],
    )


def build_escalation_handoff_node(
    fm: FlowManager, *, severity: str, category: str
) -> NodeConfig:
    return NodeConfig(
        name="escalation_handoff",
        role_message=_role_message(fm),
        task_messages=_task(fm, "postpartum_escalation_handoff"),
        functions=[
            FlowsFunctionSchema(
                name="end_call",
                handler=end_call,
                description=f"End the call after acknowledging the {severity} {category} escalation.",
                properties={},
                required=[],
            ),
        ],
    )


def build_end_node(fm: FlowManager, *, reason: str) -> NodeConfig:
    """Terminal node. post_actions ends the conversation cleanly."""
    return NodeConfig(
        name=f"end:{reason}",
        role_message=_role_message(fm),
        task_messages=[
            {
                "role": "system",
                "content": "The call is wrapping up. In one short sentence, thank the patient warmly and say goodbye. Do not ask any more questions.",
            }
        ],
        post_actions=[{"type": "end_conversation"}],
        functions=[],
    )


# ---------------------------------------------------------------------------
# Global function schemas (registered once on FlowManager)
# ---------------------------------------------------------------------------


def build_global_functions() -> list[FlowsFunctionSchema]:
    return [
        FlowsFunctionSchema(
            name="escalate_to_nurse",
            handler=escalate_to_nurse,
            description="MATERNAL red-flag escalation. Call IMMEDIATELY when the mother mentions heavy bleeding, fever > 100.4F, severe headache, chest pain, leg pain with swelling, shortness of breath, or other emergent maternal symptoms. severity='urgent' for life-threatening, 'warning' for concerning.",
            properties={
                "severity": {"type": "string", "enum": ["urgent", "warning"]},
                "trigger_phrase": {
                    "type": "string",
                    "description": "Short tag for the trigger (e.g. 'heavy bleeding').",
                },
                "trigger_text": {
                    "type": "string",
                    "description": "1-2 sentence quote of what the patient said.",
                },
            },
            required=["severity", "trigger_text"],
        ),
        FlowsFunctionSchema(
            name="escalate_pediatric",
            handler=escalate_pediatric,
            description="NEWBORN red-flag escalation. Call IMMEDIATELY for newborn fever (>100.4F), blue lips, severe lethargy, fewer than 6 wet diapers, refusing to feed, or other emergent newborn symptoms.",
            properties={
                "severity": {"type": "string", "enum": ["urgent", "warning"]},
                "trigger_phrase": {"type": "string"},
                "trigger_text": {"type": "string"},
            },
            required=["severity", "trigger_text"],
        ),
        FlowsFunctionSchema(
            name="escalate_crisis",
            handler=escalate_crisis,
            description="CRISIS escalation. Call IMMEDIATELY for any mention of self-harm, suicidal ideation, or active intimate-partner violence danger.",
            properties={
                "trigger_phrase": {"type": "string"},
                "trigger_text": {"type": "string"},
            },
            required=["trigger_text"],
        ),
        FlowsFunctionSchema(
            name="lookup_patient_billing",
            handler=lookup_patient_billing,
            description="Look up the patient's current bill/balance/status. Use when she asks about billing, costs, or amount owed. Does NOT change the flow — answer briefly and resume.",
            properties={
                "question": {
                    "type": "string",
                    "description": "Her question verbatim, for logs.",
                }
            },
            required=[],
        ),
        FlowsFunctionSchema(
            name="lookup_appointment_history",
            handler=lookup_appointment_history,
            description="Look up past or upcoming appointments. Use when she asks 'when is my next visit?' or 'did I miss anything?'. Does NOT change the flow.",
            properties={
                "time_window": {
                    "type": "string",
                    "enum": ["past", "upcoming", "all"],
                }
            },
            required=[],
        ),
        FlowsFunctionSchema(
            name="lookup_prescription_status",
            handler=lookup_prescription_status,
            description="Look up a prescription's pickup status. Use when she asks about a refill or whether something is ready at the pharmacy. Does NOT change the flow.",
            properties={
                "medication_hint": {
                    "type": "string",
                    "description": "Optional partial medication name to filter on.",
                }
            },
            required=[],
        ),
        FlowsFunctionSchema(
            name="capture_feedback",
            handler=capture_feedback,
            description="Capture open-ended feedback about her care experience. Pick the closest category. Does NOT change the flow.",
            properties={
                "category": {
                    "type": "string",
                    "enum": [
                        "clinical",
                        "billing",
                        "scheduling",
                        "facilities",
                        "staff",
                        "communication",
                        "other",
                    ],
                },
                "note": {"type": "string"},
                "sentiment": {
                    "type": "string",
                    "enum": ["positive", "neutral", "negative"],
                },
            },
            required=["category", "note"],
        ),
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def initial_node(fm: FlowManager) -> NodeConfig:
    """Return the starting node for a freshly initialized FlowManager.

    Call as `await fm.initialize(initial_node(fm))` from the bot entrypoint
    after `set_flow_context(...)` has populated fm.state.
    """
    return build_identity_verify_node(fm)
