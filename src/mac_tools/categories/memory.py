"""
categories/memory.py — registry tools that LET THE AGENT use its cross-call memory mid-call.

The agent_memory subsystem (src/agent_memory/) already CAPTURES every call (turns + tool
actions), COMPRESSES it into a short summary + durable facts, and STORES it in a private
SQLite DB. At the START of a call twilio_bot.py also INJECTS a memory block into the system
prompt via recall(). These three tools are the in-call LOOKUP surface on top of that store:

  * recall_memory(query)  — "what do you remember about X?" — substring search over durable
                            facts + recent call summaries; no query → the top facts + latest
                            summaries.
  * remember_this(text)   — "remember that ..." — explicitly save a durable fact (deduped /
                            weight-bumped via the store's upsert_fact). Secret-scrubbed first.
  * list_recent_calls(N)  — "what have we talked about lately?" — the last N calls (date + a
                            one-line summary).

House style (matches src/mac_actions.py + the runner contract):
  * Sync handlers that return a SHORT, speakable string (the agent speaks it verbatim).
  * audit() every action.
  * NEVER raise into the voice pipeline — catch everything and return a friendly string
    (the store can be empty, the DB file may not exist yet, etc.).
  * SECRETS carve-out: remember_this scrubs caller text through agent_memory.scrub BEFORE it
    is stored, so a spoken "my password is hunter2" never lands in the DB.

All three are Risk.SAFE: reading memory is non-destructive, and remembering a fact the caller
explicitly asked us to keep is an additive, recoverable note (no system state changes).
"""

from ..policy import Risk
from ..registry import tool
from ..runner import audit

# agent_memory lives alongside mac_tools on the path (both under src/). Import lazily-safe:
# if the subsystem somehow can't import, the handlers degrade to a friendly string rather than
# breaking tool registration / the call.
try:
    from agent_memory import scrub, search
    from agent_memory import store as _store
    _MEM_OK = True
except Exception:  # noqa: BLE001 — memory must never break tool loading
    _MEM_OK = False


# How many facts / call summaries we'll speak back at most (keep it phone-friendly).
_MAX_SPOKEN_FACTS = 4
_MAX_SPOKEN_CALLS = 3


def _fmt_date(ts) -> str:
    """A short, speakable date for a call (e.g. 'May 28'), or 'recently' if unknown."""
    import time

    if not ts:
        return "recently"
    try:
        return time.strftime("%b %d", time.localtime(float(ts)))
    except (ValueError, OSError, OverflowError):
        return "recently"


@tool(
    name="recall_memory",
    description=(
        "Look up what you remember about the caller from past calls. Pass a topic or keyword "
        "to search your durable notes and recent call summaries (e.g. 'my dog', 'the meeting', "
        "'coffee'). Leave it empty to hear the most important things you remember overall. Use "
        "this when the caller asks 'do you remember...?' or references something from before."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "Topic or keyword to search memory for. Omit for a general recap.",
        }
    },
    risk=Risk.SAFE,
    category="memory",
)
def recall_memory(query: str | None = None) -> str:
    """Search stored durable facts + recent call summaries for text matching `query`
    (case-insensitive substring). No query → top facts + most recent summaries. Returns a
    SHORT speakable string, or a friendly 'nothing noted' line. Never raises."""
    q = (query or "").strip()
    if not _MEM_OK:
        msg = "My memory isn't available right now."
        audit("recall_memory", {"query": q}, msg)
        return msg
    try:
        hits = search(q, limit=8)
    except Exception as e:  # noqa: BLE001
        msg = "Sorry, I couldn't check my memory right now."
        audit("recall_memory", {"query": q}, f"error: {e}")
        return msg

    facts = hits.get("facts", [])[:_MAX_SPOKEN_FACTS]
    calls = hits.get("calls", [])[:_MAX_SPOKEN_CALLS]

    if not facts and not calls:
        if q:
            msg = f"I don't have anything noted about {q} yet."
        else:
            msg = "I don't have anything noted yet."
        audit("recall_memory", {"query": q}, msg)
        return msg

    pieces: list[str] = []
    for f in facts:
        text = (f.get("text") or "").strip()
        if text:
            pieces.append(text.rstrip("."))
    for c in calls:
        summary = (c.get("summary") or "").strip()
        if summary:
            when = _fmt_date(c.get("ended_at") or c.get("started_at"))
            pieces.append(f"on {when}, {summary.rstrip('.')}")

    if not pieces:
        msg = "I don't have anything noted about that yet."
        audit("recall_memory", {"query": q}, msg)
        return msg

    msg = "Here's what I remember: " + "; ".join(pieces) + "."
    audit("recall_memory", {"query": q, "facts": len(facts), "calls": len(calls)}, msg)
    return msg


