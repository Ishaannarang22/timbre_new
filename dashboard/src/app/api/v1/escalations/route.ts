import { withAgent, z, ok } from "@/lib/api";

const Body = z.object({
  patient_id: z.guid(),
  call_id: z.guid().optional(),
  severity: z.enum(["urgent", "warning", "info"]).default("urgent"),
  category: z.enum(["maternal", "pediatric", "crisis", "concierge"]).default("maternal"),
  trigger_phrase: z.string().optional(),
  trigger_text: z.string().min(1).max(2000),
  transcript_excerpt: z.string().optional(),
});

// POST /api/v1/escalations — the agent's escalate_to_nurse / escalate_pediatric / escalate_crisis
// global functions land here. Supabase Realtime broadcasts INSERTs to the dashboard /live page.
export const POST = withAgent(Body, async ({ body, db }) => {
  const { data, error } = await db.from("escalation").insert(body).select().single();
  if (error) throw error;
  return ok(data, { status: 201 });
});
