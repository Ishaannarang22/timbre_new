"""
recorder.py — the "capture" stage, wired to a single phone call.

A `CallRecorder` is created once per call (call_sid + direction + caller). During the call
the pipeline feeds it turns and tool-invocations; at the end (`/ws` `finally` block) it
finalizes: persist the transcript, ask the summarizer to compress it, store the call summary,
and upsert durable facts (dedupe + weight-bump on repeats).

SECRETS CARVE-OUT (CONTRACT.md, owner decision: "never"): we must never store secrets. So
before ANYTHING text-bearing (a turn, an action's args/result, a fact, a summary) reaches the
DB it goes through `_scrub()`, which redacts the same deny-patterns the validator enforces:
passwords, API tokens/keys, .env/dotfile credentials, SSH/GPG private keys, Keychain dumps.
A redacted line is still stored (so the conversation shape survives) but the secret value is
replaced with [REDACTED]. If a whole line is essentially a secret, it's dropped.

Everything here is best-effort and swallow-safe: recording must NEVER break the live call.
"""

from __future__ import annotations

import json
import re
import time

from . import store

# --- Secret deny-patterns (mirror CONTRACT.md's carve-out) --------------------------------
# These match a *value* that should never be persisted. We redact the matched span, not the
# whole turn, so "my deepgram key is sk-abc123" becomes "my deepgram key is [REDACTED]".
_SECRET_PATTERNS: list[re.Pattern] = [
    # Provider key shapes: nvapi-…, sk-…, AKIA… (AWS), xoxb-/xoxp- (Slack), ghp_… (GitHub),
    # AC… (Twilio SID), and Google AIza keys.
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"\bAC[0-9a-f]{32}\b", re.IGNORECASE),  # Twilio Account SID
    # Z.AI key shape: 32 hex chars, a dot, then 16 alnum (hex32.alnum16). Caught BEFORE the
    # bare-40-hex rule so the whole token (incl. the suffix) is redacted as one span.
    re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16}\b"),
    # JWT-like / dotted-triple high-entropy tokens (e.g. eyJ….….…) — three base64url segments.
    re.compile(r"\b[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # Bare 40-hex tokens (Deepgram/SHA-style API keys). 40 hex digits is far past any English
    # word, so this won't touch ordinary speech.
    re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE),
    re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.DOTALL),
    # "password/token/secret/api[_ ]key/passphrase = <value>" (assignment-ish or spoken).
    re.compile(
        r"\b(pass(?:word|phrase)|secret|api[ _]?key|access[ _]?key|auth[ _]?token|token|bearer)\b"
        r"\s*(?:=|:|is|are|was)?\s*['\"]?([^\s'\"]{6,})['\"]?",
        re.IGNORECASE,
    ),
    # Catch-all for an UNLABELED long high-entropy token: a 32+-char run that mixes letters AND
    # digits (and may include _ or -). The mixed-case/digit requirement (enforced below via the
    # entropy guard) keeps ordinary long words ("supercalifragilistic...") from matching, while
    # catching keys like a Cartesia/Deepgram token spoken or echoed without a label.
    re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*[0-9])[A-Za-z0-9_\-]{32,}\b"),
]

# If a line is DOMINATED by secret material (env/ssh dumps, keychain output), drop it whole.
_SECRET_LINE_MARKERS: list[re.Pattern] = [
    re.compile(r"\.env\b", re.IGNORECASE),
    re.compile(r"\.ssh/id_", re.IGNORECASE),
    re.compile(r"\blogin\.keychain\b", re.IGNORECASE),
    re.compile(r"security\s+find-generic-password", re.IGNORECASE),
    re.compile(r"\bid_(rsa|ed25519|dsa|ecdsa)\b", re.IGNORECASE),
]

_REDACTED = "[REDACTED]"


def _scrub(text: str | None) -> str:
    """Redact secret values from a string before it is stored. Returns "" if the whole line
    is essentially a secret dump (so we never persist it at all)."""
    if not text:
        return ""
    s = str(text)
    # Whole-line drop for unmistakable secret dumps.
    for marker in _SECRET_LINE_MARKERS:
        if marker.search(s):
            return _REDACTED
    out = s
    for pat in _SECRET_PATTERNS:
        # For the labelled "password = X" pattern, redact only the captured VALUE (group 2);
        # for the raw key-shape patterns, redact the whole match.
        if pat.groups >= 2:
            out = pat.sub(lambda m: m.group(0).replace(m.group(2), _REDACTED), out)
        else:
            out = pat.sub(_REDACTED, out)
    return out


