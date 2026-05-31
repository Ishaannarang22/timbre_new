import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";

// POST /api/demo/call — public "try it now" demo.
// Places a REAL outbound Twilio call to the visitor's number and bridges it to the
// timbre voice agent on Pipecat Cloud, posing as the chosen patient (real profile,
// real check-in flow, writes to that patient's record). Mirrors deploy/dialout_test.py
// but server-side, triggered from the homepage.
//
// SECURITY NOTE: per product decision this endpoint is intentionally OPEN (no auth /
// no rate limit) so anyone can try the demo. The patient is restricted to the curated
// allowlist below so it can't be used to call "as" an arbitrary record, but the TO
// number is caller-supplied — i.e. this can dial any phone. Revisit before real launch.

// Only these seeded patients can be demoed. value = the greeting name + language the
// agent should use (passed straight through as <Parameter>s, like the dialout test).
const DEMO_PATIENTS: Record<string, { name: string; language: "en" | "es" }> = {
  "33333333-3333-3333-3333-333333333333": { name: "Aisha", language: "en" },
  "22222222-2222-2222-2222-222222222222": { name: "Jess", language: "en" },
  "11111111-1111-1111-1111-111111111111": { name: "María", language: "es" },
};

const Body = z.object({
  patient_id: z.string(),
  phone: z.string().min(7).max(20),
});

/** Normalize a user-typed number to E.164. US-default: bare 10 digits → +1XXXXXXXXXX.
 *  Returns null if it can't be made into a plausible E.164 number. */
function toE164(raw: string): string | null {
  const trimmed = raw.trim();
  const hadPlus = trimmed.startsWith("+");
  const digits = trimmed.replace(/\D/g, "");
  if (hadPlus) {
    return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
  }
  if (digits.length === 10) return `+1${digits}`; // US/CA without country code
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  return null;
}

export async function POST(req: NextRequest) {
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success) {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }
  const { patient_id, phone } = parsed.data;

  const demo = DEMO_PATIENTS[patient_id];
  if (!demo) {
    return NextResponse.json({ error: "Unknown demo patient." }, { status: 400 });
  }

  const to = toE164(phone);
  if (!to) {
    return NextResponse.json(
      { error: "Enter a valid phone number, e.g. +1 555 123 4567." },
      { status: 400 },
    );
  }

  const sid = process.env.TWILIO_ACCOUNT_SID;
  const token = process.env.TWILIO_AUTH_TOKEN;
  const from = process.env.TWILIO_PHONE_NUMBER;
  if (!sid || !token || !from) {
    return NextResponse.json(
      { error: "Calling is not configured on this server." },
      { status: 503 },
    );
  }

  const pipecatHost = process.env.PIPECAT_SERVICE_HOST || "timbre.linear-sturgeon-tan-585";
  const wsUrl = process.env.PIPECAT_WS_URL || "wss://api.pipecat.daily.co/ws/twilio";

  // Inline TwiML: Twilio fetches nothing — it streams the call straight to the agent.
  // patient_id + name + language ride along as <Parameter>s (Twilio echoes them in the
  // start frame's customParameters, which bot.py reads). Values here are server-controlled
  // (UUID + allowlisted name + enum), so no XML-injection surface.
  const twiml =
    '<?xml version="1.0" encoding="UTF-8"?>' +
    "<Response><Connect>" +
    `<Stream url="${wsUrl}">` +
    `<Parameter name="_pipecatCloudServiceHost" value="${pipecatHost}"/>` +
    '<Parameter name="direction" value="outbound"/>' +
    `<Parameter name="patient_id" value="${patient_id}"/>` +
    `<Parameter name="preferred_name" value="${demo.name}"/>` +
    `<Parameter name="language" value="${demo.language}"/>` +
    "</Stream></Connect></Response>";

  const form = new URLSearchParams({ To: to, From: from, Twiml: twiml });
  const auth = Buffer.from(`${sid}:${token}`).toString("base64");

  const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Calls.json`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${auth}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: form,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    return NextResponse.json(
      { error: "Couldn't place the call. Please try again.", detail: detail.slice(0, 300) },
      { status: 502 },
    );
  }

  const data = (await res.json().catch(() => ({}))) as { sid?: string };
  return NextResponse.json({
    ok: true,
    call_sid: data.sid ?? null,
    name: demo.name,
    from,
  });
}
