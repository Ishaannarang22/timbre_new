"""
messaging.py — category="messaging": iMessage, Mail, and unread-mail count.

House style (matches src/mac_actions.py and the other category modules exactly): every
handler validates its input, shells out ONLY via runner.run_osa (no shell=True, and every
caller/LLM value reaches AppleScript solely as trailing argv via `on run argv` — never
string-interpolated), audits each action, NEVER raises into the voice pipeline, and returns
a SHORT spoken-friendly string.

Risk policy (docs/tooling/CONTRACT.md): SENDS are the canonical CONFIRM class. send_imessage
and send_mail are Risk.CONFIRM — dispatch() stages them with a spoken read-back of the
recipient + content and runs the ACTUAL send only after the owner confirms via the broker.
mail_unread_count is read-only -> Risk.SAFE.

The CONFIRM tools' `do` action (the handler itself, deferred by dispatch) performs the real
Messages/Mail AppleScript send. Recipient/body/to/subject are all passed as argv — there is
no injection surface even though these originate from an LLM tool call.
"""

import subprocess

from ..policy import Risk
from ..registry import tool
from ..runner import audit, run_osa
from ..runner import clean_text as _clean

# Bound caller text so a pathological huge string can't be handed to osascript.
_MAX_RECIPIENT = 120
_MAX_SUBJECT = 200
_MAX_BODY = 5000

# `_clean` (stringify + strip + length-bound) lives in runner.clean_text — shared with
# productivity.py so there's one source of truth. Imported above as `_clean` so call sites are
# unchanged.


# --- iMESSAGE ----------------------------------------------------------------


@tool(
    "send_imessage",
    "Send an iMessage (text message) to a person or phone number. Because this SENDS, it is "
    "read back and confirmed before going out.",
    properties={
        "recipient": {
            "type": "string",
            "description": "Who to text — a phone number, email, or contact name/handle.",
        },
        "body": {"type": "string", "description": "The message text to send."},
    },
    required=["recipient", "body"],
    risk=Risk.CONFIRM,
    category="messaging",
    # Spoken read-back built by dispatch() BEFORE anything is sent. Mirrors the wording the
    # owner specified. The real send only happens on confirm via the broker.
    confirm_summary=lambda recipient="", body="": (
        f"Send a message to {_clean(recipient, _MAX_RECIPIENT) or 'them'} saying: "
        f"{_clean(body, _MAX_BODY) or '(nothing)'}. Send it?"
    ),
)
def send_imessage(recipient: str = "", body: str = "") -> str:
    """Actually send an iMessage (runs ONLY after the owner confirms via the broker). Recipient
    and body reach AppleScript only as argv. Never raises."""
    to = _clean(recipient, _MAX_RECIPIENT)
    text = _clean(body, _MAX_BODY)
    if not to:
        msg = "I need someone to send the message to."
        audit("send_imessage", {"recipient": recipient}, msg)
        return msg
    if not text:
        msg = "I need a message to send."
        audit("send_imessage", {"recipient": to}, msg)
        return msg
    try:
        # Send via the iMessage service to the given recipient. Both values are argv items.
        run_osa(
            "on run argv",
            "set theTo to item 1 of argv",
            "set theBody to item 2 of argv",
            'tell application "Messages"',
            "set svc to 1st service whose service type = iMessage",
            "set buddy to participant theTo of svc",
            "send theBody to buddy",
            "end tell",
            "end run",
            args=[to, text],
        )
        msg = f"Sent your message to {to}."
        audit("send_imessage", {"recipient": to}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't send the message to {to}."
        audit("send_imessage", {"recipient": to}, f"error: {e}")
        return msg


# --- MAIL --------------------------------------------------------------------


@tool(
    "send_mail",
    "Send an email through the Mail app to a recipient with a subject and body. Because this "
    "SENDS, it is read back and confirmed before going out.",
    properties={
        "to": {"type": "string", "description": "The recipient's email address."},
        "subject": {"type": "string", "description": "The email subject line."},
        "body": {"type": "string", "description": "The email body text."},
    },
    required=["to", "subject", "body"],
    risk=Risk.CONFIRM,
    category="messaging",
    # Read-back covers recipient + subject (per owner spec). The real send only runs on confirm.
    confirm_summary=lambda to="", subject="", body="": (
        f"Send an email to {_clean(to, _MAX_RECIPIENT) or 'them'} "
        f"with the subject: {_clean(subject, _MAX_SUBJECT) or '(no subject)'}. Send it?"
    ),
)
def send_mail(to: str = "", subject: str = "", body: str = "") -> str:
    """Actually send an email via Mail (runs ONLY after the owner confirms via the broker).
    to/subject/body reach AppleScript only as argv. Never raises."""
    addr = _clean(to, _MAX_RECIPIENT)
    subj = _clean(subject, _MAX_SUBJECT)
    text = _clean(body, _MAX_BODY)
    if not addr:
        msg = "I need an email address to send to."
        audit("send_mail", {"to": to}, msg)
        return msg
    try:
        # Build a new outgoing message, attach the recipient, and send. All three values are
        # argv items read via `on run argv` — nothing is interpolated.
        run_osa(
            "on run argv",
            "set theTo to item 1 of argv",
            "set theSubject to item 2 of argv",
            "set theBody to item 3 of argv",
            'tell application "Mail"',
            "set newMsg to make new outgoing message with properties "
            "{subject:theSubject, content:theBody, visible:false}",
            "tell newMsg",
            "make new to recipient at end of to recipients with properties {address:theTo}",
            "end tell",
            "send newMsg",
            "end tell",
            "end run",
            args=[addr, subj, text],
        )
        msg = f"Sent your email to {addr}."
        audit("send_mail", {"to": addr, "subject": subj}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = f"Sorry, I couldn't send the email to {addr}."
        audit("send_mail", {"to": addr, "subject": subj}, f"error: {e}")
        return msg


@tool(
    "mail_unread_count",
    "Say how many unread emails are in the Mail inbox.",
    risk=Risk.SAFE,
    category="messaging",
)
def mail_unread_count() -> str:
    """Report the unread message count. Read-only; never raises."""
    try:
        raw = run_osa('tell application "Mail" to get unread count of inbox')
        try:
            n = int(raw)
        except ValueError:
            n = 0
        if n == 0:
            msg = "You have no unread emails."
        elif n == 1:
            msg = "You have 1 unread email."
        else:
            msg = f"You have {n} unread emails."
        audit("mail_unread_count", {}, msg)
        return msg
    except subprocess.SubprocessError as e:
        msg = "Sorry, I couldn't check your unread mail."
        audit("mail_unread_count", {}, f"error: {e}")
        return msg
