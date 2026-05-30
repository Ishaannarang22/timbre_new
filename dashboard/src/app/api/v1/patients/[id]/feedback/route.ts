import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.string().uuid().optional(),
  category: z
    .enum(["clinical", "billing", "scheduling", "facilities", "staff", "communication", "other"])
    .default("other"),
  note: z.string().min(1).max(1000),
  sentiment: z.enum(["positive", "neutral", "negative"]).default("neutral"),
  quote_friendly: z.boolean().default(true),
});

// POST /api/v1/patients/:id/feedback — categorized open-ended feedback the agent captured.
// Surfaces on the dashboard "Patient Voices" page.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/feedback">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("feedback")
    .insert({ patient_id: id, ...parsed.data })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