# Public alias of the secret-scrubber so other modules (e.g. the memory lookup tools and the
# CLI) can scrub caller-supplied text BEFORE it is stored, without reaching into a private name.
def scrub(text: str | None) -> str:
    """Public secret-scrubber: redact secret values from `text` before it is stored. Returns ""
    if the whole line is essentially a secret dump. (Thin alias of the internal _scrub.)"""
    return _scrub(text)


def _scrub_args(args) -> str:
    """Normalize + scrub a tool's arguments to a stored string. Accepts dict/str/None."""
    if args is None:
        return ""
    if isinstance(args, (dict, list)):
        try:
            raw = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raw = str(args)
    else:
        raw = str(args)
    return _scrub(raw)


class CallRecorder:
    """Per-call capture handle. Construct at call start; feed it during; finalize at the end.

    Usage (inside twilio_bot.py /ws):
        rec = CallRecorder(call_sid, direction=mode, caller=caller_e164)
        ...
        rec.turn("user", transcript)          # as turns happen (optional — finalize also reads
        rec.action("set_volume", {"level":40}, "Set volume to 40.")   #   the full context)
        ...
        finally:
            rec.finalize(context.messages)
    """

    def __init__(self, call_sid: str, direction: str = "outbound", caller: str = "") -> None:
        self.call_sid = call_sid
        self.direction = direction
        self.caller = caller or ""
        self._finalized = False
        # Make sure schema exists + register the call row so turns/actions have a parent.
        try:
            store.init_store()
            store.upsert_call(
                call_sid, started_at=time.time(), direction=direction, caller=self.caller
            )
        except Exception:  # noqa: BLE001 — never let memory bookkeeping break a call
            pass

    # ----------------------------------------------------------------- live capture
    def turn(self, role: str, text: str) -> None:
        """Record one conversational turn (scrubbed). Best-effort, never raises."""
        try:
            clean = _scrub(text)
            if clean:
                store.add_turn(self.call_sid, role, clean)
        except Exception:  # noqa: BLE001
            pass

    def action(self, tool: str, args, result: str) -> None:
        """Record one tool invocation (args + result scrubbed). Best-effort, never raises."""
        try:
            store.add_action(self.call_sid, tool, _scrub_args(args), _scrub(result))
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------------- finalize
    def finalize(self, messages: list[dict] | None = None) -> None:
        """End-of-call: persist any transcript turns from `messages`, summarize, store the
        summary, and upsert facts. Idempotent (guarded) and never raises into the pipeline.

        `messages` is the live LLMContext.messages list (system/user/assistant turns). We
        store the user/assistant turns from it IF we haven't already captured them via .turn()
        — i.e. when the recorder was only finalized (the simplest integration). Duplicate
        protection: if turns already exist for this call we don't re-add from messages.
        """
        if self._finalized:
            return
        self._finalized = True
        try:
            # 1) Make sure the transcript is stored. Prefer live-captured turns; otherwise
            #    backfill from the context messages (skipping the system prompt).
            existing = store.get_turns(self.call_sid)
            if not existing and messages:
                for m in messages:
                    role = m.get("role")
                    if role not in ("user", "assistant"):
                        continue  # system prompt / tool plumbing not part of the transcript
                    content = m.get("content")
                    if not isinstance(content, str):
                        continue  # tool-call message parts etc. — skip non-text content
                    clean = _scrub(content)
                    if clean:
                        store.add_turn(self.call_sid, role, clean)

            turns = store.get_turns(self.call_sid)
            actions = store.get_actions(self.call_sid)

            # 2) Compress (Nemotron, or extractive fallback — summarizer never raises).
            from . import summarizer

            summary, facts = summarizer.summarize(turns, actions)
            summary = _scrub(summary)  # defense-in-depth: scrub the model output too

            # 3) Store the summary + stamp end time.
            store.finalize_call(self.call_sid, ended_at=time.time(), summary=summary)

            # 4) Upsert durable facts (dedupe / weight-bump on repeats), scrubbing each.
            for f in facts or []:
                kind = (f.get("kind") or "fact").strip() or "fact"
                ftext = _scrub(f.get("text"))
                if ftext and ftext != _REDACTED:
                    store.upsert_fact(kind, ftext, source_call=self.call_sid)
        except Exception:  # noqa: BLE001 — teardown path; swallow everything
            pass
