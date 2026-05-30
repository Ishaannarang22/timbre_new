import { NextResponse, type NextRequest } from "next/server";

// Shared bearer token between the Pipecat voice agent and the dashboard API.
// Set DASHBOARD_API_TOKEN in BOTH:
//   - the timbre_new repo's .env (the voice agent uses it as the Authorization header)
//   - the Vercel project env (this app reads it here)
// Demo-grade only — for real PHI, use mTLS or a per-agent JWT with scopes.
export function requireAgentToken(req: NextRequest): NextResponse | null {
  const expected = process.env.DASHBOARD_API_TOKEN;
  if (!expected) {
    return NextResponse.json(
      { error: "DASHBOARD_API_TOKEN is not configured on the server" },
      { status: 500 }
    );
  }
  const header = req.headers.get("authorization") ?? "";
  const presented = header.startsWith("Bearer ") ? header.slice(7) : header;
  if (presented !== expected) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return null; // ok
}
