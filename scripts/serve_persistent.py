"""
serve_persistent.py — the WARM-TUNNEL DAEMON.

Why this exists (QA-P0-1 & QA-P0-2):
  Cloudflare *quick-tunnels* are unreliable on a cold start: the URL prints in a
  second but the edge frequently takes minutes (or never) to actually route to the
  local origin. If the 7 AM orchestrator gambles on a cold tunnel, it silently
  degrades to a Polly <Say> monologue — the voice the user explicitly REJECTED.

  The fix: keep a tunnel ALREADY UP AND WARM, hours before the call. This daemon:
    1. Holds a `caffeinate -dimsu` assertion so the Mac stays awake (P0-2, no sudo).
    2. Boots uvicorn twilio_bot:app on 127.0.0.1:8090 (cwd=src so imports resolve).
    3. Opens a cloudflared quick-tunnel, parses the trycloudflare URL, waits for
       <url>/health == 200 (i.e. the edge is genuinely routing, not just printed).
    4. Atomically writes the live URL + a unix timestamp to logs/tunnel_url.txt.
    5. MONITORS forever: re-checks <url>/health on an interval; if the tunnel dies
       or stops routing, it kills it, opens a FRESH quick-tunnel, re-verifies, and
       rewrites tunnel_url.txt. The tunnel therefore has hours to stabilise and
       self-heals; the 7 AM call reuses a warm URL instead of a cold one.

  Clean teardown on SIGTERM/SIGINT: server, tunnel, AND the caffeinate assertion
  are all killed (process-group kills → no orphans).

Run (via the venv python, normally under launchd):
    .venv/bin/python scripts/serve_persistent.py

This daemon NEVER places a Twilio call. It only serves /health + /twiml + /ws so
the orchestrator can point a call at it.
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_DIR / "src"
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# INBOUND reliability: load .env so the Twilio creds (TWILIO_ACCOUNT_SID /
# TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER) are available to this daemon process —
# launchd does NOT inject them. We need them to keep the phone number's voice
# webhook pointed at the live (rotating) tunnel's /twiml (see sync_twilio_webhook).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_DIR / ".env")
except Exception:  # noqa: BLE001 — never let env loading crash the daemon
    pass

URL_FILE = LOG_DIR / "tunnel_url.txt"
DAEMON_LOG = LOG_DIR / "tunnel_daemon.log"
CF_LOG = LOG_DIR / "tunnel_cloudflared.log"  # cloudflared's own stdout for current attempt

VENV_PY = sys.executable
PORT = int(os.getenv("PERSISTENT_PORT", "8090"))  # 8090: avoid clashing with the 8080 cold path
CF_BIN = shutil.which("cloudflared") or "/opt/homebrew/bin/cloudflared"
CAFFEINATE_BIN = shutil.which("caffeinate") or "/usr/bin/caffeinate"

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# Timing knobs.
SERVER_HEALTH_WAIT_S = 30.0   # local uvicorn must come up within this
URL_WAIT_S = 20.0             # cloudflared must print a URL within this
EDGE_WAIT_S = 45.0            # the edge must route <url>/health within this (warm = generous)
MONITOR_INTERVAL_S = 15.0     # how often we poll the live tunnel's health
MONITOR_FAIL_GRACE = 3        # consecutive failed polls before we declare the tunnel dead
MAX_BACKOFF_S = 600.0         # cap on the normal exponential bring-up backoff (was 60s)
# trycloudflare throttles quick-tunnel CREATION with HTTP 429 / Cloudflare error 1015 when we
# rotate too often. Retrying on a short backoff only PROLONGS the throttle, so when we detect a
# rate-limit we cool down for this long instead of ramping the normal backoff.
RATE_LIMIT_COOLDOWN_S = float(os.getenv("CF_RATE_LIMIT_COOLDOWN_S", "600"))


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with DAEMON_LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _resolve_via_dns(host: str) -> str:
    """Resolve a host to an IPv4 address using a DIRECT DNS query (/usr/bin/host),
    bypassing the macOS stub resolver (mDNSResponder).

    Why: freshly-created *.trycloudflare.com names are published in DNS (nslookup/
    host resolve them instantly) but macOS's getaddrinfo/mDNSResponder frequently
    NEGATIVE-CACHES them for a long time, so urllib raises gaierror even though the
    name is live. Twilio's edge (which actually fetches /twiml) uses its OWN
    resolver and is unaffected — so a getaddrinfo failure here is a FALSE negative
    that would wrongly reject a perfectly good warm tunnel. We sidestep it.
    """
    try:
        out = subprocess.run(
            ["/usr/bin/host", "-t", "A", host],
            capture_output=True, text=True, timeout=6,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"has address (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def http_ok(url: str, timeout: float = 6.0) -> bool:
    """True iff GET <url> returns 200. Resilient to the macOS stub-resolver quirk:
    if the normal request fails to resolve the host, retry by connecting to a
    directly-resolved IP while preserving SNI + Host header."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.URLError as e:
        # Only fall back on a resolution failure; real 5xx/timeouts mean "not routing".
        if not isinstance(getattr(e, "reason", None), socket.gaierror):
            return False
    except Exception:
        return False

    # Resolver fallback: connect by IP, keep Host header + TLS SNI = original host.
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    ip = _resolve_via_dns(host)
    if not ip:
        return False
    port = parts.port or 443
    tls = None
    try:
        ctx = ssl.create_default_context()
        # Connect to the resolved IP but present SNI = real hostname so the cert
        # validates and Cloudflare routes to the right tunnel.
        raw = socket.create_connection((ip, port), timeout=timeout)
        tls = ctx.wrap_socket(raw, server_hostname=host)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        conn.sock = tls  # reuse our IP-targeted, correctly-SNI'd socket
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


