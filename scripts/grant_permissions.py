#!/usr/bin/env python3
"""
grant_permissions.py — macOS TCC permission helper for the voice agent.

WHY THIS EXISTS
---------------
The voice agent (src/mac_actions.py) drives the Mac via AppleScript:
`tell application "System Events" ...`, `tell application "Spotify" ...`,
`display notification ...`, etc. On macOS these are gated by **TCC**
(Transparency, Consent, and Control). When the required grants are missing,
osascript does not fail fast — it HANGS until it times out with error **-1712**
("AppleEvent timed out"), which is exactly the symptom we hit. A denied (rather
than ungranted) Automation grant returns **-1743** ("Not authorized to send
Apple events").

THE PER-BINARY TCC MODEL (the key thing to understand)
------------------------------------------------------
TCC grants are keyed to the *requesting executable*, NOT to "you the user" and
NOT to the project. macOS records, per client binary, which capabilities it may
use and which target apps it may automate. Consequences:

  * The Terminal you test from interactively (e.g. Terminal.app / iTerm) is ONE
    client. Granting it Automation/Accessibility lets *your live `python ...`
    runs from that terminal* work.
  * The launchd daemon runs a DIFFERENT client binary:
        /Users/node3/projects/voice_fun/.venv/bin/python
    (a venv symlink to python3.12). launchd does not inherit your Terminal's
    grants. So even after your terminal works, the 7 AM daemon can STILL be
    blocked until that exact interpreter path is granted.

  Therefore BOTH must be granted, in four panes:
    - Accessibility       (UI scripting / System Events keystrokes & control)
    - Automation          (per target app: System Events, Finder, Spotify, ...)
    - Screen Recording    (anything reading window contents / screenshots)
    - Full Disk Access     (reading TCC-protected file locations)

  Note: a *symlinked* interpreter can register under either the link path or its
  resolved real path depending on macOS version, so we surface BOTH paths in the
  instructions and let the owner drag whichever the file picker accepts.

WHY WE CAN ONLY PROBE, NEVER READ STATUS
----------------------------------------
The authoritative store is the TCC databases
(~/Library/Application Support/com.apple.TCC/TCC.db and the system one). Both are
SIP-protected; even root cannot read them without Full Disk Access, and we will
not try. So this tool DETECTS state empirically: it runs tiny, benign
automations under a short timeout and classifies the result
(GRANTED / BLOCKED(-1743) / TIMEOUT(-1712) / ERROR).

MODES
-----
  --check  (default-safe)  Probe current status WITHOUT opening any window or
                           popping any dialog where avoidable. Prints a summary
                           table and which client binary it ran as.
  --open / (no flag)       Open the four Privacy panes in System Settings, print
                           step-by-step "add Terminal AND the venv python"
                           instructions, then fire the benign probes so the
                           Automation consent dialogs appear for you to Allow.

SAFETY GUARANTEES
-----------------
  * NO sound, ever. No `say`, no `beep`, no `afplay`. The only notification used
    is `display notification`, which is silent.
  * No sudo. Never touches network, power, Wi-Fi, Bluetooth, or any setting.
  * Idempotent: running it repeatedly only probes / re-opens panes.
  * Pure Python standard library — no new pip dependencies.
"""

import argparse
import os
import subprocess
import sys

# The venv interpreter that the launchd daemon actually executes. This is the
# client binary that MUST be granted for the 7 AM call (and all daemon actions).
VENV_PYTHON = "/Users/node3/projects/voice_fun/.venv/bin/python"
# Its resolved real target (e.g. .../python3.12). TCC may key the grant to either
# the symlink or the real path depending on macOS version, so we show both.
try:
    VENV_PYTHON_REAL = os.path.realpath(VENV_PYTHON)
except OSError:
    VENV_PYTHON_REAL = VENV_PYTHON

# Per-osascript AppleScript-level timeout (`with timeout of N seconds`) AND the
# outer subprocess timeout. Kept short so a wedged/ungranted call returns fast
# instead of stalling. We give subprocess a little extra over the AppleScript
# timeout so the -1712 error can propagate cleanly before we kill it.
APPLE_TIMEOUT_SECONDS = 3
SUBPROCESS_TIMEOUT_SECONDS = APPLE_TIMEOUT_SECONDS + 3

