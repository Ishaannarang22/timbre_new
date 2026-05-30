#!/usr/bin/env python
"""
OFFLINE Twilio Media Streams simulator for src/twilio_bot.py.

WHY THIS EXISTS
---------------
We want to prove the phone agent's "speak-on-connect" path works end-to-end
(Twilio WS handshake -> Pipecat -> Deepgram/Nemotron/Cartesia -> greeting audio
streamed back to the caller) WITHOUT placing a real phone call. This script is a
mock of exactly what Twilio's Media Streams websocket client does:

    1. boots uvicorn (twilio_bot:app) on 127.0.0.1:8080 (unless --no-server)
    2. opens ws://127.0.0.1:8080/ws
    3. sends the real Twilio `connected` then `start` JSON frames
    4. RECEIVES server->client messages and asserts `media` events (base64 mu-law)
       arrive  ==  the fixed greeting was synthesized by Cartesia and is flowing
       back to the "caller". Counts media/clear/mark frames + timing.
    5. closes the ws cleanly and confirms the server tore the call down without
       crashing (scans the captured server log).

It prints a clear PASS/FAIL summary.

IMPORTANT / SAFETY
------------------
- No real Twilio call is placed. We only hit http(s)://127.0.0.1 locally.
- The sim first calls /twiml (with the fake CallSid) to mint a per-call /ws token, exactly
  as Twilio would, since /ws now rejects tokenless connections. /twiml also pre-generates
  the quote (one NVIDIA call), so it's keyed by the fake CallSid and popped in /ws.
- On clean ws close the server runs on_client_disconnected -> task.cancel(), which
  emits a CancelFrame. TwilioFrameSerializer.auto_hang_up will then POST a
  call-hangup to Twilio's REST API for our FAKE CallSid -> Twilio replies 404 (call
  not found). That is harmless: it cannot affect any real call. See report notes.

RUN
---
    .venv/bin/python scripts/sim_twilio_ws.py
    # or, if you already have `uvicorn twilio_bot:app` running on :8080:
    .venv/bin/python scripts/sim_twilio_ws.py --no-server
"""

import argparse
import asyncio
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
HOST = "127.0.0.1"
PORT = int(os.getenv("VOICE_SERVER_PORT", "8080"))
WS_URL = f"ws://{HOST}:{PORT}/ws"
TWIML_URL = f"http://{HOST}:{PORT}/twiml"
HEALTH_URL = f"http://{HOST}:{PORT}/health"
SERVER_LOG = Path("/tmp/sim_twilio_server.log")

# Fake-but-well-formed Twilio ids. Stream/Account/Call SIDs use Twilio's real
# prefixes (MZ/AC/CA) so the serializer and our bot parse them exactly as in prod.
FAKE_STREAM_SID = "MZ00000000000000000000000000000000"
FAKE_ACCOUNT_SID = "AC00000000000000000000000000000000"
FAKE_CALL_SID = "CA00000000000000000000000000000000"

# How long to listen for greeting audio after the start frame.
LISTEN_SECS = float(os.getenv("SIM_LISTEN_SECS", "20"))
# Greeting should begin well within this; used only for a timing sanity note.
GREETING_DEADLINE_SECS = 12.0


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def fetch_ws_token(call_sid: str, timeout: float = 15.0) -> str:
    """Mint a per-call token exactly like Twilio does: hit /twiml with our CallSid, then
    parse the token out of the <Stream><Parameter> it returns. /ws now requires this token, so the sim
    must go through /twiml first to stay green (and to exercise the real auth path).

    NOTE: /twiml also pre-generates the motivational quote (one NVIDIA call), which can take
    a few seconds — hence the generous timeout."""
    url = f"{TWIML_URL}?CallSid={call_sid}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        xml = r.read().decode("utf-8", errors="replace")
    m = re.search(r'<Parameter name="token" value="([A-Za-z0-9_\-]+)"\s*/>', xml)
    if not m:
        raise RuntimeError(f"/twiml did not return a token; body was: {xml[:200]}")
    return m.group(1)


