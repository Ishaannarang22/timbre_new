# M1/M2 — Twilio phone agent (real two-way conversation)

**Files:** `src/twilio_bot.py` · `src/run_morning_call.py` · `src/call_me.py`
**Goal:** Take the same STT→LLM→TTS brain from M0 and put it on the telephone. Twilio
rings your phone; you talk; the agent listens and replies in real-time. A 7 AM cron fires
it every morning.

---

## Why a public URL is required

Twilio is a cloud service — when it dials your number and connects, it needs to reach your
server to ask "what should I say/do?" That webhook and the real-time audio WebSocket both
need a **publicly routable HTTPS/WSS URL**. Your laptop is not publicly routable, so we
use **cloudflared** quick-tunnels: a single binary that opens an outbound connection to
Cloudflare's edge and hands back a random `*.trycloudflare.com` URL that routes to your
local server. No account, no config files, no port-forwarding.

```
your laptop :8080  ←── cloudflared ──── Cloudflare edge ──── trycloudflare.com/xxx
                                              ↑
                                           Twilio
```

The tunnel URL is ephemeral (changes every run), which is fine because `run_morning_call.py`
creates a fresh tunnel and gives Twilio the new URL on each invocation.

---

## The outbound call flow

```
run_morning_call.py boots uvicorn (twilio_bot:app) on localhost:8080
         │
         ▼
cloudflared quick-tunnel → public https://xxx.trycloudflare.com
         │
         ▼  (Twilio REST API: calls.create(url="…/twiml"))
Twilio dials +1-814-826-8818
         │
         ▼  (call connects, Twilio fetches the TwiML webhook)
GET/POST https://xxx.trycloudflare.com/twiml
         │  returns: <Connect><Stream url="wss://xxx.trycloudflare.com/ws"/>
         ▼
Twilio opens a WebSocket to /ws and streams 8kHz μ-law audio both ways
         │
         ▼
Pipecat pipeline (inside /ws handler)
```

One subtlety: the quote is generated *at `/twiml` time* (while Twilio is still setting up
the call) and stored in a dict keyed by CallSid. By the time the WebSocket opens and the
agent speaks, the quote is already ready — no LLM call-to-speech latency on the greeting.

---

## The pipeline (what runs inside the WebSocket handler)

```
Twilio audio (8k μ-law)
   │  FastAPIWebsocketTransport + TwilioFrameSerializer
   ▼
[Silero VAD]  ← detects when you're talking
   ▼
DeepgramSTTService  ← speech → text  (streaming partial results)
   ▼
aggregator.user()   ← adds your transcript to conversation context
   ▼
OpenAILLMService    ← Nemotron on build.nvidia.com (OpenAI-compatible)
   │  enable_thinking: False  (prevents hidden reasoning delay — see M0 lesson)
   │  8s hard timeout + 2 retries  (guards against 20-30s tail-latency stalls)
   ▼
aggregator.assistant()  ← adds reply to context (memory across turns)
   ▼
CartesiaTTSService  ← text → speech  (Brooke voice, Sonic model)
   ▼
Twilio audio (8k μ-law)  ← back to your phone
```

| Processor | Backed by | Why |
|-----------|-----------|-----|
| Transport | `FastAPIWebsocketTransport` + `TwilioFrameSerializer` | Handles μ-law encoding and Twilio's JSON-wrapped audio frames |
| STT | Deepgram | NVIDIA speech models (Parakeet/ASR) are partner-gated; Deepgram works reliably today |
| LLM | Nemotron `nemotron-3-nano-30b-a3b` via NVIDIA hosted API | Same brain as M0; 30B MoE (~3B active), stall-free at this model size |
| TTS | Cartesia Sonic — **Brooke** voice | Our voice. Twilio's built-in `<Say>` (Polly) is the fallback, not the primary |