# The four Privacy & Security panes the owner must populate, as deep-link URLs.
PRIVACY_PANES = [
    ("Accessibility",
     "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"),
    ("Automation",
     "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"),
    ("Screen Recording",
     "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
    ("Full Disk Access",
     "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"),
]

# Result classifications.
GRANTED = "GRANTED"
BLOCKED = "BLOCKED(-1743)"
TIMEOUT = "TIMEOUT(-1712)"
ERROR = "ERROR"

# Representative, side-effect-free automations. Each is wrapped in
# `with timeout of N seconds` so AppleScript itself bails fast on a hung
# AppleEvent. The notification one is SILENT (display notification makes no
# sound) and disappears on its own.
PROBES = [
    (
        "System Events: frontmost process",
        # UI-scripting target — the classic one that times out (-1712) when
        # Accessibility/Automation for System Events is missing.
        'with timeout of {t} seconds\n'
        '  tell application "System Events" to get name of first process '
        'whose frontmost is true\n'
        'end timeout',
    ),
    (
        "Finder: name of home",
        # A second, distinct Automation target so a System-Events-only grant
        # doesn't masquerade as "everything works".
        'with timeout of {t} seconds\n'
        '  tell application "Finder" to get name of home\n'
        'end timeout',
    ),
    (
        "display notification (silent)",
        # Silent, self-dismissing. Confirms the agent can surface notifications.
        'with timeout of {t} seconds\n'
        '  display notification "voice_fun permission probe" '
        'with title "voice_fun"\n'
        'end timeout',
    ),
]


def _classify(stderr: str) -> str:
    """Map an osascript stderr string to a TCC result classification."""
    if "-1743" in stderr:
        return BLOCKED
    if "-1712" in stderr:
        return TIMEOUT
    return ERROR


def run_probe(script: str) -> tuple[str, str]:
    """Run one AppleScript probe via osascript with both an AppleScript-level and
    a subprocess-level timeout. Returns (classification, detail).

    Never raises: a timeout/kill is classified as TIMEOUT, anything else ERROR.
    """
    body = script.format(t=APPLE_TIMEOUT_SECONDS)
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", body],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # osascript itself wedged past even the AppleScript timeout — treat as a
        # hung AppleEvent (the -1712 family) since that's what produces this.
        return TIMEOUT, "subprocess timed out (hung AppleEvent)"
    except OSError as e:
        return ERROR, str(e)

    if proc.returncode == 0:
        return GRANTED, (proc.stdout.strip() or "ok")

    stderr = (proc.stderr or "").strip()
    return _classify(stderr), stderr or f"exit {proc.returncode}"


def _client_label() -> str:
    """Describe which client binary we're running as (Terminal vs venv python).

    TCC keys grants to this executable, so the owner needs to know which one the
    current results reflect.
    """
    exe = os.path.realpath(sys.executable)
    if exe == VENV_PYTHON_REAL or sys.executable == VENV_PYTHON:
        return "venv python (the DAEMON's client binary)"
    return "interactive run (your Terminal's client binary)"


def do_check() -> int:
    """Probe each automation and print a summary table. Opens NO windows.

    Note: probes can still surface a one-time Automation consent dialog the very
    first time a given (client -> target app) pair is requested; that's macOS,
    not us opening anything. Subsequent runs are fully silent.
    """
    print("voice_fun — TCC permission probe (--check)")
    print("=" * 60)
    print(f"Running as : {_client_label()}")
    print(f"sys.executable : {sys.executable}")
    if os.path.realpath(sys.executable) != sys.executable:
        print(f"  resolves to  : {os.path.realpath(sys.executable)}")
    print(f"Daemon binary  : {VENV_PYTHON}")
    if VENV_PYTHON_REAL != VENV_PYTHON:
        print(f"  resolves to  : {VENV_PYTHON_REAL}")
    print("(TCC.db is SIP-protected and unreadable; status is inferred by probing.)")
    print("-" * 60)

    results = []
    for name, script in PROBES:
        status, detail = run_probe(script)
        results.append((name, status, detail))

    width = max(len(n) for n, _ in PROBES)
    print(f"{'PROBE'.ljust(width)}  STATUS")
    print(f"{'-' * width}  {'-' * 14}")
    for name, status, _detail in results:
        print(f"{name.ljust(width)}  {status}")
    print("-" * 60)

    # Brief interpretation so the owner knows what to do next.
    statuses = {s for _, s, _ in results}
    if statuses == {GRANTED}:
        print("All probes GRANTED for THIS client binary.")
        print("Reminder: grants are per-binary. If this was your Terminal, the")
        print("launchd DAEMON (venv python) may still be ungranted — run this")
        print("same check via the daemon's interpreter to confirm:")
        print(f"  {VENV_PYTHON} {os.path.abspath(__file__)} --check")
    else:
        if TIMEOUT in statuses:
            print("TIMEOUT(-1712) => Automation/Accessibility NOT yet granted for")
            print("this client binary (the AppleEvent hangs, then times out).")
        if BLOCKED in statuses:
            print("BLOCKED(-1743) => a grant was explicitly DENIED; re-enable it.")
        print("Run without --check (i.e. --open) to open the four Privacy panes")
        print("and trigger the consent dialogs.")
    print("=" * 60)
    # Exit 0 if everything granted, 1 otherwise — handy for scripting.
    return 0 if statuses == {GRANTED} else 1


def do_open() -> int:
    """Open the four Privacy panes, print add-both-binaries instructions, then
    fire the benign probes so Automation consent dialogs appear to Allow.
    """
    print("voice_fun — opening Privacy & Security panes (--open)")
    print("=" * 60)
    print("Opening four System Settings panes. In EACH, you must add BOTH:")
    print("  1) your Terminal app  (Terminal.app or iTerm) — for live testing")
    print(f"  2) the venv python    {VENV_PYTHON}")
    if VENV_PYTHON_REAL != VENV_PYTHON:
        print(f"     (or its real path) {VENV_PYTHON_REAL}")
    print()
    print("WHY both: TCC grants are per-executable. Your Terminal and the")
    print("launchd daemon's venv python are different client binaries, so each")
    print("must be granted independently or the 7 AM call stays blocked.")
    print("-" * 60)

    for name, url in PRIVACY_PANES:
        try:
            subprocess.run(["/usr/bin/open", url], check=False, timeout=10)
            print(f"  opened pane: {name}")
        except (OSError, subprocess.SubprocessError) as e:
            print(f"  could not open pane {name}: {e}")

    print("-" * 60)
    print("In each pane:")
    print("  * Accessibility / Screen Recording / Full Disk Access:")
    print("      click '+', press Cmd-Shift-G, paste a path below, add it.")
    print("      Add your Terminal app AND the venv python (both binaries).")
    print("  * Automation: this is per target app. Toggle ON the rows for")
    print("      System Events, Finder, and Spotify under BOTH Terminal and")
    print("      the venv python. New rows appear only after a consent prompt,")
    print("      which the probes below will trigger.")
    print()
    print("Paths to paste (Cmd-Shift-G):")
    print(f"  {VENV_PYTHON}")
    if VENV_PYTHON_REAL != VENV_PYTHON:
        print(f"  {VENV_PYTHON_REAL}")
    print("-" * 60)

    print("Firing benign probes to trigger Automation consent dialogs.")
    print("Click 'Allow' / 'OK' on any dialog that appears (the notification is")
    print("silent and self-dismissing).")
    for name, script in PROBES:
        status, detail = run_probe(script)
        print(f"  {name}: {status}" + (f" ({detail})" if status != GRANTED else ""))
    print("-" * 60)
    print("IMPORTANT: do the SAME under the daemon's interpreter so its consent")
    print("dialogs register too:")
    print(f"  {VENV_PYTHON} {os.path.abspath(__file__)} --open")
    print("Then verify with:  ... grant_permissions.py --check")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="macOS TCC permission helper for the voice_fun agent.",
        epilog="Default action (no flag) is --open. Use --check for a safe, "
               "window-free status probe.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Probe current TCC status without opening any window (safe).",
    )
    group.add_argument(
        "--open",
        dest="open_panes",
        action="store_true",
        help="Open the four Privacy panes and trigger consent dialogs (default).",
    )
    args = parser.parse_args()

    if args.check:
        return do_check()
    # No flag or --open both fall through to the opener.
    return do_open()


if __name__ == "__main__":
    sys.exit(main())
