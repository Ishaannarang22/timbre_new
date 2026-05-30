import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.string().uuid(),
  instrument: z.enum(["phq2", "phq9", "epds"]),
  score: z.number().int().min(0).max(30),
  responses: z.record(z.string(), z.unknown()).optional(),
  elevated: z.boolean().optional(),
  suicidal_ideation: z.boolean().optional(),
});

// POST /api/v1/patients/:id/phq — depression / anxiety screen score.
// If suicidal_ideation is true the agent should ALSO POST to /escalations with category=crisis.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/phq">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const elevated =
    parsed.data.elevated ??
    (parsed.data.instrument === "phq2"
      ? parsed.data.score >= 3
      : parsed.data.instrument === "phq9"
        ? parsed.data.score >= 10
        : parsed.data.score >= 10);
  const { data, error } = await db
    .from("phq_score")
    .insert({ patient_id: id, ...parsed.data, elevated })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
