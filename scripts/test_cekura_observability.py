"""Offline test for Cekura transcript export and optional Twilio recording upload."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

import cekura_observability as cekura


class _Response:
    content = b"wav-data"

    def raise_for_status(self):
        return None


def main() -> int:
    os.environ["CEKURA_API_KEY"] = "cekura-test-key"
    os.environ["CEKURA_AGENT_ID"] = "123"
    os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
    os.environ["TWILIO_AUTH_TOKEN"] = "twilio-test-token"

    posts = []
    gets = []
    cekura.requests.post = lambda *args, **kwargs: posts.append((args, kwargs)) or _Response()
    cekura.requests.get = lambda *args, **kwargs: gets.append((args, kwargs)) or _Response()

    cekura.export_transcript(
        "CAtranscript",
        [
            {"role": "system", "content": "do not export"},
            {"role": "user", "content": "my api key is sk-abcdef1234567890abcdef"},
            {"role": "assistant", "content": "How can I help?"},
        ],
        caller="14155550100",
        mode="inbound",
        authorized=True,
    )
    payload = posts.pop()[1]["json"]
    assert payload["agent"] == 123
    assert payload["metadata"]["mode"] == "inbound"
    assert len(payload["transcript_json"]) == 2
    assert "sk-abcdef" not in str(payload)
    assert "[REDACTED]" in str(payload)
    assert not gets
    print("[ok] scrubbed transcript-only call exported")

    os.environ["CEKURA_RECORD_CALLS"] = "true"
    cekura.export_transcript(
        "CAaudio",
        [{"role": "user", "content": "hello"}],
        caller="14155550100",
        mode="outbound",
        authorized=True,
    )
    assert not posts, "audio-enabled export must wait for Twilio's recording callback"
    cekura.recording_completed(
        "CAaudio",
        "https://api.twilio.com/2010-04-01/Accounts/ACtest/Recordings/RE123",
    )
    assert gets[0][0][0].endswith(".wav")
    assert gets[0][1]["auth"] == ("ACtest", "twilio-test-token")
    assert posts[0][1]["files"]["voice_recording"][2] == "audio/wav"
    print("[ok] audio-enabled call waited for recording and uploaded WAV")

    try:
        cekura._twilio_recording_wav_url("https://example.com/steal-creds")
    except ValueError:
        pass
    else:
        raise AssertionError("non-Twilio recording URL was accepted")
    print("[ok] non-Twilio recording URL rejected before authenticated download")

    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
