import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const PatchBody = z.object({
  status: z.string().optional(),
  completed_at: z.string().datetime().optional(),
  overall_score: z.number().min(0).max(100).optional(),
  transcript: z.string().optional(),
  notes: z.string().optional(),
});

// PATCH /api/v1/evals/:id — Cekura updates the run when it finishes.
export async function PATCH(req: NextRequest, ctx: RouteContext<"/api/v1/evals/[id]">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = PatchBody.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("eval_run")
    .update(parsed.data)
    .eq("id", id)
    .select()
    .single();
  if (error) throw error;
  return ok(data);
}
