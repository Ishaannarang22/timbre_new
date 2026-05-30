"""
categories/web.py — registry tools for the web browser.

Two kinds of tool:
  * OPEN/SEARCH (open_url, web_search, open_in_browser, new_browser_tab): launch a URL. We
    REFUSE any scheme that isn't http/https (so 'javascript:' and 'file:///...' can never be
    opened — that closes a real local-file/script-exec exfiltration hole). Opening uses the
    macOS `open` command via run_shell (list args, absolute path — no shell, no injection).
  * READ (browser_current_url, list_open_tabs): query an ALREADY-running browser's front tab /
    open tabs via AppleScript. We support Safari, Google Chrome, and Brave Browser (Chrome &
    Brave share the same AppleScript dictionary). The browser name is fixed-enum (validated
    against a small map); URLs/titles come back FROM the browser, not from the caller.

House style (matches src/mac_actions.py + the runner contract):
  * Caller URLs validated (scheme allowlist) BEFORE anything happens; default-deny with a
    friendly spoken string.
  * Any caller value reaching AppleScript goes via argv (`on run argv`), never interpolated.
  * `open` gets the URL as a list arg (no shell). We never auto-launch a browser for a READ
    verb — if it isn't running we say so.
  * audit() every action; catch expected exceptions; return a friendly string — never raise.

All Risk.SAFE: opening a URL / reading the current tab is non-destructive and recoverable.
"""

import subprocess
import urllib.parse

from ..policy import Risk
from ..registry import tool
from ..runner import audit, app_is_running, run_osa, run_shell

# Browsers we support for the READ tools, mapped from a friendly/loose name to the EXACT macOS
# application name AppleScript needs. Chrome & Brave share Chrome's AppleScript dictionary
# (URL/title of active tab of front window), so they're handled the same way. Safari uses its
# own ("URL of front document"). This is a fixed allowlist — caller browser names are matched
# against it, never passed raw into a `tell application` line.
_CHROMIUM = "chromium"
_SAFARI = "safari"
_BROWSERS = {
    "safari": ("Safari", _SAFARI),
    "chrome": ("Google Chrome", _CHROMIUM),
    "google chrome": ("Google Chrome", _CHROMIUM),
    "brave": ("Brave Browser", _CHROMIUM),
    "brave browser": ("Brave Browser", _CHROMIUM),
    "firefox": ("Firefox", None),  # Firefox has no usable AppleScript tab dictionary.
}

# Cap how many tabs we read back so a 40-tab window doesn't produce an unspeakable wall of text.
_MAX_TABS = 10


def _resolve_browser(browser: str):
    """Map a loose caller browser name to (app_name, dialect). Returns (None, None) on an
    unknown/unsupported browser so callers can default-deny with a friendly string."""
    key = str(browser or "").strip().lower()
    return _BROWSERS.get(key, (None, None))


def _is_web_url(url: str) -> bool:
    """True iff `url` is an http/https URL. Everything else (javascript:, file:, data:, ftp:,
    bare text, empty) is REFUSED — this is the security gate for the open/search tools."""
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _open_url_now(url: str) -> bool:
    """`open <url>` via run_shell (list args, no shell). Returns True on success, False on
    failure. Caller must have already validated the scheme with _is_web_url."""
    try:
        # /usr/bin/open hands the URL to the default handler. URL is a single list arg, so even
        # a URL with shell metacharacters in the query string can't break out — there's no shell.
        run_shell(["/usr/bin/open", str(url).strip()])
        return True
    except subprocess.SubprocessError:
        return False


def _open_url_in(url: str, app_name: str) -> bool:
    """`open -a <App> <url>` — open the URL in a SPECIFIC browser. Returns True/False."""
    try:
        run_shell(["/usr/bin/open", "-a", app_name, str(url).strip()])
        return True
    except subprocess.SubprocessError:
        return False


@tool(
    name="open_url",
    description=(
        "Open a web page in the default browser. Pass a full http or https URL (e.g. "
        "'https://example.com'). Only web links are allowed."
    ),
    properties={"url": {"type": "string", "description": "Full http(s) URL to open."}},
    required=["url"],
    risk=Risk.SAFE,
    category="web",
)
def open_url(url: str = "") -> str:
    """Open an http/https URL in the default browser. REFUSES any other scheme (javascript:,
    file:, data:, etc.) — that refusal is the security gate, default-deny."""
    u = str(url).strip()
    if not _is_web_url(u):
        msg = "I can only open web links that start with http or https."
        audit("open_url", {"url": u}, f"refused: {msg}")
        return msg
    if _open_url_now(u):
        msg = "Opening that link."
        audit("open_url", {"url": u}, msg)
        return msg
    msg = "Sorry, I couldn't open that link."
    audit("open_url", {"url": u}, msg)
    return msg


