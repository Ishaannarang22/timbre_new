import { NextResponse, type NextRequest } from "next/server";
import { createHmac, timingSafeEqual } from "node:crypto";
import { supabaseAdmin } from "@/lib/supabase/server";

const CEKURA_API_BASE = "https://api.cekura.ai";

type PersonaSlug =
  | "the_contradiction"
  | "cost_blocker"
  | "proxy_responder"
  | "ambiguous_healer";

type CriterionSlug =
  | "node_transition_accuracy"
  | "context_strategy"
  | "tool_call_latency_ms"
  | "global_function_reliability"
  | "pii_redaction"
  | "escalation_correctness";

const PERSONA_TAG_TO_SLUG: Record<string, PersonaSlug> = {
  "persona:the_contradiction": "the_contradiction",
  "persona:cost_blocker": "cost_blocker",
  "persona:proxy_responder": "proxy_responder",
  "persona:ambiguous_healer": "ambiguous_healer",
};

const PERSONA_SLUGS: PersonaSlug[] = [
  "the_contradiction",
  "cost_blocker",
  "proxy_responder",
  "ambiguous_healer",
];

const PRD_CRITERIA: ReadonlySet<CriterionSlug> = new Set([
  "node_transition_accuracy",
  "context_strategy",
  "tool_call_latency_ms",
  "global_function_reliability",
  "pii_redaction",
  "escalation_correctness",
]);

function verifySignature(req: NextRequest, rawBody: string): boolean {
  const secret = process.env.CEKURA_WEBHOOK_SECRET;
  if (!secret) {
    console.warn("[cekura/webhook] CEKURA_WEBHOOK_SECRET unset — skipping signature check");
    return true;
  }
  const sig =
    req.headers.get("x-cekura-signature") ??
    req.headers.get("x-webhook-signature") ??
    req.headers.get("x-hub-signature-256");
  if (!sig) return false;
  const cleaned = sig.startsWith("sha256=") ? sig.slice(7) : sig;
  const expected = createHmac("sha256", secret).update(rawBody).digest("hex");
  try {
    return timingSafeEqual(Buffer.from(cleaned, "hex"), Buffer.from(expected, "hex"));
  } catch {
    return false;
  }
}

function resolvePersona(scenarioName: string | undefined, tags: string[] = []): PersonaSlug | null {
  for (const tag of tags) {
    const slug = PERSONA_TAG_TO_SLUG[tag];
    if (slug) return slug;
  }
  const lower = (scenarioName ?? "").toLowerCase();
  for (const slug of PERSONA_SLUGS) {
    if (lower.includes(slug)) return slug;
  }
  return null;
}

function asCriterion(name: string | undefined): CriterionSlug | null {
  if (!name) return null;
  const normalized = name.toLowerCase().replace(/[^a-z0-9_]/g, "_");
  return PRD_CRITERIA.has(normalized as CriterionSlug) ? (normalized as CriterionSlug) : null;
}

async function fetchCekuraResult(resultId: number | string, apiKey: string) {
  const url = `${CEKURA_API_BASE}/test_framework/v1/results/${resultId}/`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`cekura results_retrieve ${resultId} → ${res.status} ${await res.text()}`);
  }
  return res.json();
}

