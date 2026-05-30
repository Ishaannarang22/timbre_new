"""
Throwaway OFFLINE test for src/agent_memory — NO network, NO real Nemotron call.

Run:  /Users/node3/projects/voice_fun/.venv/bin/python scripts/test_agent_memory.py

Strategy: point the store at a temp DB under /tmp (so we never touch data/), MONKEYPATCH
summarizer.summarize to return a canned (summary, facts) — so the test never depends on the
network — then exercise the full capture → finalize → retrieve path and assert the call,
turns, actions, and facts landed in the DB and that recall() surfaces them. Also checks the
secrets carve-out: an API key in a turn is redacted, never stored.
"""

import sys
import tempfile
from pathlib import Path

# Make `import agent_memory` work from src/.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import agent_memory
from agent_memory import store, summarizer, recorder, retrieval


def main() -> int:
    # 1) Point the store at a fresh temp DB and init the schema.
    tmpdir = tempfile.mkdtemp(prefix="agent_mem_test_")
    db_file = Path(tmpdir) / "agent_memory.db"
    store.set_db_path(db_file)
    store.init_store()
    assert db_file.exists(), "DB file was not created"
    print(f"[ok] temp DB at {db_file}")

    CALLER = "+18148268818"
    CALL_SID = "CAtest123"
    CANNED_SUMMARY = "Caller chatted about his morning run and asked to play some jazz."
    CANNED_FACTS = [
        {"kind": "preference", "text": "Likes jazz in the morning"},
        {"kind": "fact", "text": "Goes for a run most mornings"},
    ]

    # 2) MONKEYPATCH the summarizer so NO real Nemotron/network call happens.
    summarizer.summarize = lambda turns, actions: (CANNED_SUMMARY, CANNED_FACTS)

    # 3) Capture: create a recorder, add turns + actions (one carries a secret to scrub).
    rec = recorder.CallRecorder(CALL_SID, direction="inbound", caller=CALLER)
    rec.turn("assistant", "Hey Ishaan! What can I do for you?")
    rec.turn("user", "Play some jazz. Oh and my deepgram key is sk-abcdef1234567890abcdef.")
    rec.action("play_music", {"query": "jazz"}, "Now playing jazz on Spotify.")

    # 4) Finalize (uses the monkeypatched summarizer).
    rec.finalize(messages=[])

    # 5) Assert the call + turns + actions + facts are in the DB.
    call = store.get_call(CALL_SID)
    assert call is not None, "call row missing"
    assert call["caller"] == CALLER, f"wrong caller: {call['caller']!r}"
    assert call["direction"] == "inbound", f"wrong direction: {call['direction']!r}"
    assert call["summary"] == CANNED_SUMMARY, f"summary not stored: {call['summary']!r}"
    assert call["ended_at"] is not None, "ended_at not stamped"
    print(f"[ok] call stored: summary={call['summary']!r}")

    turns = store.get_turns(CALL_SID)
    assert len(turns) == 2, f"expected 2 turns, got {len(turns)}"
    print(f"[ok] {len(turns)} turns stored")

    # Secrets carve-out: the API key must be redacted, never persisted.
    joined = " ".join(t["text"] for t in turns)
    assert "sk-abcdef1234567890abcdef" not in joined, "SECRET LEAKED into stored turns!"
    assert "[REDACTED]" in joined, "secret was not redacted"
    print("[ok] secret redacted (carve-out enforced)")

    actions = store.get_actions(CALL_SID)
    assert len(actions) == 1, f"expected 1 action, got {len(actions)}"
    assert actions[0]["tool"] == "play_music", f"wrong tool: {actions[0]['tool']!r}"
    assert "jazz" in actions[0]["args"], f"args not stored: {actions[0]['args']!r}"
    print(f"[ok] action stored: {actions[0]['tool']}({actions[0]['args']})")

    facts = store.all_facts()
    fact_texts = {f["text"] for f in facts}
    assert "Likes jazz in the morning" in fact_texts, f"fact missing: {fact_texts}"
    assert "Goes for a run most mornings" in fact_texts, f"fact missing: {fact_texts}"
    print(f"[ok] {len(facts)} facts stored: {sorted(fact_texts)}")

    # 6) Dedupe / weight-bump: finalize a SECOND call with an overlapping fact.
    rec2 = recorder.CallRecorder("CAtest456", direction="inbound", caller=CALLER)
    summarizer.summarize = lambda turns, actions: (
        "Second call; talked jazz again.",
        [{"kind": "preference", "text": "Likes jazz in the morning"}],  # repeat -> bump
    )
    rec2.finalize(messages=[{"role": "user", "content": "more jazz please"}])
    jazz = [f for f in store.all_facts() if f["text"] == "Likes jazz in the morning"]
    assert len(jazz) == 1, f"fact duplicated instead of bumped: {len(jazz)}"
    assert jazz[0]["weight"] >= 2.0, f"weight not bumped: {jazz[0]['weight']}"
    print(f"[ok] repeated fact deduped + weight bumped to {jazz[0]['weight']}")

    # 7) Retrieve: recall() must surface the summary AND a fact, in a compact block.
    block = retrieval.recall(CALLER)
    assert block, "recall returned empty for a known caller"
    assert CANNED_SUMMARY in block, f"summary missing from recall block:\n{block}"
    assert "Likes jazz in the morning" in block, f"fact missing from recall block:\n{block}"
    assert "sk-abcdef" not in block, "SECRET LEAKED into recall block!"
    print("[ok] recall() block contains summary + fact:")
    print("----- recall block -----")
    print(block)
    print("------------------------")

    # 8) First-time caller -> empty block (so a new caller adds nothing to the prompt).
    assert retrieval.recall("+19990001111") == "", "unknown caller should recall nothing"
    assert agent_memory.recall("") == "", "empty caller should recall nothing"
    print("[ok] unknown/empty caller -> empty recall block")

    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