@tool(
    name="web_search",
    description=(
        "Search the web for something and open the results in the browser. Pass the search "
        "terms as plain text, e.g. 'best pizza near me' or 'pipecat docs'."
    ),
    properties={"query": {"type": "string", "description": "What to search for."}},
    required=["query"],
    risk=Risk.SAFE,
    category="web",
)
def web_search(query: str = "") -> str:
    """Open a Google results page for `query`. The query is URL-encoded, so it can't break the
    URL — and the resulting URL is always https (passes the open_url gate)."""
    q = str(query).strip()
    if not q:
        msg = "Tell me what you'd like to search for."
        audit("web_search", {"query": q}, msg)
        return msg
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
    if _open_url_now(url):
        msg = f"Searching the web for {q}."
        audit("web_search", {"query": q, "url": url}, msg)
        return msg
    msg = "Sorry, I couldn't run that search."
    audit("web_search", {"query": q, "url": url}, msg)
    return msg


@tool(
    name="open_in_browser",
    description=(
        "Open a web page in a SPECIFIC browser (Safari, Google Chrome, Brave, or Firefox). Pass "
        "a full http or https URL and the browser name. Only web links are allowed."
    ),
    properties={
        "url": {"type": "string", "description": "Full http(s) URL to open."},
        "browser": {
            "type": "string",
            "enum": ["Safari", "Google Chrome", "Brave Browser", "Firefox"],
            "description": "Which browser to open it in.",
        },
    },
    required=["url"],
    risk=Risk.SAFE,
    category="web",
)
def open_in_browser(url: str = "", browser: str = "Safari") -> str:
    """Open an http/https URL in a named browser. Refuses non-web schemes and unknown browsers."""
    u = str(url).strip()
    if not _is_web_url(u):
        msg = "I can only open web links that start with http or https."
        audit("open_in_browser", {"url": u, "browser": browser}, f"refused: {msg}")
        return msg
    app_name, _dialect = _resolve_browser(browser)
    if app_name is None:
        msg = "I can open links in Safari, Chrome, Brave, or Firefox."
        audit("open_in_browser", {"url": u, "browser": browser}, f"refused: {msg}")
        return msg
    if _open_url_in(u, app_name):
        msg = f"Opening that link in {app_name}."
        audit("open_in_browser", {"url": u, "browser": app_name}, msg)
        return msg
    msg = f"Sorry, I couldn't open that in {app_name}."
    audit("open_in_browser", {"url": u, "browser": app_name}, msg)
    return msg


@tool(
    name="new_browser_tab",
    description=(
        "Open a web page in a new browser tab in a specific browser (Safari, Google Chrome, "
        "Brave, or Firefox). Pass a full http or https URL. Only web links are allowed."
    ),
    properties={
        "url": {"type": "string", "description": "Full http(s) URL to open in a new tab."},
        "browser": {
            "type": "string",
            "enum": ["Safari", "Google Chrome", "Brave Browser", "Firefox"],
            "description": "Which browser to open the new tab in.",
        },
    },
    required=["url"],
    risk=Risk.SAFE,
    category="web",
)
def new_browser_tab(url: str = "", browser: str = "Safari") -> str:
    """Open an http/https URL in a new tab of a named browser. Refuses non-web schemes/unknown
    browsers. macOS `open -a` reuses the running browser and opens the link as a new tab."""
    u = str(url).strip()
    if not _is_web_url(u):
        msg = "I can only open web links that start with http or https."
        audit("new_browser_tab", {"url": u, "browser": browser}, f"refused: {msg}")
        return msg
    app_name, _dialect = _resolve_browser(browser)
    if app_name is None:
        msg = "I can open tabs in Safari, Chrome, Brave, or Firefox."
        audit("new_browser_tab", {"url": u, "browser": browser}, f"refused: {msg}")
        return msg
    if _open_url_in(u, app_name):
        msg = f"Opened a new tab in {app_name}."
        audit("new_browser_tab", {"url": u, "browser": app_name}, msg)
        return msg
    msg = f"Sorry, I couldn't open a new tab in {app_name}."
    audit("new_browser_tab", {"url": u, "browser": app_name}, msg)
    return msg


def _front_url_script(dialect: str):
    """Return the AppleScript lines (as a tuple) that read the front tab's URL for a dialect.
    The app name is supplied as argv (item 1) so it is never interpolated into the script."""
    if dialect == _SAFARI:
        return (
            "on run argv",
            'tell application (item 1 of argv) to return URL of front document',
            "end run",
        )
    # Chromium dialect (Chrome / Brave): URL of active tab of front window.
    return (
        "on run argv",
        "tell application (item 1 of argv) to return URL of active tab of front window",
        "end run",
    )


