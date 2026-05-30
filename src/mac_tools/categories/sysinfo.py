"""
categories/sysinfo.py — read-only system information tools.

Every tool here just READS system state via `run_shell` with ABSOLUTE binary paths (no shell,
no caller-derived args at all — these take no input), then parses the raw command output into a
SHORT, speakable string. Nothing here writes/toggles/deletes anything, so all are Risk.SAFE and
are genuinely safe to execute in autonomous tests.

House style (matches src/mac_actions.py + the runner contract):
  * run_shell([...]) with argv[0] an absolute path; never shell=True.
  * Parse raw output into a human/spoken-friendly string (the agent speaks it verbatim).
  * audit() every action.
  * Catch subprocess.SubprocessError / parse errors; return a friendly string — never raise.

The commands (all read-only, fixed argv — none take caller input):
  battery_status     /usr/bin/pmset -g batt
  disk_space         /bin/df -H /
  memory_info        /usr/bin/vm_stat  + /usr/sbin/sysctl hw.memsize
  cpu_load           /usr/bin/uptime
  date_time          /bin/date
  system_uptime      /usr/bin/uptime (uptime portion)
  os_version         /usr/bin/sw_vers

Wi-Fi SSID and local IP are NOT here — network.py (`get_wifi_name` / `get_local_ip`) owns
those; this module used to duplicate them and the duplicate tools were removed.
"""

import re
import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_shell


def _safe(action: str, fn) -> str:
    """Run a parse function, audit the result, and translate any failure into a friendly spoken
    string so a handler NEVER raises into the voice pipeline. `fn` returns the spoken string."""
    try:
        spoken = fn()
    except (subprocess.SubprocessError, ValueError, IndexError, KeyError) as e:
        spoken = "Sorry, I couldn't read that right now."
        audit(action, {}, f"error: {e}")
        return spoken
    audit(action, {}, spoken)
    return spoken


@tool(
    name="battery_status",
    description=(
        "Report this Mac's battery charge percentage, whether it's charging or on AC power, and "
        "the estimated time remaining if available."
    ),
    risk=Risk.SAFE,
    category="sysinfo",
)
def battery_status() -> str:
    def _do() -> str:
        raw = run_shell(["/usr/bin/pmset", "-g", "batt"])
        # pmset output looks like:
        #   Now drawing from 'AC Power'
        #    -InternalBattery-0 (id=...)  87%; charging; 1:23 remaining present: true
        # Some desktop Macs / no-battery machines report no InternalBattery line.
        pct_m = re.search(r"(\d+)%", raw)
        if not pct_m:
            # No battery (e.g. a Mac mini / desktop on AC) — say so rather than guess.
            if "AC Power" in raw:
                return "This Mac is on AC power and has no battery."
            return "I couldn't find battery info on this Mac."
        pct = pct_m.group(1)
        source = "AC power" if "AC Power" in raw else "battery"
        # State word after the percentage: charging / discharging / charged / etc.
        state_m = re.search(r"%;\s*([a-zA-Z ]+?);", raw)
        state = state_m.group(1).strip() if state_m else ""
        time_m = re.search(r"(\d+:\d+)\s+remaining", raw)
        parts = [f"Battery is at {pct} percent"]
        if state:
            parts.append(state)
        parts.append(f"on {source}")
        spoken = ", ".join(parts) + "."
        if time_m and time_m.group(1) != "0:00":
            h, m = time_m.group(1).split(":")
            spoken += f" About {int(h)} hours and {int(m)} minutes remaining."
        return spoken

    return _safe("battery_status", _do)


@tool(
    name="disk_space",
    description="Report free and total disk space on this Mac's main drive.",
    risk=Risk.SAFE,
    category="sysinfo",
)
def disk_space() -> str:
    def _do() -> str:
        # df -H gives human (powers of 1000) sizes; we read the row for / (last line).
        raw = run_shell(["/bin/df", "-H", "/"])
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        # Header line then the / row. Columns: Filesystem Size Used Avail Capacity ... Mounted
        row = lines[-1].split()
        # Defensive: expected layout has at least Size, Used, Avail, Capacity in cols 1-4.
        size, used, avail, capacity = row[1], row[2], row[3], row[4]
        return (
            f"Disk: {avail} free of {size} total ({used} used, {capacity} full)."
        )

    return _safe("disk_space", _do)


