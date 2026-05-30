import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.string().uuid(),
  rating: z.number().int().min(1).max(5),
  qualitative_summary: z.string().optional(),
});

// POST /api/v1/patients/:id/csat — 1-5 rating + 2-sentence qualitative summary at call end.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/csat">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("csat")
    .insert({ patient_id: id, ...parsed.data })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
