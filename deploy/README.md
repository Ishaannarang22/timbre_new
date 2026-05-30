# timbre — Pipecat Cloud deploy

Cloud form of the voice agent. Pipecat Cloud hosts the Twilio Media Streams websocket and
invokes `bot(runner_args)` in `bot.py`. No cloudflared tunnel, no self-hosted FastAPI server.

## Files
- `bot.py` — the agent entrypoint (Deepgram STT → NVIDIA Nemotron LLM → Cartesia TTS).
- `pyproject.toml` + `uv.lock` — deps the cloud build installs.
- `Dockerfile` — `FROM dailyco/pipecat-base:latest`; built server-side (no local Docker needed).
- `pcc-deploy.toml` — agent name (`timbre`), secret set, scaling.
- `dialout_test.py` — local script to place an OUTBOUND test call (Twilio REST → this agent).

## Deploy (from this directory)
```bash
# 1. Upload secrets (one secret set, from a minimal env file — NOT committed)
pipecat cloud secrets set timbre-secrets --file secrets.env

# 2. Build + deploy (server-side cloud build; no local Docker / registry)
pipecat cloud deploy
```

## Inbound: point a Twilio number at the agent
Twilio Console → Phone Numbers → your number → "A call comes in" → TwiML Bin:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://api.pipecat.daily.co/ws/twilio">
      <Parameter name="_pipecatCloudServiceHost" value="timbre.linear-sturgeon-tan-585"/>
    </Stream>
  </Connect>
</Response>
```
(`timbre` = agent name, `linear-sturgeon-tan-585` = org. Non-default region → prefix the host,
e.g. `wss://eu-central.api.pipecat.daily.co/ws/twilio`.)

## Outbound: place a test call
```bash
# from the repo root venv (has the `twilio` package)
../.venv/bin/python dialout_test.py +1XXXXXXXXXX   # number to call; defaults to TARGET_PHONE_NUMBER
```