@tool(
    name="remember_this",
    description=(
        "Save something durable that the caller wants you to remember for future calls, e.g. "
        "'remember that I take my coffee black' or 'remember my flight is on Friday'. Pass the "
        "thing to remember as plain text. Use this when the caller explicitly asks you to "
        "remember, note, or keep track of something."
    ),
    properties={
        "text": {
            "type": "string",
            "description": "The fact or preference to remember, in plain words.",
        },
        "kind": {
            "type": "string",
            "enum": ["note", "preference", "fact", "detail"],
            "description": "Optional category for the memory. Defaults to 'note'.",
        },
    },
    required=["text"],
    risk=Risk.SAFE,
    category="memory",
)
def remember_this(text: str = "", kind: str = "note") -> str:
    """Explicitly save a durable fact via the store's upsert_fact (dedupe / weight-bump on
    repeats). Caller text is SECRET-SCRUBBED first so we never persist secrets. Never raises."""
    raw = str(text or "").strip()
    if not raw:
        msg = "What would you like me to remember?"
        audit("remember_this", {"text": raw}, msg)
        return msg
    if not _MEM_OK:
        msg = "My memory isn't available right now, so I can't save that."
        audit("remember_this", {"text": raw}, msg)
        return msg

    # SECRETS carve-out: scrub BEFORE storing. If scrubbing redacts the whole thing (a pure
    # secret), refuse rather than store a useless "[REDACTED]" note.
    try:
        clean = scrub(raw)
    except Exception as e:  # noqa: BLE001
        msg = "Sorry, I couldn't save that right now."
        audit("remember_this", {"text": raw}, f"scrub error: {e}")
        return msg

    # If scrubbing redacted ANY part of it, it contained secret material — refuse rather than
    # store a half-redacted, useless note. (Belt-and-suspenders on the hard secrets carve-out.)
    if not clean or "[REDACTED]" in clean:
        msg = "I won't store that — it looks like a secret, and I don't keep those."
        audit("remember_this", {"text": "[REDACTED]"}, msg)
        return msg

    k = str(kind or "note").strip() or "note"
    try:
        _store.init_store()
        _store.upsert_fact(k, clean)
    except Exception as e:  # noqa: BLE001
        msg = "Sorry, I couldn't save that right now."
        audit("remember_this", {"text": clean, "kind": k}, f"error: {e}")
        return msg

    msg = "Got it, I'll remember that."
    audit("remember_this", {"text": clean, "kind": k}, msg)
    return msg


@tool(
    name="list_recent_calls",
    description=(
        "Recap the last few calls you've had — each with its date and a one-line summary. Use "
        "this when the caller asks 'what have we talked about lately?' or 'what was our last "
        "call about?'. Optionally pass how many recent calls to recap (default 3)."
    ),
    properties={
        "limit": {
            "type": "integer",
            "description": "How many recent calls to recap (default 3, max 5).",
        }
    },
    risk=Risk.SAFE,
    category="memory",
)
def list_recent_calls(limit: int = 3) -> str:
    """Return a short summary of the last N calls (date + one-line summary). Never raises."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(5, n))

    if not _MEM_OK:
        msg = "My memory isn't available right now."
        audit("list_recent_calls", {"limit": n}, msg)
        return msg

    try:
        _store.init_store()
        calls = _store.recent_calls(limit=n)
    except Exception as e:  # noqa: BLE001
        msg = "Sorry, I couldn't look up recent calls right now."
        audit("list_recent_calls", {"limit": n}, f"error: {e}")
        return msg

    if not calls:
        msg = "We haven't had any calls I've made notes on yet."
        audit("list_recent_calls", {"limit": n}, msg)
        return msg

    lines: list[str] = []
    for c in calls:
        summary = (c.get("summary") or "").strip()
        if not summary:
            continue
        when = _fmt_date(c.get("ended_at") or c.get("started_at"))
        lines.append(f"{when}: {summary.rstrip('.')}")

    if not lines:
        msg = "We haven't had any calls I've made notes on yet."
        audit("list_recent_calls", {"limit": n}, msg)
        return msg

    if len(lines) == 1:
        msg = f"Our most recent call — {lines[0]}."
    else:
        msg = f"The last {len(lines)} calls — " + "; ".join(lines) + "."
    audit("list_recent_calls", {"limit": n, "count": len(lines)}, msg)
    return msg
