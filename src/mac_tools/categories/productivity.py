"""
productivity.py — category="productivity": Notes, Reminders, and Calendar.

House style (matches src/mac_actions.py and the other category modules exactly): every
handler validates/clamps its input, shells out ONLY via runner.run_osa (no shell=True, and
every caller/LLM value reaches AppleScript solely as trailing argv via `on run argv` —
never string-interpolated), audits each action, NEVER raises into the voice pipeline, and
returns a SHORT spoken-friendly string.

Risk policy (docs/tooling/CONTRACT.md): everything here is Risk.SAFE. Creating a note, a
reminder, or a LOCAL calendar event is non-destructive and recoverable — no confirmation
needed. CRITICAL: calendar_create_event makes a *local* event with NO attendees/invitees, so
creating it never SENDS anything (sends are the CONFIRM class; this deliberately avoids that).

All caller text (titles, bodies, reminder text, list/calendar names, dates) is passed as
trailing argv and read inside AppleScript via `on run argv` — there is no injection surface
even though these values originate from an LLM tool call.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa
from ..runner import clean_text as _clean

# Bound caller text so a pathological huge string can't be handed to osascript. These are
# generous (a note body can be long) but finite.
_MAX_TITLE = 200
_MAX_BODY = 5000
_MAX_NAME = 120

# `_clean` (stringify + strip + length-bound) lives in runner.clean_text — shared with
# messaging.py so there's one source of truth. Imported above as `_clean` so call sites are
# unchanged.


# --- NOTES -------------------------------------------------------------------


@tool(
    "notes_create",
    "Create a new note in the Notes app with a title and body text.",
    properties={
        "title": {"type": "string", "description": "The note's title / first line."},
        "body": {"type": "string", "description": "The note's body text."},
    },
    required=["title", "body"],
    risk=Risk.SAFE,
    category="productivity",
)
def notes_create(title: str = "", body: str = "") -> str:
    """Create a Notes note. Title+body are passed as argv (no injection). Notes stores the
    body as HTML-ish text; we set the body to "<title>\\n<body>" so the title shows as the
    note's name. Never raises."""
    t = _clean(title, _MAX_TITLE)
    b = _clean(body, _MAX_BODY)
    if not t:
        msg = "I need a title for the note."
        audit("notes_create", {"title": title}, msg)
        return msg
    try:
        # Notes uses the first line of the body as the note's name. We build "<title>\n<body>"
        # inside AppleScript from the two argv items, so the caller text is never interpolated.
        run_osa(
            "on run argv",
            "set t to item 1 of argv",
            "set b to item 2 of argv",
            'tell application "Notes" to make new note at folder "Notes" '
            'with properties {body:(t & return & b)}',
            "end run",
            args=[t, b],
        )
        msg = f"Created a note titled {t}."
        audit("notes_create", {"title": t}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't create that note."
        audit("notes_create", {"title": t}, f"error: {e}")
        return msg


@tool(
    "notes_append",
    "Append more text to the end of an existing note, found by its title.",
    properties={
        "title": {"type": "string", "description": "The title of the note to add to."},
        "text": {"type": "string", "description": "The text to append to the note."},
    },
    required=["title", "text"],
    risk=Risk.SAFE,
    category="productivity",
)
def notes_append(title: str = "", text: str = "") -> str:
    """Append text to the first note whose name contains the given title. Caller values are
    argv. If no matching note exists, say so. Never raises."""
    t = _clean(title, _MAX_TITLE)
    x = _clean(text, _MAX_BODY)
    if not t:
        msg = "I need the title of the note to add to."
        audit("notes_append", {"title": title}, msg)
        return msg
    if not x:
        msg = "I need some text to add to the note."
        audit("notes_append", {"title": t}, msg)
        return msg
    try:
        # Find the first note whose name contains the title; append "<return><text>" to its
        # body. Both values come from argv. Returns "ok" or "missing".
        res = run_osa(
            "on run argv",
            "set theTitle to item 1 of argv",
            "set theText to item 2 of argv",
            'tell application "Notes"',
            "set matches to (every note whose name contains theTitle)",
            "if (count of matches) is 0 then return \"missing\"",
            "set n to item 1 of matches",
            "set body of n to (body of n & return & theText)",
            "end tell",
            'return "ok"',
            "end run",
            args=[t, x],
        )
        if res.strip() == "missing":
            msg = f"I couldn't find a note titled {t}."
            audit("notes_append", {"title": t}, msg)
            return msg
        msg = f"Added that to the note titled {t}."
        audit("notes_append", {"title": t}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't update that note."
        audit("notes_append", {"title": t}, f"error: {e}")
        return msg


# --- REMINDERS ---------------------------------------------------------------


@tool(
    "reminder_add",
    "Add a reminder (a to-do) to the Reminders app. Optionally put it on a specific list and "
    "give it a due date/time.",
    properties={
        "text": {"type": "string", "description": "What the reminder is, e.g. 'call the dentist'."},
        "list_name": {
            "type": "string",
            "description": "Optional Reminders list to add it to (defaults to your default list).",
        },
        "due": {
            "type": "string",
            "description": "Optional due date/time as a natural date string, e.g. '2026-05-29 9:00 AM'.",
        },
    },
    required=["text"],
    risk=Risk.SAFE,
    category="productivity",
)
def reminder_add(text: str = "", list_name: str = "", due: str = "") -> str:
    """Add a reminder. Text/list/due are passed as argv. If a `due` is given we parse it with
    AppleScript's `date` (best-effort: a bad date is ignored rather than failing). If a
    list_name is given but doesn't exist, fall back to the default list. Never raises."""
    body = _clean(text, _MAX_BODY)
    lst = _clean(list_name, _MAX_NAME)
    when = _clean(due, _MAX_NAME)
    if not body:
        msg = "What should the reminder say?"
        audit("reminder_add", {"text": text}, msg)
        return msg
    try:
        # argv: 1=text, 2=list_name (may be ""), 3=due (may be ""). All read via `on run argv`;
        # nothing is interpolated. We build the new-reminder properties conditionally inside
        # AppleScript so an empty due/list simply isn't applied.
        run_osa(
            "on run argv",
            "set theText to item 1 of argv",
            "set theList to item 2 of argv",
            "set theDue to item 3 of argv",
            'tell application "Reminders"',
            "set props to {name:theText}",
            # Best-effort due parsing: only attempt if a non-empty string was given; a bad
            # date raises inside the try and we just skip the due date.
            'if theDue is not "" then',
            "try",
            "set props to props & {due date:(date theDue)}",
            "end try",
            "end if",
            # Target a named list if it exists, else the default list.
            'if theList is not "" and (exists list theList) then',
            "make new reminder at end of list theList with properties props",
            "else",
            "make new reminder with properties props",
            "end if",
            "end tell",
            "end run",
            args=[body, lst, when],
        )
        where = f" on your {lst} list" if lst else ""
        whenmsg = f" due {when}" if when else ""
        msg = f"Added a reminder{where}: {body}{whenmsg}."
        audit("reminder_add", {"text": body, "list": lst, "due": when}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't add that reminder."
        audit("reminder_add", {"text": body, "list": lst, "due": when}, f"error: {e}")
        return msg


@tool(
    "reminders_list",
    "Read out the open (not-yet-completed) reminders, optionally from a specific list.",
    properties={
        "list_name": {
            "type": "string",
            "description": "Optional Reminders list to read from (defaults to all lists).",
        }
    },
    required=[],
    risk=Risk.SAFE,
    category="productivity",
)
def reminders_list(list_name: str = "") -> str:
    """Report incomplete reminders (optionally scoped to a named list). Read-only. list_name
    is passed as argv. Never raises."""
    lst = _clean(list_name, _MAX_NAME)
    try:
        # Return the names of incomplete reminders, newline-joined, so Python can split cleanly.
        res = run_osa(
            "on run argv",
            "set theList to item 1 of argv",
            "set out to {}",
            'tell application "Reminders"',
            'if theList is not "" then',
            "if not (exists list theList) then return \"nolist\"",
            "set src to (reminders of list theList whose completed is false)",
            "else",
            "set src to (reminders whose completed is false)",
            "end if",
            "repeat with r in src",
            "set end of out to (name of r)",
            "end repeat",
            "end tell",
            'set AppleScript\'s text item delimiters to linefeed',
            "return (out as text)",
            "end run",
            args=[lst],
        )
        if res.strip() == "nolist":
            msg = f"I don't see a list called {lst}."
            audit("reminders_list", {"list": lst}, msg)
            return msg
        items = [line.strip() for line in res.split("\n") if line.strip()]
        scope = f" on your {lst} list" if lst else ""
        if not items:
            msg = f"You have no open reminders{scope}."
            audit("reminders_list", {"list": lst}, msg)
            return msg
        # Keep the spoken list reasonable; cap how many we read out.
        shown = items[:10]
        more = len(items) - len(shown)
        body = ", ".join(shown)
        tail = f", and {more} more" if more > 0 else ""
        msg = f"You have {len(items)} open reminders{scope}: {body}{tail}."
        audit("reminders_list", {"list": lst}, f"{len(items)} items")
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read your reminders."
        audit("reminders_list", {"list": lst}, f"error: {e}")
        return msg


# --- CALENDAR ----------------------------------------------------------------


@tool(
    "calendar_create_event",
    "Create a LOCAL calendar event with a title and start time (and optional end time and "
    "calendar name). This makes an event only on your own calendar — it does NOT invite anyone.",
    properties={
        "title": {"type": "string", "description": "The event title, e.g. 'Dentist'."},
        "start": {
            "type": "string",
            "description": "Start date/time as a natural date string, e.g. '2026-05-29 2:00 PM'.",
        },
        "end": {
            "type": "string",
            "description": "Optional end date/time (defaults to one hour after start).",
        },
        "calendar": {
            "type": "string",
            "description": "Optional calendar name to add it to (defaults to your default calendar).",
        },
    },
    required=["title", "start"],
    risk=Risk.SAFE,
    category="productivity",
)
def calendar_create_event(title: str = "", start: str = "", end: str = "", calendar: str = "") -> str:
    """Create a LOCAL calendar event (NO attendees/invites -> nothing is sent, so this stays
    SAFE). Title/start/end/calendar are passed as argv. If `end` is blank we default to one
    hour after start. If `calendar` is given but missing, fall back to the first calendar.
    Never raises."""
    t = _clean(title, _MAX_TITLE)
    s = _clean(start, _MAX_NAME)
    e_ = _clean(end, _MAX_NAME)
    cal = _clean(calendar, _MAX_NAME)
    if not t:
        msg = "What should the event be called?"
        audit("calendar_create_event", {"title": title}, msg)
        return msg
    if not s:
        msg = "When should the event start?"
        audit("calendar_create_event", {"title": t}, msg)
        return msg
    try:
        # argv: 1=title, 2=start, 3=end(maybe ""), 4=calendar(maybe ""). We parse the dates with
        # AppleScript's `date` inside a try so a bad start date yields a friendly "baddate"
        # rather than an exception. NO attendees are ever added -> this never sends an invite.
        res = run_osa(
            "on run argv",
            "set theTitle to item 1 of argv",
            "set theStart to item 2 of argv",
            "set theEnd to item 3 of argv",
            "set theCal to item 4 of argv",
            "try",
            "set sd to date theStart",
            "on error",
            'return "baddate"',
            "end try",
            'if theEnd is not "" then',
            "try",
            "set ed to date theEnd",
            "on error",
            "set ed to sd + (60 * 60)",
            "end try",
            "else",
            "set ed to sd + (60 * 60)",
            "end if",
            'tell application "Calendar"',
            'if theCal is not "" and (exists calendar theCal) then',
            "set c to calendar theCal",
            "else",
            "set c to item 1 of calendars",
            "end if",
            # summary+start+end only — no attendees property, so nothing is invited/sent.
            "make new event at end of events of c with properties "
            "{summary:theTitle, start date:sd, end date:ed}",
            "end tell",
            'return "ok"',
            "end run",
            args=[t, s, e_, cal],
        )
        if res.strip() == "baddate":
            msg = f"I couldn't understand the start time '{s}'."
            audit("calendar_create_event", {"title": t, "start": s}, msg)
            return msg
        where = f" on your {cal} calendar" if cal else ""
        msg = f"Created the event {t}{where} starting {s}."
        audit("calendar_create_event", {"title": t, "start": s, "end": e_, "cal": cal}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't create that event."
        audit("calendar_create_event", {"title": t, "start": s}, f"error: {e}")
        return msg


@tool(
    "calendar_today",
    "Read out today's calendar events.",
    risk=Risk.SAFE,
    category="productivity",
)
def calendar_today() -> str:
    """Report today's events across all calendars. Read-only; never raises. Returns each event
    as 'HH:MM Title', newline-joined, for Python to format into speech."""
    try:
        res = run_osa(
            'set dayStart to (current date)',
            "set hours of dayStart to 0",
            "set minutes of dayStart to 0",
            "set seconds of dayStart to 0",
            "set dayEnd to dayStart + (24 * 60 * 60)",
            "set out to {}",
            'tell application "Calendar"',
            "repeat with c in calendars",
            "set evs to (every event of c whose start date is greater than or equal to dayStart "
            "and start date is less than dayEnd)",
            "repeat with ev in evs",
            "set sd to start date of ev",
            "set hh to (hours of sd) as text",
            "set mm to (minutes of sd) as text",
            'if (count mm) is 1 then set mm to "0" & mm',
            'set end of out to (hh & ":" & mm & " " & (summary of ev))',
            "end repeat",
            "end repeat",
            "end tell",
            "set AppleScript's text item delimiters to linefeed",
            "return (out as text)",
        )
        items = [line.strip() for line in res.split("\n") if line.strip()]
        if not items:
            msg = "You have nothing on your calendar today."
            audit("calendar_today", {}, msg)
            return msg
        body = "; ".join(items[:10])
        msg = f"Today you have {len(items)} events: {body}."
        audit("calendar_today", {}, f"{len(items)} events")
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't read today's calendar."
        audit("calendar_today", {}, f"error: {e}")
        return msg


@tool(
    "calendar_next_event",
    "Say what your next upcoming calendar event is.",
    risk=Risk.SAFE,
    category="productivity",
)
def calendar_next_event() -> str:
    """Report the soonest upcoming event across all calendars. Read-only; never raises."""
    try:
        # Walk every calendar, track the earliest event whose start is in the future. Return
        # "MM/DD HH:MM Title" or "" if there's nothing upcoming.
        res = run_osa(
            "set now to (current date)",
            "set bestDate to missing value",
            'set bestDesc to ""',
            'tell application "Calendar"',
            "repeat with c in calendars",
            "set evs to (every event of c whose start date is greater than now)",
            "repeat with ev in evs",
            "set sd to start date of ev",
            "if bestDate is missing value or sd < bestDate then",
            "set bestDate to sd",
            "set hh to (hours of sd) as text",
            "set mm to (minutes of sd) as text",
            'if (count mm) is 1 then set mm to "0" & mm',
            'set bestDesc to ((month of sd as text) & " " & (day of sd as text) & " " & hh & ":" & mm & " " & (summary of ev))',
            "end if",
            "end repeat",
            "end repeat",
            "end tell",
            "return bestDesc",
        )
        desc = res.strip()
        if not desc:
            msg = "You have no upcoming events."
            audit("calendar_next_event", {}, msg)
            return msg
        msg = f"Your next event is {desc}."
        audit("calendar_next_event", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't find your next event."
        audit("calendar_next_event", {}, f"error: {e}")
        return msg
