import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.guid(),
  bleeding: z.enum(["none", "spotting", "light", "moderate", "heavy", "concerning"]).optional(),
  pain_score: z.number().int().min(0).max(10).optional(),
  incision_status: z.string().optional(),
  mobility_status: z.string().optional(),
  urination_status: z.string().optional(),
  emotional_state: z.string().optional(),
  notes: z.string().optional(),
});

// POST /api/v1/patients/:id/recovery — mom's recovery answers from the postpartum flow.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/recovery">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("recovery_answer")
    .insert({ patient_id: id, ...parsed.data })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