def start_server() -> subprocess.Popen:
    print(f"[sim] starting uvicorn twilio_bot:app on {HOST}:{PORT} …")
    SERVER_LOG.write_text("")
    log = SERVER_LOG.open("w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "twilio_bot:app", "--host", HOST, "--port", str(PORT)],
        cwd=str(SRC_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited early (rc={proc.returncode}); see {SERVER_LOG}")
        if _http_ok(HEALTH_URL):
            print("[sim] server healthy")
            return proc
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy within 30s; see {SERVER_LOG}")


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


async def run_sim() -> dict:
    """Drive one mock Twilio call. Returns a results dict for the summary."""
    res = {
        "ws_handshake": False,
        "media_frames": 0,
        "media_bytes": 0,
        "clear_frames": 0,
        "mark_frames": 0,
        "other_frames": 0,
        "first_media_after_s": None,
        "last_media_after_s": None,
        "ws_error": None,
        "closed_cleanly": False,
    }

    # Mint the per-call /ws token the same way Twilio would: via /twiml. /ws rejects
    # connections without a matching token (security hardening), so the sim must do this.
    try:
        token = fetch_ws_token(FAKE_CALL_SID)
        print("[sim] minted /ws token via /twiml (call auth OK)")
    except Exception as e:  # noqa: BLE001
        res["ws_error"] = f"token mint failed: {type(e).__name__}: {e}"
        return res

    connected = {"event": "connected", "protocol": "Call", "version": "1.0.0"}
    start = {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": FAKE_STREAM_SID,
        "start": {
            "streamSid": FAKE_STREAM_SID,
            "accountSid": FAKE_ACCOUNT_SID,
            "callSid": FAKE_CALL_SID,
            "tracks": ["inbound"],
            # DUAL-CHANNEL: deliver the token via start.customParameters exactly as Twilio
            # delivers a <Stream><Parameter>. /ws now validates from EITHER this channel or
            # the URL query string. We put it HERE (customParameters) to exercise the
            # officially-supported channel that survives even if the query string is dropped.
            "customParameters": {"token": token},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    }

    # Also append ?token=... so this regression test exercises /ws's fallback query-string
    # channel. Real Twilio delivers the token through start.customParameters.
    ws_url = f"{WS_URL}?token={token}"

    try:
        async with websockets.connect(ws_url, max_size=None) as ws:
            res["ws_handshake"] = True
            print("[sim] ws handshake OK")

            await ws.send(json.dumps(connected))
            await ws.send(json.dumps(start))
            print("[sim] sent connected + start frames")

            t0 = time.monotonic()
            deadline = t0 + LISTEN_SECS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                except websockets.ConnectionClosed as e:
                    res["ws_error"] = f"server closed connection: {e}"
                    break

                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    res["other_frames"] += 1
                    continue

                event = msg.get("event")
                dt = time.monotonic() - t0
                if event == "media":
                    payload = msg.get("media", {}).get("payload", "")
                    try:
                        nbytes = len(base64.b64decode(payload))
                    except Exception:
                        nbytes = 0
                    res["media_frames"] += 1
                    res["media_bytes"] += nbytes
                    if res["first_media_after_s"] is None:
                        res["first_media_after_s"] = dt
                        print(f"[sim] FIRST greeting media frame at +{dt:.2f}s ({nbytes} bytes)")
                    res["last_media_after_s"] = dt
                elif event == "clear":
                    res["clear_frames"] += 1
                    print(f"[sim] 'clear' (interruption) at +{dt:.2f}s")
                elif event == "mark":
                    res["mark_frames"] += 1
                    print(f"[sim] 'mark' at +{dt:.2f}s -> {msg.get('mark')}")
                else:
                    res["other_frames"] += 1
                    print(f"[sim] other event '{event}' at +{dt:.2f}s")

            # Clean teardown: tell server we're going away (Twilio sends 'stop'),
            # then close the websocket normally.
            try:
                await ws.send(json.dumps({"event": "stop", "streamSid": FAKE_STREAM_SID}))
            except websockets.ConnectionClosed:
                pass
            await ws.close()
            # websockets>=14 exposes State enum on .state; closed == State.CLOSED (value 3).
            res["closed_cleanly"] = getattr(ws.state, "name", "") == "CLOSED"
    except (OSError, websockets.InvalidHandshake, websockets.ConnectionClosed) as e:
        res["ws_error"] = f"{type(e).__name__}: {e}"
    return res


def scan_log() -> dict:
    """Pull noteworthy lines from the captured server log."""
    out = {"exists": SERVER_LOG.exists(), "tracebacks": [], "errors": [], "teardown": []}
    if not SERVER_LOG.exists():
        return out
    text = SERVER_LOG.read_text(errors="replace")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "traceback (most recent call last)" in low:
            out["tracebacks"].append("\n".join(lines[i : i + 12]))
        elif "| error" in low or " error " in low or "exception" in low:
            out["errors"].append(ln)
        if "call" in low and "finished" in low:
            out["teardown"].append(ln)
        if "client_disconnected" in low or "ws call" in low:
            out["teardown"].append(ln)
    out["full"] = text
    return out


def print_summary(res: dict, log: dict) -> bool:
    def mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    hs_ok = res["ws_handshake"]
    media_ok = res["media_frames"] > 0
    # Teardown is "clean" if we closed the ws and the server logged finishing the
    # call (or at least did not emit a traceback after our close).
    teardown_ok = res["closed_cleanly"] and not log["tracebacks"]

    span = None
    if res["first_media_after_s"] is not None and res["last_media_after_s"] is not None:
        span = res["last_media_after_s"] - res["first_media_after_s"]

    print("\n" + "=" * 64)
    print("OFFLINE TWILIO WS SIMULATION — SUMMARY")
    print("=" * 64)
    print(f"[{mark(hs_ok)}] ws handshake ({WS_URL})")
    print(
        f"[{mark(media_ok)}] greeting audio frames: {res['media_frames']} media events, "
        f"{res['media_bytes']} mu-law bytes"
    )
    if media_ok:
        approx_audio_s = res["media_bytes"] / 8000.0  # 8kHz, 1 byte/sample mu-law
        print(
            f"        first frame @ +{res['first_media_after_s']:.2f}s, "
            f"last @ +{res['last_media_after_s']:.2f}s, stream span {span:.2f}s, "
            f"~{approx_audio_s:.2f}s of audio"
        )
        if res["first_media_after_s"] > GREETING_DEADLINE_SECS:
            print(f"        WARN: first frame later than {GREETING_DEADLINE_SECS:.0f}s")
    print(f"        clear(interruption)={res['clear_frames']} mark={res['mark_frames']} other={res['other_frames']}")
    if res["ws_error"]:
        print(f"        ws note: {res['ws_error']}")
    print(f"[{mark(teardown_ok)}] clean teardown (ws closed + no server traceback)")

    print("\n--- server log: noteworthy ---")
    if log["tracebacks"]:
        print(f"  TRACEBACKS: {len(log['tracebacks'])}")
        for tb in log["tracebacks"][:3]:
            print("  " + tb.replace("\n", "\n  "))
    if log["errors"]:
        print(f"  ERROR/EXCEPTION lines: {len(log['errors'])}")
        for ln in log["errors"][:15]:
            print(f"    {ln}")
    if log["teardown"]:
        print("  teardown lines:")
        for ln in log["teardown"][-5:]:
            print(f"    {ln}")
    if not (log["tracebacks"] or log["errors"]):
        print("  (no tracebacks or error lines)")
    print(f"\n  full server log: {SERVER_LOG}")
    print("=" * 64)

    overall = hs_ok and media_ok and teardown_ok
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    print("=" * 64)
    return overall


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline Twilio Media Streams simulator")
    ap.add_argument("--no-server", action="store_true", help="assume uvicorn already running on :8080")
    args = ap.parse_args()

    server = None
    try:
        if args.no_server:
            if not _http_ok(HEALTH_URL):
                print(f"[sim] --no-server but {HEALTH_URL} is not healthy; aborting.")
                return 2
            print("[sim] using already-running server")
        else:
            server = start_server()

        res = asyncio.run(run_sim())
        # Give the server a beat to log teardown after we closed the ws.
        time.sleep(1.5)
        log = scan_log()
        ok = print_summary(res, log)
        return 0 if ok else 1
    finally:
        if server is not None:
            print("[sim] stopping server …")
            stop_server(server)


if __name__ == "__main__":
    sys.exit(main())
