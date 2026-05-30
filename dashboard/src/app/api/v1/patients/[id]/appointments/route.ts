import type { NextRequest } from "next/server";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

// GET /api/v1/patients/:id/appointments
// Past + upcoming appointments for the agent's lookup_appointment_history tool.
export async function GET(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]/appointments">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("appointment")
    .select("*")
    .eq("patient_id", id)
    .order("scheduled_at", { ascending: true });
  if (error) throw error;
  return ok(data);
}
