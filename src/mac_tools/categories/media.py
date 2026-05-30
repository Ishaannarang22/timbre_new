"""
categories/media.py — registry tools for audio/volume/playback.

This module is a THIN WRAPPER. The actual, battle-tested handlers already live in
`src/mac_actions.py` (the original allowlisted executor that twilio_bot.py shipped with):
they clamp, validate, audit, shell out via osascript with list args (no injection), and
return SHORT spoken-friendly strings — exactly the contract every registry tool must meet.
Re-implementing them here would duplicate that careful logic and risk drift, so instead we
`import mac_actions` and call straight through, registering each one as a `@tool`.

Descriptions are lifted verbatim from twilio_bot.py's FunctionSchemas (the wording the LLM
was already tuned against), so the model sees the same tool surface it always has.

Everything here is Risk.SAFE, category="media": volume/mute/playback are non-destructive and
recoverable, so they run immediately with no confirmation. (mac_actions never auto-launches a
media app for a control verb, and play_music only targets an already-installed Spotify.)
"""

import mac_actions

from ..policy import Risk
from ..registry import tool


@tool(
    name="set_volume",
    description="Set this Mac's output volume to an absolute level from 0 to 100.",
    properties={"level": {"type": "integer", "description": "Target volume, 0-100."}},
    required=["level"],
    risk=Risk.SAFE,
    category="media",
)
def set_volume(level: int = 0) -> str:
    # mac_actions.set_volume clamps to 0-100, audits, and never raises.
    return mac_actions.set_volume(level)


@tool(
    name="change_volume",
    description=(
        "Adjust this Mac's SYSTEM output volume by a relative amount. Use a negative delta to "
        "lower (e.g. 'turn it down' -> -10) and positive to raise (e.g. 'louder' -> +10). This "
        "is the overall Mac volume, NOT Spotify's own volume — for the music/song's loudness "
        "use change_spotify_volume instead."
    ),
    properties={"delta": {"type": "integer", "description": "Relative change, e.g. -10 or 10."}},
    required=["delta"],
    risk=Risk.SAFE,
    category="media",
)
def change_volume(delta: int = 0) -> str:
    return mac_actions.change_volume(delta)


@tool(
    name="set_muted",
    description="Mute or unmute this Mac's audio output.",
    properties={"muted": {"type": "boolean", "description": "true to mute, false to unmute."}},
    required=["muted"],
    risk=Risk.SAFE,
    category="media",
)
def set_muted(muted: bool = False) -> str:
    return mac_actions.set_muted(muted)


@tool(
    name="media_control",
    description=(
        "Control music playback on this Mac (Music or Spotify, whichever is already open). "
        "Use play_pause to play/pause, next to skip forward, previous to go back."
    ),
    properties={
        "action": {
            "type": "string",
            "enum": ["play_pause", "next", "previous"],
            "description": "play_pause, next, or previous.",
        }
    },
    required=["action"],
    risk=Risk.SAFE,
    category="media",
)
def media_control(action: str = "") -> str:
    # mac_actions.media_control rejects unknown actions and never auto-launches an app.
    return mac_actions.media_control(action)


@tool(
    name="play_music",
    description=(
        "Play any specific song, artist, album, or vibe on the user's Spotify. Use this "
        "when the user names something they want to hear (e.g. 'play Ed Sheeran', 'I wanna "
        "listen to some jazz', 'put on Bohemian Rhapsody'). Pass whatever they asked for as "
        "the search query. This SEARCHES and starts NEW playback — for play/pause/skip of "
        "what's already playing, use media_control instead."
    ),
    properties={
        "query": {
            "type": "string",
            "description": "The song, artist, album, or description to search and play, e.g. 'Ed Sheeran'.",
        }
    },
    required=["query"],
    risk=Risk.SAFE,
    category="media",
)
def play_music(query: str = "") -> str:
    return mac_actions.play_music(query)


@tool(
    name="set_spotify_volume",
    description=(
        "Set SPOTIFY's own playback volume to an absolute level from 0 to 100. This controls "
        "how loud the MUSIC/song is in Spotify specifically (independent of the Mac's system "
        "output volume). Use this when the user says things like 'set the spotify volume to 30' "
        "or refers to the song/music's loudness."
    ),
    properties={"level": {"type": "integer", "description": "Target Spotify volume, 0-100."}},
    required=["level"],
    risk=Risk.SAFE,
    category="media",
)
def set_spotify_volume(level: int = 0) -> str:
    # mac_actions says so (and stops) if Spotify isn't running — never auto-launches it.
    return mac_actions.set_spotify_volume(level)


@tool(
    name="change_spotify_volume",
    description=(
        "Adjust SPOTIFY's own playback volume by a relative amount. Use a negative delta to "
        "make the music quieter (e.g. 'turn the music down', 'lower the song' -> -10) and a "
        "positive delta to make it louder (e.g. 'turn the music up' -> +10). This is the "
        "MUSIC/song loudness in Spotify, NOT the Mac's overall system volume."
    ),
    properties={"delta": {"type": "integer", "description": "Relative change, e.g. -10 or 10."}},
    required=["delta"],
    risk=Risk.SAFE,
    category="media",
)
def change_spotify_volume(delta: int = 0) -> str:
    return mac_actions.change_spotify_volume(delta)
