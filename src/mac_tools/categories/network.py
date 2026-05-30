"""
network.py — category="network": local IP, Wi-Fi name, ping, and Wi-Fi/Bluetooth power.

House style (matches src/mac_actions.py and the other category modules exactly): every
handler validates its input, shells out ONLY via runner.run_shell / run_osa (no shell=True,
and caller/LLM values reach a subprocess solely as list args or AppleScript trailing argv —
never string-interpolated into a shell line), audits each action, NEVER raises into the voice
pipeline, and returns a SHORT spoken-friendly string.

Risk policy (docs/tooling/CONTRACT.md): reads are SAFE; NETWORK TOGGLES are CONFIRM. So
get_local_ip / get_wifi_name / ping_host are Risk.SAFE, while set_wifi_power and
set_bluetooth_power are Risk.CONFIRM (turning Wi-Fi off could drop connectivity / the live
call). The CONFIRM tools' real toggle runs ONLY after the owner confirms via the broker.

ping_host validates the host to a strict hostname/IP charset BEFORE it ever reaches ping, and
passes it as a list arg — no shell, no injection.
"""

import re
import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa, run_shell

# Hostname / IP charset: letters, digits, dots, hyphens, and colons (for IPv6). No spaces,
# slashes, semicolons, or shell metacharacters. We run with no shell anyway, but validating
# here is defense in depth and lets us reject nonsense fast with a friendly message.
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:]+$")
_MAX_HOST = 255

# Absolute paths per the runner contract (run_shell resolves bare names too, but the contract's
# examples use absolute paths for these system binaries).
_PING = "/sbin/ping"
_NETWORKSETUP = "/usr/sbin/networksetup"

# The Wi-Fi interface device name. The contract's example uses en0, but the actual Wi-Fi
# device varies by machine (e.g. en1 when en0 is wired Ethernet), so we DETECT it at runtime
# from `networksetup -listallhardwareports` and fall back to en0 if detection fails.
_WIFI_IFACE_FALLBACK = "en0"
# Cache the resolved device so we don't re-shell on every call (the hardware port list is
# stable for the life of the process).
_wifi_iface_cache: str | None = None


def _wifi_iface() -> str:
    """Resolve the Wi-Fi interface device name (e.g. 'en0' or 'en1'). Reads the hardware-port
    list once and caches it; falls back to en0 (the contract's example) if detection fails."""
    global _wifi_iface_cache
    if _wifi_iface_cache:
        return _wifi_iface_cache
    try:
        out = run_shell([_NETWORKSETUP, "-listallhardwareports"])
        # Output is blocks of "Hardware Port: X / Device: enN / Ethernet Address: ...". Find the
        # Device line that follows the "Hardware Port: Wi-Fi" line.
        lines = out.splitlines()
        for i, line in enumerate(lines):
            if "Hardware Port:" in line and "Wi-Fi" in line:
                for follow in lines[i + 1 : i + 3]:
                    if "Device:" in follow:
                        dev = follow.split("Device:", 1)[1].strip()
                        if dev:
                            _wifi_iface_cache = dev
                            return dev
    except subprocess.SubprocessError:
        pass
    _wifi_iface_cache = _WIFI_IFACE_FALLBACK
    return _wifi_iface_cache


def _valid_host(host) -> str | None:
    """Return a cleaned host if it's a plausible hostname/IP, else None. Default-deny."""
    h = str(host or "").strip()
    if not h or len(h) > _MAX_HOST or not _HOST_RE.match(h):
        return None
    return h


# --- READS (SAFE) ------------------------------------------------------------


