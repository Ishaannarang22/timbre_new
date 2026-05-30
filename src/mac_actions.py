"""
mac_actions.py — the SAFE, allowlisted executor for voice-controlled Mac media/volume.

Design: least-privilege, default-deny. There is NO generic "run this command" path. The
only things that can ever happen are the bounded handlers defined below — that fixed set
IS the allowlist. Each one:

  * validates / clamps its input (volume always lands in 0-100; media action ∈ a fixed set),
  * shells out to /usr/bin/osascript with arguments passed as a *list* (never shell=True,
    never string interpolation of user text into a shell line — so there is no command
    injection surface even though some args originate from an LLM tool call),
  * appends an audit line to logs/actions.log (timestamp, action, args, result),
  * returns a SHORT human-readable string the agent can speak back verbatim.

The volume/mute handlers use AppleScript's built-in `volume` verbs (no app needed). Media
control targets whichever of Music/Spotify is already running — and NEVER auto-launches an
app if neither is open (we just say "nothing's playing").
"""

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# logs/actions.log lives next to the project's other logs (../logs from this src/ file).
_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "actions.log"

# Only these media apps are ever touched, and only if ALREADY running. Order = preference
# when both happen to be open (Music first, then Spotify).
_MEDIA_APPS = ("Music", "Spotify")
_MEDIA_ACTIONS = {
    "play_pause": "playpause",
    "next": "next track",
    "previous": "previous track",
}

# A tight timeout so a wedged osascript can never stall the voice pipeline.
_OSA_TIMEOUT = 5.0
# Starting a brand-new track in Spotify can be slow (the app fetches + buffers the
# stream). We saw `play track` time out at 5s live once, so play_music gets its OWN
# longer timeout — but ONLY that one call. Every other osascript stays snappy (5s) so a
# wedged volume/media call still can't stall the voice pipeline.
_OSA_PLAY_TIMEOUT = 12.0


