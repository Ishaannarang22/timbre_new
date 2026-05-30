import type { NextRequest } from "next/server";
import { requireAgentToken } from "@/lib/auth";
import { supabaseAdmin } from "@/lib/supabase/server";
import { ok } from "@/lib/api";

// GET /api/v1/patients/:id
// Full concierge profile: patient + newborn + billing + appointments + prescriptions + recent feedback.
// Called by the voice agent when the patient asks a profile question mid-call.
export async function GET(req: NextRequest, ctx: RouteContext<"/api/v1/patients/[id]">) {
  const authError = requireAgentToken(req);
  if (authError) return authError;
  const { id } = await ctx.params;
  const db = supabaseAdmin();
  const [patient, newborns, billing, appointments, prescriptions] = await Promise.all([
    db.from("patient").select("*").eq("id", id).single(),
    db.from("newborn").select("*").eq("patient_id", id),
    db.from("billing").select("*").eq("patient_id", id).order("created_at", { ascending: false }),
    db
      .from("appointment")
      .select("*")
      .eq("patient_id", id)
      .order("scheduled_at", { ascending: true }),
    db
      .from("prescription")
      .select("*")
      .eq("patient_id", id)
      .order("prescribed_date", { ascending: false }),
  ]);
  if (patient.error) throw patient.error;
  return ok({
    patient: patient.data,
    newborns: newborns.data ?? [],
    billing: billing.data ?? [],
    appointments: appointments.data ?? [],
    prescriptions: prescriptions.data ?? [],
  });
}