@tool(
    name="browser_current_url",
    description=(
        "Report the URL of the page in the front tab of a browser (Safari, Google Chrome, or "
        "Brave). Use this to answer 'what page am I on?'. The browser must already be open."
    ),
    properties={
        "browser": {
            "type": "string",
            "enum": ["Safari", "Google Chrome", "Brave Browser"],
            "description": "Which browser to read the front tab from.",
        }
    },
    risk=Risk.SAFE,
    category="web",
)
def browser_current_url(browser: str = "Safari") -> str:
    """Read the front tab's URL from an ALREADY-running Safari/Chrome/Brave. Never launches the
    browser. Returns a friendly string if it's closed or AppleScript fails/times out."""
    app_name, dialect = _resolve_browser(browser)
    if app_name is None or dialect is None:
        msg = "I can read the current page in Safari, Chrome, or Brave."
        audit("browser_current_url", {"browser": browser}, f"refused: {msg}")
        return msg
    if not app_is_running(app_name):
        msg = f"{app_name} isn't open right now."
        audit("browser_current_url", {"browser": app_name}, msg)
        return msg
    try:
        url = run_osa(*_front_url_script(dialect), args=[app_name])
    except subprocess.SubprocessError as e:
        # Includes the -1712 Apple-event timeout (Automation grant not yet given) — handled
        # gracefully rather than raised into the pipeline.
        msg = f"Sorry, I couldn't read the current page from {app_name}."
        audit("browser_current_url", {"browser": app_name}, f"error: {e}")
        return msg
    if not url:
        msg = f"There's no open page in {app_name} right now."
        audit("browser_current_url", {"browser": app_name}, msg)
        return msg
    msg = f"The front tab in {app_name} is {url}."
    audit("browser_current_url", {"browser": app_name}, msg)
    return msg


def _tabs_script(dialect: str):
    """AppleScript that returns the open tabs of the front window as 'title\\turl' lines, one per
    tab. App name is argv item 1 (never interpolated)."""
    if dialect == _SAFARI:
        return (
            "on run argv",
            "set out to {}",
            "tell application (item 1 of argv)",
            "set theTabs to tabs of front window",
            "repeat with t in theTabs",
            'set end of out to ((name of t) & "\t" & (URL of t))',
            "end repeat",
            "end tell",
            'set AppleScript\'s text item delimiters to linefeed',
            "return out as text",
            "end run",
        )
    # Chromium (Chrome / Brave): tabs have `title` and `URL`.
    return (
        "on run argv",
        "set out to {}",
        "tell application (item 1 of argv)",
        "set theTabs to tabs of front window",
        "repeat with t in theTabs",
        'set end of out to ((title of t) & "\t" & (URL of t))',
        "end repeat",
        "end tell",
        'set AppleScript\'s text item delimiters to linefeed',
        "return out as text",
        "end run",
    )


@tool(
    name="list_open_tabs",
    description=(
        "List the open tabs (titles and URLs) in the front window of a browser (Safari, Google "
        "Chrome, or Brave). The browser must already be open."
    ),
    properties={
        "browser": {
            "type": "string",
            "enum": ["Safari", "Google Chrome", "Brave Browser"],
            "description": "Which browser to list tabs from.",
        }
    },
    risk=Risk.SAFE,
    category="web",
)
def list_open_tabs(browser: str = "Safari") -> str:
    """List up to _MAX_TABS open tabs (title + URL) from an ALREADY-running Safari/Chrome/Brave.
    Never launches the browser. Friendly string if it's closed or AppleScript fails/times out."""
    app_name, dialect = _resolve_browser(browser)
    if app_name is None or dialect is None:
        msg = "I can list tabs in Safari, Chrome, or Brave."
        audit("list_open_tabs", {"browser": browser}, f"refused: {msg}")
        return msg
    if not app_is_running(app_name):
        msg = f"{app_name} isn't open right now."
        audit("list_open_tabs", {"browser": app_name}, msg)
        return msg
    try:
        raw = run_osa(*_tabs_script(dialect), args=[app_name])
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't read the tabs from {app_name}."
        audit("list_open_tabs", {"browser": app_name}, f"error: {e}")
        return msg

    rows = [ln for ln in raw.splitlines() if ln.strip()]
    if not rows:
        msg = f"There are no open tabs in {app_name}."
        audit("list_open_tabs", {"browser": app_name}, msg)
        return msg

    total = len(rows)
    capped = rows[:_MAX_TABS]
    titles = []
    for row in capped:
        title = row.split("\t", 1)[0].strip()
        titles.append(title or "(untitled)")
    listing = "; ".join(titles)
    if total > _MAX_TABS:
        msg = f"{total} tabs open in {app_name}. First {_MAX_TABS}: {listing}."
    else:
        msg = f"{total} tabs open in {app_name}: {listing}."
    audit("list_open_tabs", {"browser": app_name, "count": total}, msg)
    return msg