**Why 8kHz throughout:** Telephony audio is natively 8kHz μ-law (the PSTN standard). Running
the whole pipeline at `SR=8000` avoids any resampling mismatch — what comes in from Twilio
is what goes out, and Deepgram + Cartesia both accept 8kHz.

---

## The `call_me.py` inline-TwiML fallback

`call_me.py` is the hardened fallback path. It requires no running server and no tunnel:
you hand Twilio the entire spoken script upfront as TwiML XML, and Twilio reads it as a
monologue. This works from any machine with credentials.

`run_morning_call.py` uses it automatically if any step of the rich path fails:
- server doesn't become healthy in 25s
- cloudflared doesn't produce a URL in 30s
- the public URL isn't reachable in 90s
- Twilio call creation throws

The design principle: **the morning call must never silently no-op**. Degrading to a monologue
is far better than no call at all. The fallback uses Twilio's built-in `Polly.Joanna-Neural`
voice (which has no Cartesia dependency) and a Nemotron-generated quote with its own fast
timeout and curated hardcoded fallback.

---

## The 7 AM cron entry

The orchestrator (`run_morning_call.py`) is fully self-contained — it boots the server, opens
the tunnel, places the call, waits for completion, and tears everything down. That makes a
simple cron entry sufficient.

**Crontab line (install via `crontab -e`):**
```
0 7 * * * cd /Users/node3/projects/voice_fun && PATH=/opt/homebrew/bin:/usr/bin:/bin:$PATH /Users/node3/projects/voice_fun/.venv/bin/python src/run_morning_call.py >> /Users/node3/projects/voice_fun/logs/morning_call.log 2>&1
```

**Why `PATH=/opt/homebrew/bin:…`:** cron runs with a minimal environment — none of your shell
profile is sourced. `cloudflared` lives in `/opt/homebrew/bin` (Homebrew default), which is not
on cron's default PATH. Without it, `shutil.which("cloudflared")` returns None and the script
falls back to the hardcoded `/opt/homebrew/bin/cloudflared` path — but it's cleaner to put the
directory on PATH explicitly so all Homebrew tools are available.

**Why `0 7 * * *` is correct for Pacific year-round:** macOS cron uses the system's *local*
clock, and the machine is set to the Pacific timezone (confirmed: `date +%Z` = `PDT` in summer,
`PST` in winter). Local cron schedules automatically follow DST transitions — a `0 7` entry
fires at 7:00 AM Pacific whether that's UTC-7 (PDT) or UTC-8 (PST). No timezone math needed.

---

## macOS-sleep caveat — the most important thing to know

**cron will NOT fire if the Mac is asleep.** This is the most common cause of missed 7 AM
calls. macOS suspends the cron daemon when the machine sleeps; if it's still asleep at 7:00 AM,
the job never runs (unlike `launchd`, cron does not catch up on missed jobs after wake).

What you must do:
1. **Keep the Mac awake at 7 AM.** Plug it in and configure System Settings → Battery → "Prevent
   automatic sleeping when the display is off" (or set sleep to "Never" while plugged in).
2. **`caffeinate` option:** run `caffeinate -i &` in a terminal before bed to assert an "idle
   sleep prevention" assertion. Not persistent across reboots, but useful for a single night.
