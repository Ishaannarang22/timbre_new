"""
Morning-call orchestrator — what the 7 AM cron actually runs.

It tries for the *rich* experience and degrades gracefully:

  0. PREFER a WARM tunnel: if the persistent daemon (scripts/serve_persistent.py,
     run by com.timbre.tunnel) has published a healthy public URL to
     logs/tunnel_url.txt, place the call straight at that already-routing
     <warm-url>/twiml — no cold-start gamble. This is the reliable 7 AM path.
  1. COLD fallback: if there's no warm URL (or it's unhealthy), boot voice_server
     (FastAPI) on localhost ourselves.
  2. Open a cloudflared quick-tunnel to get a public https URL (with retries).
  3. Place a Twilio call pointed at <tunnel>/twiml  → a real two-way conversation.
  4. If ANY of 1-3 fails, fall back to call_me.py's inline-TwiML monologue so a warm,
     talking call still lands. The morning call must never silently no-op.

Always tears down any server + tunnel WE started at the end (the warm daemon's
tunnel is owned by the daemon and is left running).

Cloudflare quick-tunnels are unreliable *per run*: the URL is printed within a
second, but the edge frequently never starts routing to the local origin. We
defend against that by retrying tunnel creation: each attempt gets a *fresh*
cloudflared process and a *bounded* window to make <url>/health return 200. A
tunnel that doesn't propagate in time is killed and a brand-new one is tried.
The server (uvicorn) is started once and kept up across all tunnel attempts.

Run:  .venv/bin/python src/run_morning_call.py
Optional: --keep-alive  (bring up server+tunnel, hold them open, print the URL,
          and wait for Ctrl-C instead of placing a call — handy for manual
          testing / a long-lived dev session). Default behavior is unchanged.
"""

import http.client
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from twilio.rest import Client

SRC_DIR = Path(__file__).resolve().parent
load_dotenv(SRC_DIR.parent / ".env")

VENV_PY = sys.executable  # the venv python running this script
PORT = int(os.getenv("VOICE_SERVER_PORT", "8080"))
CF_BIN = shutil.which("cloudflared") or "/opt/homebrew/bin/cloudflared"

ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
FROM_NUMBER = os.environ["TWILIO_PHONE_NUMBER"]
TO_NUMBER = os.environ.get("TARGET_PHONE_NUMBER", "+18148268818")

CF_LOG_DIR = Path("/tmp")
CF_LOG = CF_LOG_DIR / "timbre_cloudflared.log"  # symlinked/copied latest attempt

# The warm-tunnel daemon (scripts/serve_persistent.py) publishes its live public
# URL here. Preferring it skips the cold-start tunnel gamble entirely.
WARM_URL_FILE = SRC_DIR.parent / "logs" / "tunnel_url.txt"

