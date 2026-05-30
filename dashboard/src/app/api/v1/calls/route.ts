import { withAgent, z, ok } from "@/lib/api";

const Body = z.object({
  patient_id: z.guid(),
  call_sid: z.string().optional(),
  direction: z.enum(["inbound", "outbound"]).default("outbound"),
  language: z.enum(["en", "es"]).default("en"),
  flow_name: z.string().default("postpartum_v1"),
  // Optional: if the agent is starting a previously-queued call instead of creating new.
  existing_call_id: z.guid().optional(),
});

// POST /api/v1/calls — Pipecat worker creates / starts a call record at /ws connect time.
export const POST = withAgent(Body, async ({ body, db }) => {
  const now = new Date().toISOString();
  if (body.existing_call_id) {
    const { data, error } = await db
      .from("call")
      .update({
        status: "in_progress",
        started_at: now,
        call_sid: body.call_sid ?? null,
      })
      .eq("id", body.existing_call_id)
      .select()
      .single();
    if (error) throw error;
    return ok(data);
  }
  const { existing_call_id: _unused, ...insertable } = body;
  void _unused;
  const { data, error } = await db
    .from("call")
    .insert({ ...insertable, status: "in_progress", started_at: now })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
});
