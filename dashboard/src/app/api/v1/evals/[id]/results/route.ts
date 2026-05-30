import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

const Body = z.object({
  criterion: z.enum([
    "node_transition_accuracy",
    "context_strategy",
    "tool_call_latency_ms",
    "global_function_reliability",
    "pii_redaction",
    "escalation_correctness",
  ]),
  passed: z.boolean(),
  score: z.number().optional(),
  details: z.record(z.string(), z.unknown()).optional(),
  notes: z.string().optional(),
});

// POST /api/v1/evals/:id/results — Cekura posts per-criterion results.
export async function POST(req: NextRequest, ctx: RouteContext<"/api/v1/evals/[id]/results">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const parsed = Body.safeParse(await req.json().catch(() => ({})));
  if (!parsed.success)
    return NextResponse.json({ error: "validation failed", issues: parsed.error.issues }, { status: 422 });
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("eval_result")
    .insert({ eval_run_id: id, ...parsed.data })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
}