async function fetchScenarioTags(scenarioId: number, apiKey: string): Promise<string[]> {
  try {
    const res = await fetch(`${CEKURA_API_BASE}/test_framework/v1/scenarios/${scenarioId}/`, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return [];
    const scenario = await res.json();
    return Array.isArray(scenario.tags) ? scenario.tags : [];
  } catch {
    return [];
  }
}

export async function POST(req: NextRequest) {
  const rawBody = await req.text();

  if (!verifySignature(req, rawBody)) {
    return NextResponse.json({ error: "invalid signature" }, { status: 401 });
  }

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }

  const event =
    (payload.event as string | undefined) ??
    (payload.event_type as string | undefined) ??
    "unknown";

  const resultId =
    (payload.result_id as number | string | undefined) ??
    (payload.id as number | string | undefined) ??
    ((payload.data as Record<string, unknown> | undefined)?.result_id as number | string | undefined) ??
    ((payload.data as Record<string, unknown> | undefined)?.id as number | string | undefined);

  if (!resultId) {
    console.warn("[cekura/webhook] no result_id in payload", { event, keys: Object.keys(payload) });
    return NextResponse.json({ ok: true, ignored: true, reason: "no result_id" });
  }

  const apiKey = process.env.CEKURA_API_KEY;
  if (!apiKey) {
    console.error("[cekura/webhook] CEKURA_API_KEY env not set");
    return NextResponse.json({ error: "server not configured" }, { status: 500 });
  }

  let result: Record<string, unknown>;
  try {
    result = await fetchCekuraResult(resultId, apiKey);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    console.error("[cekura/webhook] fetch result failed", detail);
    return NextResponse.json({ error: "cekura fetch failed", detail }, { status: 502 });
  }

  const runsObj = (result.runs as Record<string, Record<string, unknown>> | undefined) ?? {};
  const db = supabaseAdmin();
  const created: Array<Record<string, unknown>> = [];

  for (const [runIdStr, run] of Object.entries(runsObj)) {
    const scenarioId = run.scenario as number | undefined;
    const scenarioName = run.scenario_name as string | undefined;

    let tags: string[] = [];
    if (scenarioId) tags = await fetchScenarioTags(scenarioId, apiKey);
    const persona = resolvePersona(scenarioName, tags);
    if (!persona) {
      console.warn("[cekura/webhook] could not map scenario to persona", {
        scenarioId,
        scenarioName,
        tags,
      });
      continue;
    }

    const runStatus = run.status as string | undefined;
    const success = Boolean(run.success);
    const status = runStatus === "completed" ? (success ? "completed" : "failed") : "errored";

    const overallScore =
      typeof result.success_rate === "number" ? Number(result.success_rate) * 100 : null;

    const transcriptJson = run.transcript_json ?? run.transcript ?? null;
    const transcriptText =
      typeof transcriptJson === "string" ? transcriptJson : JSON.stringify(transcriptJson);

    const { data: evalRunRow, error: insertErr } = await db
      .from("eval_run")
      .insert({
        persona,
        flow_name: "postpartum_v1",
        status,
        cekura_run_id: runIdStr,
        completed_at: (result.updated_at as string | undefined) ?? new Date().toISOString(),
        overall_score: overallScore,
        transcript: transcriptText && transcriptText !== "null" ? transcriptText : null,
        notes: `Cekura result ${resultId}, run ${runIdStr}, scenario "${scenarioName ?? ""}"`,
      })
      .select()
      .single();

    if (insertErr || !evalRunRow) {
      console.error("[cekura/webhook] eval_run insert failed", insertErr);
      continue;
    }

    const evaluation = (run.evaluation as Record<string, unknown> | undefined) ?? {};
    const metrics = (evaluation.metrics as Array<Record<string, unknown>> | undefined) ?? [];

    const evalResults: Array<{
      eval_run_id: string;
      criterion: CriterionSlug;
      passed: boolean;
      score: number | null;
      details: Record<string, unknown>;
    }> = [];

    for (const metric of metrics) {
      const criterion = asCriterion(metric.name as string | undefined);
      if (!criterion) continue;
      const verdict = metric.result ?? metric.value ?? metric.passed;
      const passed =
        verdict === true ||
        verdict === "pass" ||
        verdict === "PASS" ||
        (typeof verdict === "number" && criterion === "tool_call_latency_ms" && verdict <= 1500);
      const score =
        typeof metric.score === "number"
          ? metric.score
          : typeof metric.value === "number"
            ? metric.value
            : null;
      evalResults.push({
        eval_run_id: evalRunRow.id as string,
        criterion,
        passed: Boolean(passed),
        score,
        details: metric,
      });
    }

    if (evalResults.length > 0) {
      const { error: resultsErr } = await db.from("eval_result").insert(evalResults);
      if (resultsErr) {
        console.error("[cekura/webhook] eval_result insert failed", resultsErr);
      }
    }

    created.push({
      cekura_run_id: runIdStr,
      eval_run_id: evalRunRow.id,
      persona,
      metrics_recorded: evalResults.length,
    });
  }

  return NextResponse.json({ ok: true, event, result_id: resultId, created });
}

export async function GET() {
  return NextResponse.json({
    ok: true,
    message: "cekura webhook receiver — POST events here",
  });
}