@tool(
    "get_local_ip",
    "Say this Mac's local network IP address (the one other devices on your network use).",
    risk=Risk.SAFE,
    category="network",
)
def get_local_ip() -> str:
    """Report the primary local IPv4 address. Read-only; never raises. Uses `ipconfig getifaddr`
    on the Wi-Fi interface, falling back to the wired interface if Wi-Fi has no address."""
    try:
        ip = ""
        # Try the detected Wi-Fi device first, then the common wired interfaces. A no-address
        # interface returns nonzero, which run_shell raises on — so we try each in turn.
        candidates = [_wifi_iface(), "en0", "en1"]
        seen = set()
        for iface in candidates:
            if iface in seen:
                continue
            seen.add(iface)
            try:
                ip = run_shell(["/usr/sbin/ipconfig", "getifaddr", iface])
                if ip:
                    break
            except subprocess.SubprocessError:
                continue
        if not ip:
            msg = "I couldn't find a local IP address right now."
            audit("get_local_ip", {}, msg)
            return msg
        msg = f"Your local IP address is {ip}."
        audit("get_local_ip", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't get the local IP address."
        audit("get_local_ip", {}, f"error: {e}")
        return msg


@tool(
    "get_wifi_name",
    "Say the name (SSID) of the Wi-Fi network this Mac is connected to.",
    risk=Risk.SAFE,
    category="network",
)
def get_wifi_name() -> str:
    """Report the current Wi-Fi SSID. Read-only; never raises. Uses networksetup's
    -getairportnetwork, which prints 'Current Wi-Fi Network: <ssid>' or a 'not associated'
    style line when off/disconnected."""
    try:
        out = run_shell([_NETWORKSETUP, "-getairportnetwork", _wifi_iface()])
        # Typical success: "Current Wi-Fi Network: MyNetwork". Otherwise it's a "You are not
        # associated..." / "Wi-Fi power is off" style message.
        marker = "Current Wi-Fi Network: "
        if marker in out:
            ssid = out.split(marker, 1)[1].strip()
            msg = f"You're connected to {ssid}." if ssid else "I couldn't read the Wi-Fi name."
        else:
            msg = "You don't seem to be connected to a Wi-Fi network."
        audit("get_wifi_name", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't check the Wi-Fi network."
        audit("get_wifi_name", {}, f"error: {e}")
        return msg


@tool(
    "ping_host",
    "Ping a host (by name or IP) once to check if it's reachable, e.g. 'google.com' or '8.8.8.8'.",
    properties={
        "host": {"type": "string", "description": "The hostname or IP address to ping."}
    },
    required=["host"],
    risk=Risk.SAFE,
    category="network",
)
def ping_host(host: str = "") -> str:
    """Ping `host` once with a 2-second timeout. The host is validated to a strict charset and
    passed as a list arg (no shell). Read-only; never raises."""
    h = _valid_host(host)
    if h is None:
        msg = "That doesn't look like a valid host to ping."
        audit("ping_host", {"host": host}, msg)
        return msg
    try:
        # -c1 = one packet, -t2 = 2-second overall timeout. Host is a list arg, never a shell
        # token. ping returns nonzero (raises) when unreachable -> we report that as "couldn't
        # reach" rather than an error.
        run_shell([_PING, "-c1", "-t2", h], timeout=4.0)
        msg = f"{h} is reachable."
        audit("ping_host", {"host": h}, msg)
        return msg
    except subprocess.TimeoutExpired:
        msg = f"{h} didn't respond in time."
        audit("ping_host", {"host": h}, msg)
        return msg
    except subprocess.SubprocessError:
        # Nonzero exit = host unreachable / unknown. That's a normal ping result, not a fault.
        msg = f"I couldn't reach {h}."
        audit("ping_host", {"host": h}, msg)
        return msg


# --- TOGGLES (CONFIRM) -------------------------------------------------------


@tool(
    "set_wifi_power",
    "Turn this Mac's Wi-Fi on or off. Turning it OFF could drop your connection, so it's "
    "confirmed first.",
    properties={
        "on": {"type": "boolean", "description": "true to turn Wi-Fi on, false to turn it off."}
    },
    required=["on"],
    risk=Risk.CONFIRM,
    category="network",
    confirm_summary=lambda on=False: f"Turn Wi-Fi {'on' if on else 'off'}?",
)
def set_wifi_power(on: bool = False) -> str:
    """Toggle Wi-Fi power (runs ONLY after the owner confirms via the broker). The on/off state
    is our own fixed-enum string ("on"/"off"), not caller text, so there's nothing to inject.
    Never raises."""
    state = "on" if bool(on) else "off"
    try:
        # networksetup -setairportpower <wifi-dev> on|off. State is a controlled literal; the
        # device is the detected Wi-Fi interface (en0 fallback per the contract example).
        run_shell([_NETWORKSETUP, "-setairportpower", _wifi_iface(), state])
        msg = f"Wi-Fi is now {state}."
        audit("set_wifi_power", {"on": bool(on)}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the Wi-Fi setting."
        audit("set_wifi_power", {"on": bool(on)}, f"error: {e}")
        return msg


@tool(
    "set_bluetooth_power",
    "Turn this Mac's Bluetooth on or off. This is confirmed first.",
    properties={
        "on": {"type": "boolean", "description": "true to turn Bluetooth on, false to turn it off."}
    },
    required=["on"],
    risk=Risk.CONFIRM,
    category="network",
    confirm_summary=lambda on=False: f"Turn Bluetooth {'on' if on else 'off'}?",
)
def set_bluetooth_power(on: bool = False) -> str:
    """Toggle Bluetooth power (runs ONLY after the owner confirms via the broker). macOS has no
    stable built-in *non-GUI* CLI for this and the contract forbids new binary deps (e.g.
    blueutil), so we drive the Control Center Bluetooth switch via System Events GUI scripting.
    The desired state is encoded as the argv "1"/"0" literal we control (not free caller text),
    so there's no injection surface. GUI scripting needs Accessibility permission; if it's not
    granted the osascript errors/times out and we return a friendly string. Never raises."""
    want_on = bool(on)
    try:
        # Open the Bluetooth menu-bar control, then click its switch only if the current state
        # differs from what's wanted. UI element paths vary slightly across macOS versions, so
        # this is best-effort and fully wrapped — any failure becomes a friendly spoken string.
        run_osa(
            "on run argv",
            'set wantOn to (item 1 of argv is "1")',
            'tell application "System Events"',
            'tell process "ControlCenter"',
            # The Bluetooth item in the menu bar (Control Center extra). Clicking opens its pane.
            'set btMenu to (first menu bar item of menu bar 1 whose description contains "Bluetooth")',
            "click btMenu",
            "delay 0.3",
            # The toggle switch inside the opened Bluetooth pane. value 1 = on, 0 = off.
            "set sw to (first checkbox of window 1)",
            "if ((value of sw as integer) is 1) is not wantOn then click sw",
            "key code 53",  # Escape to dismiss the Control Center pane
            "end tell",
            "end tell",
            "end run",
            args=["1" if want_on else "0"],
        )
        msg = f"Bluetooth is now {'on' if want_on else 'off'}."
        audit("set_bluetooth_power", {"on": want_on}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the Bluetooth setting."
        audit("set_bluetooth_power", {"on": want_on}, f"error: {e}")
        return msg
