import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  call_id: z.string().uuid(),
  prescription_id: z.string().uuid().optional(),
  medication: z.string().optional(),
  picked_up: z.boolean().optional(),
  taking_as_prescribed: z.boolean().optional(),
  barrier: z
    .enum(["cost", "transport", "side_effects", "forgot", "no_pharmacy", "concerns", "other", "none"])
    .optional(),
  barrier_notes: z.string().optional(),
});

// POST /api/v1/patients/:id/adherence — med adherence + barrier tag from the medication node.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/adherence">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("adherence")
    .insert({ patient_id: id, ...parsed.data })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
