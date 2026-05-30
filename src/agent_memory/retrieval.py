"""
retrieval.py — the "retrieve + inject" stage: build a compact memory block for the prompt.

At the START of a call, twilio_bot.py can call `recall(caller)` and splice the returned text
into the system prompt, so the agent walks in already knowing who it's talking to. The block
is deliberately SHORT and SPEAKABLE (it shares the LLM context with everything else and the
agent must stay terse on the phone): a few durable facts + a few recent call summaries.

Ranking
-------
* Facts: weight * recency. Repeats bump weight (see store.upsert_fact), and we add a mild
  recency boost so something said last week outranks an equally-weighted thing from a year
  ago. Top `max_facts` after re-ranking.
* Calls: the most recent `limit_calls` summaries for this caller.

A first-time / unknown caller has nothing stored, so `recall` returns "" — twilio_bot.py
then adds nothing to the prompt and the call behaves exactly as it does today.
"""

from __future__ import annotations

import time

from . import store

# Recency half-life for the fact re-rank: a fact's recency multiplier halves every ~30 days.
_RECENCY_HALFLIFE_SECS = 30 * 24 * 3600.0


def _fact_score(fact: dict, now: float) -> float:
    """weight * recency. Recency is an exponential decay on last_seen so fresh, repeated
    facts win. Weight already encodes how many times we've heard it (upsert bumps it)."""
    weight = float(fact.get("weight") or 1.0)
    last_seen = float(fact.get("last_seen") or fact.get("ts") or now)
    age = max(0.0, now - last_seen)
    recency = 0.5 ** (age / _RECENCY_HALFLIFE_SECS)
    return weight * (0.25 + 0.75 * recency)  # floor so old-but-heavy facts still rank


def _fmt_date(ts: float | None) -> str:
    if not ts:
        return "recently"
    try:
        return time.strftime("%b %d", time.localtime(float(ts)))
    except (ValueError, OSError, OverflowError):
        return "recently"


def recall(caller: str, limit_calls: int = 3, max_facts: int = 8) -> str:
    """Return a compact, speakable memory block for `caller`, or "" if nothing is stored.

    Shape:
        Here is what you remember about this caller. Use it naturally; do not read it aloud
        as a list.
        Things to remember about this caller:
        - <fact>
        - <fact>
        Recent calls:
        - <Mon DD>: <summary>
    """
    if not caller:
        return ""
    try:
        store.init_store()
    except Exception:  # noqa: BLE001
        return ""

    now = time.time()

    # --- calls: most recent summarized calls for THIS caller ---
    # We do this FIRST and treat it as the recognition gate: facts in this schema are durable
    # and not caller-scoped (they were learned from the owner's calls), so we only surface
    # them to a caller we actually RECOGNIZE — i.e. one with prior calls on record. A genuine
    # first-time caller has no calls here, so recall returns "" and adds nothing to the prompt.
    call_lines: list[str] = []
    try:
        for c in store.recent_calls_for_caller(caller, limit=limit_calls):
            summary = (c.get("summary") or "").strip()
            if summary:
                when = _fmt_date(c.get("ended_at") or c.get("started_at"))
                call_lines.append(f"- {when}: {summary}")
    except Exception:  # noqa: BLE001
        call_lines = []

    if not call_lines:
        return ""  # unknown / first-time caller — add nothing.

    # --- facts: pull a generous candidate set, re-rank by weight*recency, take the top N ---
    fact_lines: list[str] = []
    try:
        candidates = store.top_facts(limit=max_facts)
        candidates.sort(key=lambda f: _fact_score(f, now), reverse=True)
        for f in candidates[:max_facts]:
            text = (f.get("text") or "").strip()
            if text:
                fact_lines.append(f"- {text}")
    except Exception:  # noqa: BLE001
        fact_lines = []

    parts = [
        "Here is what you remember about this caller from past calls. Use it naturally to "
        "personalize the conversation; do NOT read it aloud as a list."
    ]
    if fact_lines:
        parts.append("Things to remember about this caller:\n" + "\n".join(fact_lines))
    if call_lines:
        parts.append("Recent calls:\n" + "\n".join(call_lines))
    return "\n".join(parts)
