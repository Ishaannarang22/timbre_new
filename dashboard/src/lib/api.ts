import { NextResponse, type NextRequest } from "next/server";
import { z, type ZodType } from "zod";
import { requireAgentToken } from "./auth";
import { supabaseAdmin } from "./supabase/server";

// Wraps a route handler so every voice-agent call gets:
//   1. shared bearer token auth (DASHBOARD_API_TOKEN),
//   2. zod-validated body,
//   3. Supabase service-role client passed in.
export function withAgent<T>(
  schema: ZodType<T>,
  handler: (args: { req: NextRequest; body: T; db: ReturnType<typeof supabaseAdmin> }) => Promise<Response>,
) {
  return async (req: NextRequest) => {
    const authError = requireAgentToken(req);
    if (authError) return authError;
    let raw: unknown;
    try {
      raw = await req.json();
    } catch {
      return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
    }
    const parsed = schema.safeParse(raw);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "validation failed", issues: parsed.error.issues },
        { status: 422 },
      );
    }
    try {
      return await handler({ req, body: parsed.data, db: supabaseAdmin() });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[api] route handler failed:", message);
      return NextResponse.json({ error: "internal error", detail: message }, { status: 500 });
    }
  };
}

// Read-only variant — for concierge lookups the agent makes during a call.
export function withAgentGet(
  handler: (args: { req: NextRequest; db: ReturnType<typeof supabaseAdmin> }) => Promise<Response>,
) {
  return async (req: NextRequest) => {
    const authError = requireAgentToken(req);
    if (authError) return authError;
    try {
      return await handler({ req, db: supabaseAdmin() });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[api] route handler failed:", message);
      return NextResponse.json({ error: "internal error", detail: message }, { status: 500 });
    }
  };
}

// Standard JSON ok response.
export const ok = (data: unknown, init?: ResponseInit) =>
  NextResponse.json({ ok: true, data }, init);

export { z };
