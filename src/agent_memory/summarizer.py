"""
summarizer.py — the "compress" stage: turn a finished call into (summary, facts).

This is the only part of agent_memory that talks to a model. It uses NVIDIA **Nemotron**
(the project's "brain") via the OpenAI-compatible endpoint — NOT GLM/Z.AI. (The GLM key is
reserved for the mac_tools factory; per the contract Nemotron is the voice-path model and
GLM is ONLY for tool authoring.) One short, NON-streaming, low-token call with thinking
disabled (`enable_thinking: False`), to keep it cheap and fast.

Robustness is the whole point of this module: summarizing happens in the call's `finally`
block, so it must NEVER raise and must NEVER hang the teardown. On ANY trouble — no API key,
endpoint stall, bad JSON, anything — we fall back to a trivial EXTRACTIVE summary built from
the first and last user lines, and return [] facts. A call still gets remembered (turns +
actions are stored regardless); we just lose the polished summary that turn.
"""

from __future__ import annotations

import json
import os

# Mirror call_me.py / twilio_bot.py: NVIDIA Nemotron over the OpenAI-compatible endpoint.
_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"

_SYSTEM = (
    "You compress a phone call into durable memory for a voice assistant. "
    "Return STRICT JSON only, no prose, no markdown fences. Schema:\n"
    '{"summary": "<=2 sentences, third person, what happened on this call>", '
    '"facts": [{"kind": "preference|fact|detail", "text": "<short durable fact about the '
    'caller worth remembering next time>"}]}\n'
    "Keep facts genuinely durable (preferences, recurring topics, personal details) — not "
    "one-off call mechanics. At most 5 facts. If nothing is worth remembering, facts = []."
)


def _extractive_fallback(turns: list[dict]) -> str:
    """No-LLM summary: stitch the first and last USER lines. Always works, never raises.
    `turns` are dicts shaped like {"role": ..., "text": ...}."""
    user_lines = [
        (t.get("text") or "").strip()
        for t in turns
        if t.get("role") == "user" and (t.get("text") or "").strip()
    ]
    if not user_lines:
        return "Brief call; nothing notable captured."
    if len(user_lines) == 1:
        return f"Caller said: {user_lines[0][:200]}"
    return f"Caller opened with: {user_lines[0][:160]} … and ended with: {user_lines[-1][:160]}"


def _render_transcript(turns: list[dict], actions: list[dict]) -> str:
    """Compact transcript + action log fed to Nemotron. Bounded so we never send a giant
    prompt to the free endpoint (we keep the last ~40 turns / 20 actions)."""
    lines: list[str] = []
    for t in turns[-40:]:
        role = t.get("role", "?")
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    block = "\n".join(lines) if lines else "(no transcript)"
    if actions:
        act = "\n".join(
            f"- {a.get('tool')}({a.get('args')}) -> {a.get('result')}" for a in actions[-20:]
        )
        block += "\n\nActions taken during the call:\n" + act
    return block


def summarize(turns: list[dict], actions: list[dict]) -> tuple[str, list[dict]]:
    """Compress (turns, actions) -> (summary_str, facts). NEVER raises.

    On any failure returns (extractive_fallback, []). `facts` items are {"kind","text"}.
    """
    fallback = (_extractive_fallback(turns), [])

    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        return fallback

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=_BASE_URL).with_options(
            timeout=8.0, max_retries=1
        )
        resp = client.chat.completions.create(
            model=os.getenv("NVIDIA_LLM_MODEL", _DEFAULT_MODEL),
            temperature=0.2,
            top_p=0.95,
            max_tokens=400,
            # enable_thinking False: nemotron-3-nano otherwise reasons into reasoning_content,
            # which both wastes tokens and can starve the visible JSON (see project memory).
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _render_transcript(turns, actions)},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        summary, facts = _parse(raw)
        if not summary:
            return fallback
        return summary, facts
    except Exception:  # noqa: BLE001 — summarizing runs in the call teardown; never raise
        return fallback


def _parse(raw: str) -> tuple[str, list[dict]]:
    """Pull {summary, facts} out of the model output, tolerating stray fences/prose by
    grabbing the outermost {...}. Returns ("", []) if it can't be trusted."""
    if not raw:
        return "", []
    text = raw.strip()
    # Strip a ```json fence if the model added one despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # Fall back to the first/last brace if there's surrounding prose.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "", []
    if not isinstance(data, dict):
        return "", []

    summary = str(data.get("summary") or "").strip()

    facts_out: list[dict] = []
    raw_facts = data.get("facts")
    if isinstance(raw_facts, list):
        for f in raw_facts[:5]:
            if isinstance(f, dict):
                kind = str(f.get("kind") or "fact").strip() or "fact"
                ftext = str(f.get("text") or "").strip()
            elif isinstance(f, str):
                kind, ftext = "fact", f.strip()
            else:
                continue
            if ftext:
                facts_out.append({"kind": kind, "text": ftext})
    return summary, facts_out
