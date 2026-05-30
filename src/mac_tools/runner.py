"""
runner.py — the ONE shell-out path for every Mac tool (no `shell=True`, ever).

This mirrors `src/mac_actions.py` exactly in spirit and style:

  * `run_osa` runs /usr/bin/osascript with each statement as its own `-e` arg. Any value
    that originates from the caller/LLM is passed as TRAILING argv (read in AppleScript via
    `on run argv`) — NEVER string-interpolated into a script line. That kills the AppleScript
    /shell injection surface (verified: passing "x y; z" comes back as the literal string).
  * `run_shell` is a list-arg subprocess. argv[0] must be an absolute path or be resolvable
    via shutil.which — there is no shell, so no glob/quote/injection surface.
  * `audit` appends one line to logs/actions.log in the SAME format mac_actions uses.
  * `clamp` bounds an int (default 0-100).
  * `app_is_running` / `frontmost_app` are read-only helpers that NEVER launch an app.

Like mac_actions, the runners are deliberately thin: they shell out and either return
stripped stdout or RAISE (subprocess.SubprocessError / TimeoutExpired). Category handlers
are responsible for catching those and returning a friendly spoken string — handlers must
NEVER raise into the voice pipeline.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

# logs/actions.log lives next to the project's other logs (../../logs from this file:
# src/mac_tools/runner.py -> parents[2] is the project root). Same target mac_actions uses.
_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "actions.log"

# A tight default timeout so a wedged osascript can never stall the voice pipeline. Matches
# mac_actions' _OSA_TIMEOUT. Individual callers may pass a longer timeout for a known-slow op.
OSA_TIMEOUT = 5.0


def audit(action: str, args, result: str) -> None:
    """Append one audit line to logs/actions.log. Best-effort: logging must never break an
    action, so all OSErrors are swallowed. Format matches mac_actions._audit exactly."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a") as f:
            f.write(f"{ts}\taction={action}\targs={args!r}\tresult={result!r}\n")
    except OSError:
        pass


def clamp(n, lo: int = 0, hi: int = 100) -> int:
    """Bound `n` to [lo, hi] as an int. Non-numeric input collapses to `lo` (default-deny)."""
    try:
        return max(lo, min(hi, int(n)))
    except (TypeError, ValueError):
        return lo


# App names: letters/numbers/spaces and the . and - that appear in real app names
# ("VS Code", "Google Chrome", "Brave Browser", "Microsoft Word"). No slashes -> no paths, so a
# caller can never point an app verb at an arbitrary executable on disk — only an installed app
# by name. (The validated name is STILL passed as argv by callers — defense in depth.) Shared
# here so apps.py and windows.py don't each maintain a byte-identical copy.
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9 .\-]+$")
_MAX_APP_NAME = 80


def valid_app_name(name, max_len: int = _MAX_APP_NAME) -> str | None:
    """Return a cleaned app name if it's a plausible installed-app name, else None.
    Default-deny: anything with a slash or odd characters is rejected (no paths/executables)."""
    n = str(name or "").strip()
    if not n or len(n) > max_len or not _APP_NAME_RE.match(n):
        return None
    return n


def clean_text(value, limit: int) -> str:
    """Stringify, strip, and length-bound a caller value. Returns "" for None/blank. Shared so
    productivity.py and messaging.py don't each maintain a byte-identical copy."""
    return str(value or "").strip()[:limit]


def run_osa(*lines: str, args: list | None = None, timeout: float = OSA_TIMEOUT) -> str:
    """Run osascript with each statement as its own `-e` argument (no shell, no interpolation
    of caller text into a script line).

    `args` are DYNAMIC USER values handed to the script as trailing argv. In AppleScript you
    read them with an `on run argv ... end run` handler, e.g.:

        run_osa('on run argv', 'return item 1 of argv', 'end run', args=["x y; z"])
        # -> "x y; z" (literal — proven safe against shell/AppleScript injection)

    Every arg is stringified before being passed to subprocess (argv must be strings). Returns
    stripped stdout; raises subprocess.SubprocessError / TimeoutExpired on failure (callers
    catch and translate to a friendly spoken string)."""
    cmd = ["/usr/bin/osascript"]
    for line in lines:
        cmd += ["-e", line]
    # Trailing argv: anything after the script becomes `argv` inside `on run argv`. These are
    # passed as a list to subprocess — there is NO shell, so they are never re-parsed.
    if args:
        cmd += [str(a) for a in args]
    out = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True
    )
    return out.stdout.strip()


def run_shell(argv: list[str], timeout: float = 10.0, input_text: str | None = None) -> str:
    """Run a subprocess from a list of args (NEVER shell=True). argv[0] must be an absolute
    path or resolvable on PATH via shutil.which; we resolve it so a bare name like "pmset"
    becomes "/usr/bin/pmset" and a missing binary fails fast rather than hitting a shell.

    `input_text`, if given, is fed to stdin. Returns stripped stdout; raises
    subprocess.SubprocessError / TimeoutExpired on failure."""
    if not argv:
        raise subprocess.SubprocessError("run_shell: empty argv")
    exe = argv[0]
    # Resolve to an absolute path. If it's already absolute we keep it; otherwise we look it
    # up on PATH. An unresolvable binary raises so we never silently fall through to a shell.
    if not exe.startswith("/"):
        resolved = shutil.which(exe)
        if resolved is None:
            raise subprocess.SubprocessError(f"run_shell: cannot resolve {exe!r} on PATH")
        exe = resolved
    cmd = [exe, *[str(a) for a in argv[1:]]]
    out = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
        input=input_text,
    )
    return out.stdout.strip()


def app_is_running(name: str) -> bool:
    """True iff the named app is ALREADY running. NEVER launches the app.

    The app name comes from the caller, so it is passed as trailing argv (not interpolated).
    `application (item 1 of argv) is running` is True only for an already-open app — querying
    `is running` does not launch it. Any failure -> False (safe default)."""
    try:
        res = run_osa(
            "on run argv",
            "return (application (item 1 of argv) is running)",
            "end run",
            args=[name],
        )
        return res.strip().lower() == "true"
    except (subprocess.SubprocessError, ValueError):
        return False


def frontmost_app() -> str | None:
    """Name of the frontmost (active) application, or None if it can't be determined. Read
    only — launches nothing."""
    try:
        name = run_osa(
            'tell application "System Events" to '
            "get name of first application process whose frontmost is true"
        )
        return name or None
    except (subprocess.SubprocessError, ValueError):
        return None
