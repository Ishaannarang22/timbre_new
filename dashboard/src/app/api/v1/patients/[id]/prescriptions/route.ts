import type { NextRequest } from "next/server";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

// GET /api/v1/patients/:id/prescriptions
// Current + recent prescriptions for the agent's lookup_prescription_status tool.
export async function GET(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/prescriptions">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("prescription")
    .select("*")
    .eq("patient_id", id)
    .order("prescribed_date", { ascending: false });
  if (error) throw error;
  return ok(data);
}
