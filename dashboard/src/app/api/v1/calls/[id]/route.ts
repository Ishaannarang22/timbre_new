import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const PatchBody = z.object({
  status: z
    .enum(["queued", "in_progress", "completed", "escalated", "abandoned", "failed"])
    .optional(),
  current_node: z.string().optional(),
  // Accept timezone OFFSET form (e.g. "...+00:00"), not just the "Z" suffix —
  // the agent sends datetime.now(timezone.utc).isoformat(), which uses +00:00.
  // Plain .datetime() rejects offsets in zod 4 → every "mark completed" PATCH 422'd.
  ended_at: z.string().datetime({ offset: true }).optional(),
  transcript_redacted: z.string().optional(),
  summary: z.string().optional(),
});

// PATCH /api/v1/calls/:id — incremental updates from the Pipecat worker:
//   current_node on every NodeConfig transition, status on completion, transcript at end.
export async function PATCH(req: NextRequest, ctx: RouteContext<"/api/v1/calls/[id]">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = PatchBody.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("call")
    .update(parsed.data)
    .eq("id", id)
    .select()
    .single();
  if (error) throw error;
  return ok(data);
}

export async function GET(req: NextRequest, ctx: RouteContext<"/api/v1/calls/[id]">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const db = supabaseAdmin();
  const { data, error } = await db.from("call").select("*").eq("id", id).single();
  if (error) throw error;
  return ok(data);
}
