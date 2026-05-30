import { withAgent, z, ok } from "@/lib/api";

const Body = z.object({
  persona: z.enum(["the_contradiction", "cost_blocker", "proxy_responder", "ambiguous_healer"]),
  flow_name: z.string().default("postpartum_v1"),
  cekura_run_id: z.string().optional(),
  notes: z.string().optional(),
});

// POST /api/v1/evals — register the start of a Cekura persona run.
export const POST = withAgent(Body, async ({ body, db }) => {
  const { data, error } = await db
    .from("eval_run")
    .insert({ ...body, status: "running" })
    .select()
    .single();
  if (error) throw error;
  return ok(data, { status: 201 });
});