@tool(
    name="memory_info",
    description="Report this Mac's total RAM and roughly how much is currently free.",
    risk=Risk.SAFE,
    category="sysinfo",
)
def memory_info() -> str:
    def _do() -> str:
        # Total physical RAM in bytes.
        total_bytes = int(run_shell(["/usr/sbin/sysctl", "-n", "hw.memsize"]))
        total_gb = total_bytes / (1024 ** 3)

        # vm_stat reports page counts; the page size is in its first line ("page size of N").
        vm = run_shell(["/usr/bin/vm_stat"])
        page_m = re.search(r"page size of (\d+) bytes", vm)
        page = int(page_m.group(1)) if page_m else 4096

        def _pages(label: str) -> int:
            m = re.search(rf"{re.escape(label)}:\s+(\d+)\.", vm)
            return int(m.group(1)) if m else 0

        # "Free" to a user ≈ truly free + speculative + inactive (reclaimable) pages.
        free_pages = _pages("Pages free") + _pages("Pages speculative") + _pages("Pages inactive")
        free_gb = (free_pages * page) / (1024 ** 3)
        return (
            f"This Mac has {total_gb:.0f} gigabytes of RAM, with about "
            f"{free_gb:.1f} gigabytes free."
        )

    return _safe("memory_info", _do)


@tool(
    name="cpu_load",
    description="Report this Mac's current CPU load averages (1, 5, and 15 minute).",
    risk=Risk.SAFE,
    category="sysinfo",
)
def cpu_load() -> str:
    def _do() -> str:
        raw = run_shell(["/usr/bin/uptime"])
        # ... load averages: 1.79 1.95 2.07
        m = re.search(r"load averages?:\s+([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)", raw)
        if not m:
            raise ValueError("could not parse load averages")
        one, five, fifteen = m.group(1), m.group(2), m.group(3)
        return (
            f"CPU load average is {one} over the last minute, "
            f"{five} over five minutes, and {fifteen} over fifteen minutes."
        )

    return _safe("cpu_load", _do)


@tool(
    name="date_time",
    description="Report the current date and time on this Mac.",
    risk=Risk.SAFE,
    category="sysinfo",
)
def date_time() -> str:
    def _do() -> str:
        # A fixed, speech-friendly format string — no caller input. E.g.
        # "Thursday, May 28, 2026 at 03:14 PM".
        raw = run_shell(["/bin/date", "+%A, %B %-d, %Y at %-I:%M %p"])
        return f"It's {raw}."

    return _safe("date_time", _do)


@tool(
    name="system_uptime",
    description="Report how long this Mac has been running since its last boot.",
    risk=Risk.SAFE,
    category="sysinfo",
)
def system_uptime() -> str:
    def _do() -> str:
        raw = run_shell(["/usr/bin/uptime"])
        # uptime: "12:34  up 3 days,  4:21, 2 users, load averages: ..."
        # Grab the segment between "up " and the user/load count.
        m = re.search(r"up\s+(.*?),\s+\d+\s+users?", raw)
        if not m:
            # Some forms read "1 user"; fall back to up-to-load-averages.
            m = re.search(r"up\s+(.*?),\s+load averages?", raw)
        if not m:
            raise ValueError("could not parse uptime")
        up = m.group(1).strip()
        return f"This Mac has been up for {up}."

    return _safe("system_uptime", _do)


@tool(
    name="os_version",
    description="Report this Mac's macOS name/version and build number.",
    risk=Risk.SAFE,
    category="sysinfo",
)
def os_version() -> str:
    def _do() -> str:
        name = run_shell(["/usr/bin/sw_vers", "-productName"])
        ver = run_shell(["/usr/bin/sw_vers", "-productVersion"])
        build = run_shell(["/usr/bin/sw_vers", "-buildVersion"])
        return f"Running {name} {ver}, build {build}."

    return _safe("os_version", _do)


# NOTE: Wi-Fi SSID (`wifi_network_name`) and local IP (`local_ip`) tools used to live here, but
# they duplicated network.py's `get_wifi_name` / `get_local_ip` — same parses, same en0/en1
# fallback — and offering both confused tool selection. They were removed; network.py is the
# single home for those (it has interface auto-discovery WITH a process-life cache).
