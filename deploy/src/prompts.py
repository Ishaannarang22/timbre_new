"""
Prompt loader — keeps system prompts OUT of the pipeline code.

All prompts live in `prompts/prompts.json` (project root), keyed by the agent
that uses them. Key convention: <milestone>_<agent>, e.g. "m0_local_mic_voice_agent",
"m1_twilio_voice_agent". Edit that JSON to change behavior — no need to touch bot code.

Usage:
    from prompts import load_prompt
    M0_LOCAL_MIC_VOICE_AGENT_SYSTEM_PROMPT = load_prompt("m0_local_mic_voice_agent")
"""

import json
from pathlib import Path

# prompts.json sits at the project root: <repo>/prompts/prompts.json
# (this file is <repo>/src/prompts.py, so go up one level then into prompts/).
_PROMPTS_PATH = Path(__file__).resolve().parent.parent / "prompts" / "prompts.json"


def load_prompt(name: str) -> str:
    """Return the prompt text stored under `name` in prompts/prompts.json.

    Raises a clear error if the file or the key is missing, so a typo fails
    loudly instead of silently sending an empty system prompt to the LLM.
    """
    try:
        prompts = json.loads(_PROMPTS_PATH.read_text())
    except FileNotFoundError as e:
        raise SystemExit(f"Prompt file not found: {_PROMPTS_PATH}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"Prompt file {_PROMPTS_PATH} is not valid JSON: {e}") from e

    if name not in prompts:
        available = ", ".join(sorted(prompts)) or "(none)"
        raise SystemExit(
            f"No prompt named '{name}' in {_PROMPTS_PATH}. Available: {available}"
        )
    return prompts[name]