def wait_for(predicate, deadline_s: float, interval: float = 1.0) -> bool:
    end = time.time() + deadline_s
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def kill_proc(proc: "subprocess.Popen | None", name: str = "proc") -> None:
    """Terminate a process AND its session group so helpers don't orphan."""
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
        log(f"[daemon] {name} didn't exit on SIGTERM — SIGKILL")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pass


def atomic_write_url(url: str) -> None:
    """Write the live URL + unix ts atomically (write temp, fsync, rename) so a
    reader (run_morning_call) never sees a half-written file."""
    tmp = URL_FILE.with_suffix(".txt.tmp")
    payload = f"{url}\n{int(time.time())}\n"
    with tmp.open("w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, URL_FILE)
    log(f"[daemon] wrote warm URL -> {URL_FILE}: {url}")


def sync_twilio_webhook(url: str) -> None:
    """INBOUND reliability fix: point the Twilio phone number's VOICE webhook at the
    live tunnel's /twiml so an incoming call always reaches the current (rotating)
    quick-tunnel.

    Twilio's incoming-call webhook is a SAVED setting on the number, but our public
    URL is a cloudflared quick-tunnel that rotates whenever it dies. So every time we
    publish a fresh URL (first bring-up AND every rotation) we update the number's
    voice_url to <url>/twiml. Best-effort: any failure is logged but NEVER crashes the
    daemon — the warm tunnel + tunnel_url.txt path keeps working for outbound either way.
    """
    try:
        from twilio.rest import Client
    except Exception as e:  # noqa: BLE001
        log(f"[daemon] twilio sdk import failed — skipping webhook sync: {e}")
        return
    try:
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        phone_number = os.environ["TWILIO_PHONE_NUMBER"]
    except KeyError as e:
        log(f"[daemon] missing Twilio env {e} — skipping inbound webhook sync")
        return
    try:
        client = Client(account_sid, auth_token)
        matches = client.incoming_phone_numbers.list(phone_number=phone_number)
        if not matches:
            log(f"[daemon] no Twilio number matched {phone_number} — cannot sync webhook")
            return
        rec = matches[0]
        client.incoming_phone_numbers(rec.sid).update(
            voice_url=f"{url}/twiml", voice_method="POST"
        )
        log(f"[daemon] Twilio voice webhook synced -> {url}/twiml (inbound ready)")
    except Exception as e:  # noqa: BLE001 — webhook sync must never take the daemon down
        log(f"[daemon] Twilio webhook sync FAILED (inbound may be stale): {e}")


def start_caffeinate() -> subprocess.Popen:
    """Hold a power assertion so the Mac doesn't idle-sleep before/at 7 AM.
    -d display, -i idle-system, -m disk, -s while-on-AC, -u user-active.
    No sudo required."""
    log("[daemon] starting caffeinate -dimsu (prevent idle sleep)")
    return subprocess.Popen(
        [CAFFEINATE_BIN, "-dimsu"],
        start_new_session=True,
    )


def start_server() -> subprocess.Popen:
    log(f"[daemon] starting uvicorn twilio_bot:app on 127.0.0.1:{PORT}")
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "twilio_bot:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(SRC_DIR),
        start_new_session=True,
    )
    if not wait_for(lambda: http_ok(f"http://127.0.0.1:{PORT}/health"), SERVER_HEALTH_WAIT_S):
        kill_proc(proc, "server")
        raise RuntimeError(f"uvicorn did not become healthy on :{PORT}")
    log("[daemon] uvicorn healthy locally")
    return proc


def cloudflared_rate_limited() -> bool:
    """True iff the most recent cloudflared attempt failed because trycloudflare is
    RATE-LIMITING quick-tunnel creation (HTTP 429 / Cloudflare error 1015). In that state no
    URL is ever printed, so open_tunnel() raises 'printed no URL'; we read cloudflared's own
    log to tell a rate-limit apart from a generic failure and back off much harder."""
    try:
        txt = CF_LOG.read_text()
    except OSError:
        return False
    return "429 Too Many Requests" in txt or "error code: 1015" in txt


