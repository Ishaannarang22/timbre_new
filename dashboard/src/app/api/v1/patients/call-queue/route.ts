import { withAgentGet, ok } from "@/lib/api";

// GET /api/v1/patients/call-queue
// Returns today's queued + in-progress calls with patient + newborn join.
// Used by the voice agent worker on Pipecat Cloud to fetch the call list.
export const GET = withAgentGet(async ({ db }) => {
  const { data, error } = await db
    .from("call")
    .select(
      `id, patient_id, direction, status, language, scheduled_at, started_at, current_node, flow_name,
       patient:patient_id ( id, name, preferred_name, language, phone, birth_date, birth_type, discharge_date, primary_provider, doula_assigned, notes ),
       newborns:newborn ( id, name, dob, sex, feeding_type )`,
    )
    .in("status", ["queued", "in_progress"])
    .order("scheduled_at", { ascending: true })
    .limit(50);
  if (error) throw error;
  return ok(data);
});