# Tunnel-retry tuning. Each attempt gets its own fresh cloudflared process and a
# bounded window to actually route /health. Total worst-case ~= attempts * (
# URL_WAIT_S + EDGE_WAIT_S).
TUNNEL_ATTEMPTS = int(os.getenv("TUNNEL_ATTEMPTS", "4"))
URL_WAIT_S = float(os.getenv("TUNNEL_URL_WAIT_S", "15"))      # time to print the URL
EDGE_WAIT_S = float(os.getenv("TUNNEL_EDGE_WAIT_S", "30"))    # time for edge to route

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _resolve_via_dns(host: str) -> str:
    """Resolve a host via a DIRECT DNS query (/usr/bin/host), bypassing the macOS
    stub resolver (mDNSResponder), which frequently negative-caches freshly-created
    *.trycloudflare.com names even though they're live in DNS. Returns "" on failure."""
    try:
        out = subprocess.run(
            ["/usr/bin/host", "-t", "A", host],
            capture_output=True, text=True, timeout=6,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"has address (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def _http_ok(url: str, timeout: float = 4.0) -> bool:
    """True iff GET <url> returns 200. Resilient to the macOS stub-resolver quirk:
    on a name-resolution failure, retry by connecting to a directly-resolved IP
    while preserving SNI + Host header (Twilio's own resolver is unaffected, so this
    avoids a FALSE negative that would wrongly reject a good warm tunnel)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), socket.gaierror):
            return False
    except Exception:
        return False

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    ip = _resolve_via_dns(host)
    if not ip:
        return False
    port = parts.port or 443
    tls = None
    try:
        ctx = ssl.create_default_context()
        raw = socket.create_connection((ip, port), timeout=timeout)
        tls = ctx.wrap_socket(raw, server_hostname=host)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        conn.sock = tls
        conn.request("GET", parts.path or "/", headers={"Host": host})
        resp = conn.getresponse()
        ok = resp.status == 200
        resp.read()
        conn.close()
        return ok
    except Exception:
        if tls is not None:
            try:
                tls.close()
            except Exception:
                pass
        return False


def _wait(predicate, deadline_s: float, interval: float = 1.0) -> bool:
    end = time.time() + deadline_s
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def read_warm_url(path: Path = WARM_URL_FILE) -> str:
    """Read the public URL published by the warm-tunnel daemon, or "" if absent.

    The file format is two lines: the https://*.trycloudflare.com URL, then a unix
    timestamp. We only return the URL (well-formed trycloudflare URL); the caller
    decides whether it's actually routing by hitting /health.
    """
    try:
        first = path.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
    return first if URL_RE.fullmatch(first) else ""


def select_call_url() -> "tuple[str, str]":
    """Decide which URL the call WOULD use, without placing a call or starting a
    cold tunnel. Pure selection logic — used by the dry-run check and by main().

    Returns (mode, url):
      ("warm", <url>)  — a healthy warm tunnel from logs/tunnel_url.txt
      ("cold", "")     — no usable warm URL; caller must do the cold-start path
    """
    warm = read_warm_url()
    # Retry /health for a short window rather than giving up on the first miss: the
    # macOS stub resolver intermittently negative-caches the fresh *.trycloudflare.com
    # name (~1 in 5 single probes), which would FALSELY send us to cold-start (and risk
    # the rejected Polly fallback) even though the warm tunnel is routing fine. A few
    # retries absorb that transient and keep us on the preferred Cartesia path.
    if warm and _wait(lambda: _http_ok(f"{warm}/health"), 15, interval=2.0):
        return "warm", warm
    if warm:
        print(f"[orch] warm URL {warm} present but /health not 200 after retries — will cold-start")
    else:
        print("[orch] no warm URL published — will cold-start")
    return "cold", ""


def start_server() -> subprocess.Popen:
    print("[orch] starting voice_server …")
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "twilio_bot:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(SRC_DIR),
        # Own session so we can signal the whole group on teardown.
        start_new_session=True,
    )
    if not _wait(lambda: _http_ok(f"http://127.0.0.1:{PORT}/health"), 25):
        raise RuntimeError("voice_server did not become healthy")
    print("[orch] voice_server healthy")
    return proc


def _kill_proc(proc: "subprocess.Popen | None") -> None:
    """Terminate a process *and its session group* so no children orphan.

    cloudflared can spawn helpers; SIGTERM to the process-group reaps them all.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pass


def _spawn_one_tunnel(attempt: int) -> tuple[subprocess.Popen, Path]:
    """Start one fresh cloudflared quick-tunnel. Returns (proc, its_logfile)."""
    log_path = CF_LOG_DIR / f"timbre_cloudflared.attempt{attempt}.log"
    log_path.write_text("")
    log = log_path.open("w")
    proc = subprocess.Popen(
        [CF_BIN, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group → clean group-kill
    )
    return proc, log_path


def _parse_url(log_path: Path) -> str:
    m = URL_RE.search(log_path.read_text())
    return m.group(0) if m else ""


def start_tunnel_with_retries(
    health_path: str = "/health",
    attempts: int = TUNNEL_ATTEMPTS,
    url_wait_s: float = URL_WAIT_S,
    edge_wait_s: float = EDGE_WAIT_S,
    spawned: "list[subprocess.Popen] | None" = None,
) -> tuple[subprocess.Popen, str]:
    """Bring up a cloudflared quick-tunnel that *actually routes* to the origin.

    Each attempt: spawn a fresh cloudflared, wait (bounded) for it to print a
    trycloudflare URL, then wait (bounded) for <url><health_path> to return 200.
    A tunnel that fails either gate is killed and the next attempt starts clean.

    `spawned` (if provided) is appended with every cloudflared Popen we create so
    the caller can guarantee teardown of all of them — including failed ones.

    Returns (live_proc, public_url). Raises RuntimeError if all attempts fail.
    """
    if spawned is None:
        spawned = []

    last_err = "no attempts ran"
    for attempt in range(1, attempts + 1):
        print(f"[orch] tunnel attempt {attempt}/{attempts} — spawning fresh cloudflared …")
        proc, log_path = _spawn_one_tunnel(attempt)
        spawned.append(proc)

        # Mirror the active attempt to the well-known log path for humans/cron.
        try:
            if CF_LOG.exists() or CF_LOG.is_symlink():
                CF_LOG.unlink()
            CF_LOG.symlink_to(log_path)
        except OSError:
            pass

        public_url = ""

        def _got_url() -> bool:
            nonlocal public_url
            if proc.poll() is not None:  # cloudflared died early → stop waiting
                return False
            public_url = _parse_url(log_path)
            return bool(public_url)

        if not _wait(_got_url, url_wait_s):
            last_err = f"attempt {attempt}: no URL within {url_wait_s:.0f}s (log {log_path})"
            print(f"[orch] {last_err} — killing this tunnel, retrying")
            _kill_proc(proc)
            continue

        print(f"[orch] attempt {attempt}: URL = {public_url} — waiting up to "
              f"{edge_wait_s:.0f}s for edge to route {health_path} …")

        if _wait(lambda: _http_ok(f"{public_url}{health_path}"), edge_wait_s, interval=2.0):
            print(f"[orch] attempt {attempt}: tunnel LIVE → {public_url} (edge routing OK)")
            return proc, public_url

        last_err = f"attempt {attempt}: {public_url}{health_path} never reached 200 in {edge_wait_s:.0f}s"
        print(f"[orch] {last_err} — killing this tunnel, retrying")
        _kill_proc(proc)

    raise RuntimeError(f"all {attempts} tunnel attempts failed; last: {last_err}")


def place_interactive_call(public_url: str) -> str:
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    call = client.calls.create(
        to=TO_NUMBER, from_=FROM_NUMBER, url=f"{public_url}/twiml", method="POST"
    )
    print(f"[orch] interactive call placed: {call.sid}")
    return _poll(client, call.sid)


def place_fallback_call() -> str:
    print("[orch] FALLBACK → inline-TwiML monologue")
    from call_me import build_twiml, generate_quote

    twiml = build_twiml(generate_quote())
    client = Client(ACCOUNT_SID, AUTH_TOKEN)
    call = client.calls.create(to=TO_NUMBER, from_=FROM_NUMBER, twiml=twiml)
    print(f"[orch] fallback call placed: {call.sid}")
    return _poll(client, call.sid)


def _poll(client: Client, sid: str) -> str:
    # P2-2: budget must comfortably exceed MAX_CALL_SECS (150s) + ring time so we
    # don't stop polling while the call is still live and report a non-terminal
    # status. 80 * 3s = 240s (~4 min).
    terminal = {"completed", "busy", "failed", "no-answer", "canceled"}
    status = "queued"
    for _ in range(80):  # ~4 min, > MAX_CALL_SECS(150s) + ring
        time.sleep(3)
        status = client.calls(sid).fetch().status
        print(f"[orch] status={status}")
        if status in terminal:
            break
    return status


def _teardown(server: "subprocess.Popen | None", tunnels: "list[subprocess.Popen]") -> None:
    # Kill every cloudflared we spawned (live, failed, or already-dead), then server.
    for proc in tunnels:
        _kill_proc(proc)
    _kill_proc(server)
    print("[orch] torn down server + all tunnels")


def main() -> None:
    keep_alive = "--keep-alive" in sys.argv[1:]

    server = None
    tunnels: "list[subprocess.Popen]" = []
    status = None

    # PREFERRED PATH: reuse a warm tunnel published by the persistent daemon.
    # This skips cold-start entirely — no server, no tunnel of our own to tear down.
    if not keep_alive:
        mode, warm_url = select_call_url()
        if mode == "warm":
            print(f"[orch] using WARM tunnel {warm_url} — placing call (no cold start)")
            try:
                status = place_interactive_call(warm_url)
                print(f"[orch] DONE (warm) — final status: {status}")
                if status in {"failed", "no-answer", "busy", "canceled", None}:
                    sys.exit(1)
                return
            except Exception as e:  # noqa: BLE001
                # Warm path raced/failed at call time → fall through to cold start.
                print(f"[orch] warm-tunnel call failed: {e} — falling back to cold start")

    try:
        server = start_server()
        tunnel, public_url = start_tunnel_with_retries(spawned=tunnels)

        if keep_alive:
            print(f"[orch] --keep-alive: server + tunnel up at {public_url}")
            print("[orch] holding open — Ctrl-C to tear down (no call placed).")
            try:
                while server.poll() is None and tunnel.poll() is None:
                    time.sleep(2)
            except KeyboardInterrupt:
                print("\n[orch] Ctrl-C — tearing down")
            return

        status = place_interactive_call(public_url)
    except Exception as e:  # noqa: BLE001
        print(f"[orch] interactive path failed: {e}")
        if keep_alive:
            # In keep-alive mode we never place a call, even on failure.
            return
        try:
            status = place_fallback_call()
        except Exception as e2:  # noqa: BLE001
            print(f"[orch] FALLBACK ALSO FAILED: {e2}")
            _teardown(server, tunnels)
            sys.exit(2)
    finally:
        _teardown(server, tunnels)

    print(f"[orch] DONE — final status: {status}")
    if status in {"failed", "no-answer", "busy", "canceled", None}:
        sys.exit(1)


if __name__ == "__main__":
    main()