def open_tunnel() -> "tuple[subprocess.Popen, str]":
    """Open ONE fresh cloudflared quick-tunnel that actually routes /health.

    Returns (proc, public_url). Raises RuntimeError if it never routes in time.
    Caller is responsible for killing proc.
    """
    CF_LOG.write_text("")
    cf_out = CF_LOG.open("w")
    proc = subprocess.Popen(
        [CF_BIN, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=cf_out,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    public_url = ""

    def _got_url() -> bool:
        nonlocal public_url
        if proc.poll() is not None:  # died early
            return False
        m = URL_RE.search(CF_LOG.read_text())
        if m:
            public_url = m.group(0)
            return True
        return False

    if not wait_for(_got_url, URL_WAIT_S):
        kill_proc(proc, "cloudflared")
        raise RuntimeError(f"cloudflared printed no URL within {URL_WAIT_S:.0f}s")

    log(f"[daemon] tunnel URL = {public_url} — waiting up to {EDGE_WAIT_S:.0f}s for edge to route")
    if not wait_for(lambda: http_ok(f"{public_url}/health"), EDGE_WAIT_S, interval=2.0):
        kill_proc(proc, "cloudflared")
        raise RuntimeError(f"{public_url}/health never reached 200 in {EDGE_WAIT_S:.0f}s")

    log(f"[daemon] tunnel LIVE and routing -> {public_url}")
    return proc, public_url


class Daemon:
    def __init__(self):
        self.caffeinate: "subprocess.Popen | None" = None
        self.server: "subprocess.Popen | None" = None
        self.tunnel: "subprocess.Popen | None" = None
        self._stop = threading.Event()

    def handle_signal(self, signum, _frame):
        log(f"[daemon] received signal {signum} — shutting down")
        self._stop.set()

    def establish_tunnel(self) -> str:
        """(Re)open a fresh tunnel until one routes, then publish its URL.
        Retries with backoff; respects the stop flag."""
        backoff = 5.0
        while not self._stop.is_set():
            # Make sure the local server is still up before tunnelling.
            if self.server is None or self.server.poll() is not None:
                log("[daemon] server is down — (re)starting it")
                kill_proc(self.server, "server")
                self.server = start_server()
            try:
                self.tunnel, url = open_tunnel()
                atomic_write_url(url)
                # INBOUND: keep Twilio's voice webhook pointed at this live tunnel's
                # /twiml. Runs on first bring-up and on every rotation (this method is
                # the single publish path), so inbound calls always hit the current URL.
                sync_twilio_webhook(url)
                return url
            except RuntimeError as e:
                kill_proc(self.tunnel, "cloudflared")
                self.tunnel = None
                if cloudflared_rate_limited():
                    # Don't prolong the throttle: cool down for a long fixed interval and
                    # reset the ramp, instead of poking trycloudflare again in ~60s.
                    wait = RATE_LIMIT_COOLDOWN_S
                    backoff = 5.0
                    log(
                        f"[daemon] tunnel bring-up failed: {e} — trycloudflare is RATE-LIMITING "
                        f"quick tunnels (429/1015); cooling down {wait:.0f}s before retry"
                    )
                else:
                    wait = backoff
                    log(f"[daemon] tunnel bring-up failed: {e} — retrying in {wait:.0f}s")
                    backoff = min(backoff * 1.5, MAX_BACKOFF_S)
                self._stop.wait(wait)
        return ""

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        log("=" * 60)
        log(f"[daemon] starting warm-tunnel daemon (pid {os.getpid()}, port {PORT})")
        try:
            self.caffeinate = start_caffeinate()
            self.server = start_server()
            url = self.establish_tunnel()
            if self._stop.is_set():
                return

            # MONITOR loop: poll the live tunnel; self-heal on failure.
            fails = 0
            while not self._stop.is_set():
                self._stop.wait(MONITOR_INTERVAL_S)
                if self._stop.is_set():
                    break

                # Server crashed? Rebuild everything.
                if self.server is None or self.server.poll() is not None:
                    log("[daemon] uvicorn exited — rebuilding server + tunnel")
                    kill_proc(self.tunnel, "cloudflared")
                    self.tunnel = None
                    url = self.establish_tunnel()
                    fails = 0
                    continue

                # Tunnel process died?
                if self.tunnel is None or self.tunnel.poll() is not None:
                    log("[daemon] cloudflared process exited — reopening tunnel")
                    kill_proc(self.tunnel, "cloudflared")
                    self.tunnel = None
                    url = self.establish_tunnel()
                    fails = 0
                    continue

                # Tunnel alive but is the edge still routing?
                if http_ok(f"{url}/health"):
                    if fails:
                        log("[daemon] tunnel health recovered")
                    fails = 0
                else:
                    fails += 1
                    log(f"[daemon] tunnel health check FAILED ({fails}/{MONITOR_FAIL_GRACE}) for {url}")
                    if fails >= MONITOR_FAIL_GRACE:
                        log("[daemon] tunnel declared dead — rotating to a fresh tunnel")
                        kill_proc(self.tunnel, "cloudflared")
                        self.tunnel = None
                        url = self.establish_tunnel()
                        fails = 0
        finally:
            self.teardown()

    def teardown(self) -> None:
        log("[daemon] tearing down (tunnel, server, caffeinate)")
        kill_proc(self.tunnel, "cloudflared")
        kill_proc(self.server, "server")
        kill_proc(self.caffeinate, "caffeinate")
        log("[daemon] teardown complete — no orphans")


if __name__ == "__main__":
    Daemon().run()
