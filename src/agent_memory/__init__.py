"""
agent_memory — cross-call MEMORY for the phone voice agent.

Inspired by claude-mem's pipeline: CAPTURE (record turns + tool actions during a call) →
COMPRESS (Nemotron summarizes the call into a short summary + durable facts) → STORE (local
private SQLite at data/agent_memory.db) → RETRIEVE+INJECT (at the next call's start, splice a
compact memory block into the system prompt so the agent remembers the caller).

Integration sketch for src/twilio_bot.py:
    from agent_memory import CallRecorder, recall, init_store
    init_store()                                   # once, e.g. at app startup
    mem = recall(caller)                            # at /ws start; "" for first-time callers
    # ...append `mem` to the system prompt if non-empty...
    rec = CallRecorder(call_sid, direction=mode, caller=caller)
    # ...rec.turn(...) / rec.action(...) during the call (optional)...
    finally:
        rec.finalize(context.messages)             # in the /ws `finally` block

Secrets are NEVER stored: the recorder scrubs the contract's deny-patterns before any text
hits the DB. The summarizer never raises (extractive fallback on any failure) so call
teardown can't be broken by it.
"""

from .recorder import CallRecorder, scrub
from .retrieval import recall
from .store import db_path, init_store, search, set_db_path

__all__ = [
    "CallRecorder",
    "recall",
    "init_store",
    "set_db_path",
    "db_path",
    "search",
    "scrub",
]