def _audit(action: str, args, result: str) -> None:
    """Append one audit line. Best-effort: logging must never break an action."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a") as f:
            f.write(f"{ts}\taction={action}\targs={args!r}\tresult={result!r}\n")
    except OSError:
        pass


def _osa(*script_lines: str, timeout: float = _OSA_TIMEOUT) -> str:
    """Run osascript with each statement as a separate -e arg (no shell, no interpolation
    of caller text). Returns stripped stdout; raises subprocess.SubprocessError on failure.

    `timeout` defaults to the tight _OSA_TIMEOUT (5s) so a wedged call can never stall the
    voice pipeline. play_music passes the longer _OSA_PLAY_TIMEOUT for its `play track` call
    only, since starting a fresh Spotify stream can legitimately take a few seconds."""
    cmd = ["/usr/bin/osascript"]
    for line in script_lines:
        cmd += ["-e", line]
    out = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True
    )
    return out.stdout.strip()


def _clamp(n: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(n)))


def get_volume() -> int:
    """Current output volume as an int 0-100."""
    try:
        raw = _osa("output volume of (get volume settings)")
        vol = _clamp(int(raw))
        _audit("get_volume", {}, str(vol))
        return vol
    except (subprocess.SubprocessError, ValueError) as e:
        _audit("get_volume", {}, f"error: {e}")
        # Surface a safe default rather than throwing into the pipeline.
        return 0


def set_volume(level: int) -> str:
    """Set absolute output volume (clamped to 0-100). Returns a spoken-friendly string."""
    lvl = _clamp(level)
    try:
        # AppleScript wants a bare number; we build the statement from our OWN clamped int,
        # not from raw caller text, so there is nothing to inject.
        _osa(f"set volume output volume {lvl}")
        msg = f"Volume set to {lvl}."
        _audit("set_volume", {"level": lvl}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the volume."
        _audit("set_volume", {"level": lvl}, f"error: {e}")
        return msg


def change_volume(delta: int) -> str:
    """Adjust volume by a relative delta; result clamped to 0-100."""
    try:
        d = int(delta)
    except (TypeError, ValueError):
        d = 0
    current = get_volume()
    target = _clamp(current + d)
    try:
        _osa(f"set volume output volume {target}")
        verb = "up" if d > 0 else "down" if d < 0 else "to"
        msg = f"Volume {verb} to {target}." if verb != "to" else f"Volume at {target}."
        _audit("change_volume", {"delta": d, "from": current, "to": target}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the volume."
        _audit("change_volume", {"delta": d}, f"error: {e}")
        return msg


def set_muted(muted: bool) -> str:
    """Mute or unmute output."""
    flag = bool(muted)
    try:
        _osa(f"set volume output muted {'true' if flag else 'false'}")
        msg = "Muted." if flag else "Unmuted."
        _audit("set_muted", {"muted": flag}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the mute setting."
        _audit("set_muted", {"muted": flag}, f"error: {e}")
        return msg


def _running_media_app() -> str | None:
    """Return the first of Music/Spotify that is ALREADY running, else None.
    Never launches anything."""
    for app in _MEDIA_APPS:
        try:
            # `application "X" is running` is True only if the app is already open; it does
            # NOT launch the app. App name is from our fixed _MEDIA_APPS tuple, not caller text.
            res = _osa(f'application "{app}" is running')
            if res.strip().lower() == "true":
                return app
        except subprocess.SubprocessError:
            continue
    return None


def media_control(action: str) -> str:
    """Control playback (play_pause / next / previous) on whichever supported player is
    already running. If neither Music nor Spotify is open, say nothing's playing — and do
    NOT launch an app."""
    act = str(action).strip().lower()
    if act not in _MEDIA_ACTIONS:
        msg = "I can only play, pause, or skip tracks."
        _audit("media_control", {"action": action}, msg)
        return msg

    app = _running_media_app()
    if app is None:
        msg = "Nothing's playing right now."
        _audit("media_control", {"action": act}, msg)
        return msg

    verb = _MEDIA_ACTIONS[act]  # from a fixed dict — safe to embed
    try:
        _osa(f'tell application "{app}" to {verb}')
        if act == "play_pause":
            msg = f"Toggled playback in {app}."
        elif act == "next":
            msg = f"Skipped to the next track in {app}."
        else:
            msg = f"Went back to the previous track in {app}."
        _audit("media_control", {"action": act, "app": app}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't control {app}."
        _audit("media_control", {"action": act, "app": app}, f"error: {e}")
        return msg


# --- PLAY ANY SONG/ARTIST ON SPOTIFY -----------------------------------------
# This is the one handler that reaches OUT to the network (Spotify Web API) before
# acting locally. Flow: client-credentials token (CACHED at module level until it
# expires) -> /v1/search for the top track -> tell the already-installed Spotify
# desktop app to play that exact track URI. As everywhere in this file, the osascript
# call passes args as a list (no shell), and the only caller-derived value that ever
# touches AppleScript (the track URI) is validated against a strict regex first, so
# there is no injection surface even though `query` originates from an LLM tool call.

_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
# Track URIs look like spotify:track:<base62 id>. We REQUIRE this exact shape before
# we'll ever hand the value to AppleScript.
_TRACK_URI_RE = re.compile(r"^spotify:track:[A-Za-z0-9]+$")
# Network timeout for the token + search calls — short so a slow Spotify API can't
# stall the voice pipeline.
_SPOTIFY_HTTP_TIMEOUT = 10.0

# Module-level token cache: (access_token, expiry_monotonic). We refresh only when the
# cached token is missing or within a small skew of expiring, so a busy call doesn't
# re-auth on every "play X".
_SPOTIFY_TOKEN: str | None = None
_SPOTIFY_TOKEN_EXP: float = 0.0
_TOKEN_EXPIRY_SKEW = 30.0  # refresh this many seconds BEFORE the real expiry


def _spotify_token() -> str:
    """Return a valid client-credentials access token, refreshing only when expired.
    Raises urllib/KeyError/ValueError on failure (caller handles)."""
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXP
    now = time.monotonic()
    if _SPOTIFY_TOKEN and now < _SPOTIFY_TOKEN_EXP - _TOKEN_EXPIRY_SKEW:
        return _SPOTIFY_TOKEN

    client_id = os.environ["SPOTIFY_CLIENT_ID"]
    client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        _SPOTIFY_TOKEN_URL,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=_SPOTIFY_HTTP_TIMEOUT) as r:
        payload = json.load(r)
    _SPOTIFY_TOKEN = payload["access_token"]
    # Cache against the documented expires_in (~3600s); default to 3600 if absent.
    _SPOTIFY_TOKEN_EXP = time.monotonic() + float(payload.get("expires_in", 3600))
    return _SPOTIFY_TOKEN


def _spotify_search_top_track(query: str, token: str) -> dict | None:
    """Return the top matching track dict (or None if no results)."""
    q = urllib.parse.quote(query)
    url = f"{_SPOTIFY_SEARCH_URL}?q={q}&type=track&limit=1"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=_SPOTIFY_HTTP_TIMEOUT) as r:
        payload = json.load(r)
    items = payload.get("tracks", {}).get("items", [])
    return items[0] if items else None


def play_music(query: str) -> str:
    """Search Spotify for `query` and play the top track on the running Spotify desktop
    app. Returns a SHORT spoken-friendly string. Never raises — any network/osascript
    failure becomes a friendly spoken error so the voice pipeline keeps going."""
    q = str(query).strip()
    if not q:
        msg = "Tell me what you'd like to hear."
        _audit("play_music", {"query": query}, msg)
        return msg

    # 1) Token (cached) + search. Network errors -> friendly spoken error.
    try:
        token = _spotify_token()
        track = _spotify_search_top_track(q, token)
    except (urllib.error.URLError, ValueError, KeyError, OSError) as e:
        msg = "Sorry, I couldn't reach Spotify just now."
        _audit("play_music", {"query": q}, f"error: {e}")
        return msg

    if track is None:
        msg = f"I couldn't find anything for '{q}' on Spotify."
        _audit("play_music", {"query": q}, msg)
        return msg

    uri = track.get("uri", "")
    name = track.get("name", "that track")
    artists = track.get("artists") or []
    artist = artists[0].get("name", "an unknown artist") if artists else "an unknown artist"

    # 2) Validate the URI shape BEFORE it ever touches AppleScript (defense in depth —
    # this is the only caller/network-derived value that reaches osascript).
    if not _TRACK_URI_RE.match(uri):
        msg = "Sorry, I couldn't play that track."
        _audit("play_music", {"query": q, "uri": uri}, f"error: bad uri {uri!r}")
        return msg

    # 3) Play it. The validated URI is inlined into the AppleScript statement (osascript
    # has no clean way to bind it as a separate arg the way `set volume N` does), but the
    # strict regex above guarantees it's only [A-Za-z0-9:] — nothing that could break out
    # of the AppleScript string or inject a shell.
    try:
        # Longer timeout HERE ONLY: starting a fresh track can be slow (saw a 5s timeout
        # live). Other osascript calls keep the snappy default.
        _osa(f'tell application "Spotify" to play track "{uri}"', timeout=_OSA_PLAY_TIMEOUT)
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't play that on Spotify."
        _audit("play_music", {"query": q, "uri": uri}, f"error: {e}")
        return msg

    msg = f"Now playing {name} by {artist}."
    _audit("play_music", {"query": q, "uri": uri, "name": name, "artist": artist}, msg)
    return msg


# --- SPOTIFY'S OWN PLAYBACK VOLUME -------------------------------------------
# Distinct from the Mac SYSTEM output volume (set_volume/change_volume above). This Mac's
# system output is an HDMI display with no software volume, so `set volume` is a silent
# no-op for the user — but Spotify exposes its OWN `sound volume` (0-100) via AppleScript,
# which works regardless of the output device. These two handlers drive THAT. As everywhere
# in this file: clamp to 0-100, build the AppleScript statement from our own clamped int
# (never caller text, so no injection), audit-log, return a short spoken-friendly string.
# We NEVER auto-launch Spotify: if it isn't running we say so and stop.


def set_spotify_volume(level: int) -> str:
    """Set Spotify's own playback volume to an absolute level (clamped 0-100). If Spotify
    isn't running, say so (never auto-launch). Returns a spoken-friendly string."""
    lvl = _clamp(level)
    try:
        # `application "Spotify" is running` is True only if already open; never launches it.
        if _osa('application "Spotify" is running').strip().lower() != "true":
            msg = "Spotify isn't open right now."
            _audit("set_spotify_volume", {"level": lvl}, msg)
            return msg
        # Number comes from our OWN clamped int, not raw caller text — nothing to inject.
        _osa(f'tell application "Spotify" to set sound volume to {lvl}')
        msg = f"Spotify volume set to {lvl}."
        _audit("set_spotify_volume", {"level": lvl}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't change the Spotify volume."
        _audit("set_spotify_volume", {"level": lvl}, f"error: {e}")
        return msg


def change_spotify_volume(delta: int) -> str:
    """Adjust Spotify's own playback volume by a relative delta; result clamped to 0-100.
    If Spotify isn't running, say so (never auto-launch)."""
    try:
        d = int(delta)
    except (TypeError, ValueError):
        d = 0
    try:
        if _osa('application "Spotify" is running').strip().lower() != "true":
            msg = "Spotify isn't open right now."
            _audit("change_spotify_volume", {"delta": d}, msg)
            return msg
        current = _clamp(int(_osa('tell application "Spotify" to get sound volume')))
        target = _clamp(current + d)
        _osa(f'tell application "Spotify" to set sound volume to {target}')
        verb = "up" if d > 0 else "down" if d < 0 else "to"
        msg = (
            f"Spotify volume {verb} to {target}."
            if verb != "to"
            else f"Spotify volume at {target}."
        )
        _audit("change_spotify_volume", {"delta": d, "from": current, "to": target}, msg)
        return msg
    except (subprocess.SubprocessError, ValueError) as e:
        msg = "Sorry, I couldn't change the Spotify volume."
        _audit("change_spotify_volume", {"delta": d}, f"error: {e}")
        return msg
