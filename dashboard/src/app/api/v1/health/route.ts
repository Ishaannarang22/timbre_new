// GET /api/v1/health — no auth, returns ok. Useful for Pipecat startup sanity check.
export async function GET() {
  return Response.json({ ok: true, service: "timbre-dashboard", time: new Date().toISOString() });
}