3. **Scheduled wake (best option without launchd):** System Settings → Battery → Schedule → "Wake
   for network access" or "Start up or wake" at 6:50 AM. This works even from deep sleep as long
   as the Mac is plugged in and the lid is at least slightly open (or it's a desktop/Mac mini).

**The more robust macOS-native alternative — `launchd`:**
`launchd` (via a `~/Library/LaunchAgents/*.plist`) is Apple's recommended replacement for cron.
Its key advantage for this use case: `StartCalendarInterval` fires at the scheduled time *or at
the next opportunity after wake* — so if the machine was asleep, the job runs when it wakes up.
For a 7 AM call a few minutes late is fine; silent skip is not. We chose cron here for
simplicity (one command to install), but if missed calls become a problem, migrating to a
LaunchAgent plist is the right fix. The plist shape would be:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.voice_fun.morning_call</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/node3/projects/voice_fun/.venv/bin/python</string>
    <string>/Users/node3/projects/voice_fun/src/run_morning_call.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key>
  <string>/Users/node3/projects/voice_fun/logs/morning_call.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/node3/projects/voice_fun/logs/morning_call.log</string>
  <key>WorkingDirectory</key>
  <string>/Users/node3/projects/voice_fun</string>
</dict>
</plist>
```

Install: `cp that.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/that.plist`

---

## Key concepts learned in M1/M2

- **TwiML `<Connect><Stream>`:** the verb that hands off audio to a WebSocket. This is how
  Twilio bridges the PSTN call to your code. Everything after this is your problem, not Twilio's.
- **`TwilioFrameSerializer`:** Twilio doesn't send raw PCM — it sends JSON messages with
  base64-encoded μ-law audio. The serializer unpacks those and repacks your output audio.
  Pipecat handles this; you never see the raw WebSocket messages.
- **8kHz everywhere:** set `audio_in_sample_rate` and `audio_out_sample_rate` to 8000 and also
  pass `SR` to the STT and TTS services. If there's a mismatch, audio sounds like a robot or
  chipmunk. The symptom you'd see: speech that sounds sped up, or STT that produces garbage.
- **`LLMRunFrame` for the opening greeting:** the same trick as M0 — inject a turn before the
  user speaks so the agent greets first. Voice UX expects the agent to speak first on an outbound
  call; silence on pickup is disorienting.
- **The 90-second tunnel warmup:** cloudflared quick-tunnels are assigned almost instantly, but
  Cloudflare's edge routing often takes 30–90 seconds to propagate. The orchestrator polls
  `GET /health` on the public URL before telling Twilio to use it. Skipping this wait causes
  Twilio to 4xx the webhook and the call silently falls back to the monologue.
- **Max-duration guard:** `MAX_CALL_SECS=150` (2.5 min). Unattended cron + an open-ended LLM
  conversation = unlimited phone charges. The guard queues an `EndFrame` after the ceiling,
  which causes Pipecat to cleanly hang up.

---

## How to run manually (test before relying on cron)

```bash
# Full interactive call (boots server + tunnel + places outbound call):
.venv/bin/python src/run_morning_call.py

# Just the fallback monologue (no server needed — good smoke-test of Twilio creds):
.venv/bin/python src/call_me.py

# Just the server (for webhook testing with ngrok/cloudflared separately):
cd src && uvicorn twilio_bot:app --host 0.0.0.0 --port 8080
```

Required `.env` keys: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`,
`TARGET_PHONE_NUMBER`, `NVIDIA_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`.

---

## Troubleshooting

- **"cloudflared did not report a public URL":** `cloudflared` not on PATH or not installed.
  Check `which cloudflared`; install with `brew install cloudflared`.
- **"public URL not reachable" after 90s:** Cloudflare edge congestion. Rare but happens.
  Re-run; it almost always works on the second attempt.
- **Call connects but silence / no greeting:** the WebSocket connected but `on_client_connected`
  didn't fire (Twilio connected event not received). Check that `await websocket.receive_text()`
  consumed the 'connected' frame before the 'start' frame.
- **TTS sounds garbled / sped up:** sample rate mismatch. Confirm `SR=8000` is passed to both
  `CartesiaTTSService(sample_rate=SR)` and `DeepgramSTTService(sample_rate=SR)`.
- **Call falls back to monologue every time:** usually the tunnel warmup failing. Increase the
  90s window or add a retry loop in `start_tunnel()`.
- **macOS Full Disk Access error when installing crontab:** Terminal / the shell running
  `crontab -` needs Full Disk Access in System Settings → Privacy & Security → Full Disk Access.
  This is required on macOS 15+ for `crontab` to write to `/private/var/at/tmp`.
