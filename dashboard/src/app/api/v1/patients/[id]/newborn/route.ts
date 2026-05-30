import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.string().uuid(),
  newborn_id: z.string().uuid(),
  feeding_count_24h: z.number().int().min(0).max(30).optional(),
  wet_diapers_24h: z.number().int().min(0).max(30).optional(),
  dirty_diapers_24h: z.number().int().min(0).max(30).optional(),
  jaundice_observed: z.boolean().optional(),
  fever: z.boolean().optional(),
  fever_temp_f: z.number().min(90).max(110).optional(),
  sleep_pattern: z.string().optional(),
  weight_check_oz: z.number().int().optional(),
  notes: z.string().optional(),
});

// POST /api/v1/patients/:id/newborn — newborn answers for this call.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/newborn">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  await ctx.params; // id from path is metadata only — body carries newborn_id
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db.from("newborn_answer").insert(parsed.data).select().single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
