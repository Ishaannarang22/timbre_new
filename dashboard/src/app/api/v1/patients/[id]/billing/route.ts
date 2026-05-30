import type { NextRequest } from "next/server";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

// GET /api/v1/patients/:id/billing
// Used by the agent's lookup_patient_billing global tool when the patient asks
// "where is my bill?" or "how much do I owe?".
export async function GET(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/billing">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("billing")
    .select("*")
    .eq("patient_id", id)
    .order("created_at", { ascending: false });
  if (error) throw error;
  return ok(data);
}
