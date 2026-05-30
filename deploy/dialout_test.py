"""
Outbound test: place a Twilio call that streams to the deployed Pipecat Cloud agent.

This runs LOCALLY (it just triggers Twilio's REST API). When the callee answers, Twilio fetches
the inline TwiML below, which <Connect><Stream>s the call to the `timbre` agent on Pipecat Cloud.
The agent sees direction=outbound (passed as a <Parameter>) and opens with the outbound greeting.

Usage:
    ../.venv/bin/python dialout_test.py [+1XXXXXXXXXX]
    # number to call; defaults to TARGET_PHONE_NUMBER from .env

Reads from the repo-root .env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER,
and (optionally) TARGET_PHONE_NUMBER.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from twilio.rest import Client

# .env lives at the repo root (one level up from deploy/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PIPECAT_HOST = os.getenv("PIPECAT_SERVICE_HOST", "timbre.linear-sturgeon-tan-585")
WS_URL = os.getenv("PIPECAT_WS_URL", "wss://api.pipecat.daily.co/ws/twilio")


def main() -> None:
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_PHONE_NUMBER"]
    to_number = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TARGET_PHONE_NUMBER")
    if not to_number:
        raise SystemExit("No number to call. Pass one as an argument or set TARGET_PHONE_NUMBER.")

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{WS_URL}">'
        f'<Parameter name="_pipecatCloudServiceHost" value="{PIPECAT_HOST}"/>'
        '<Parameter name="direction" value="outbound"/>'
        "</Stream></Connect></Response>"
    )

    client = Client(account_sid, auth_token)
    call = client.calls.create(to=to_number, from_=from_number, twiml=twiml)
    print(f"placed outbound call -> {to_number} (sid={call.sid}); streaming to {PIPECAT_HOST}")


if __name__ == "__main__":
    main()
