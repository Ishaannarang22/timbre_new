import { Gauge } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { relativeTime } from "@/lib/format";

export const dynamic = "force-dynamic";

type EvalRow = {
  id: string;
  persona: string;
  flow_name: string;
  started_at: string;
  completed_at: string | null;
  overall_score: number | null;
  status: string;
  cekura_run_id: string | null;
};

type ResultRow = {
  id: string;
  eval_run_id: string;
  criterion: string;
  passed: boolean;
  score: number | null;
  notes: string | null;
};

const PERSONA_LABEL: Record<string, string> = {
  the_contradiction: "The Contradiction",
  cost_blocker: "The Cost-Blocker",
  proxy_responder: "The Proxy Responder",
  ambiguous_healer: "The Ambiguous Healer",
};

const CRITERION_LABEL: Record<string, string> = {
  node_transition_accuracy: "Node transition accuracy",
  context_strategy: "Context isolation",
  tool_call_latency_ms: "Tool latency (ms)",
  global_function_reliability: "Global function reliability",
  pii_redaction: "PII redaction",
  escalation_correctness: "Escalation correctness",
};

export default async function EvalsPage() {
  const db = supabaseAdmin();
  const [runsRes, resultsRes] = await Promise.all([
    db.from("eval_run").select("*").order("started_at", { ascending: false }).limit(30),
    db.from("eval_result").select("*").order("created_at", { ascending: false }).limit(500),
  ]);
  const runs = (runsRes.data ?? []) as EvalRow[];
  const results = (resultsRes.data ?? []) as ResultRow[];
  const resultsByRun = new Map<string, ResultRow[]>();
  for (const r of results) {
    const arr = resultsByRun.get(r.eval_run_id) ?? [];
    arr.push(r);
    resultsByRun.set(r.eval_run_id, arr);
  }
  return (
    <>
      <PageHeader
        title="Cekura evals"
        description="Persona-based self-evaluating loop. Wired to the Cekura MCP server."
        action={<Badge variant="outline">{runs.length} runs</Badge>}
      />
      <div className="space-y-3 p-6">
        {runs.length === 0 ? (
          <Empty
            title="No eval runs yet"
            description="Once the Cekura MCP server is connected and a persona is run, results appear here."
          />
        ) : (
          runs.map((run) => {
            const runResults = resultsByRun.get(run.id) ?? [];
            return (
              <Card key={run.id}>
                <CardHeader>
                  <div className="flex flex-wrap items-center gap-3">
                    <CardTitle className="flex items-center gap-2">
                      <Gauge className="size-4 text-primary" />
                      {PERSONA_LABEL[run.persona] ?? run.persona}
                    </CardTitle>
                    <Badge variant="muted">{run.flow_name}</Badge>
                    <Badge variant={run.status === "completed" ? "success" : "outline"}>{run.status}</Badge>
                    {run.overall_score != null ? (
                      <Badge variant={run.overall_score >= 80 ? "success" : run.overall_score >= 50 ? "warning" : "destructive"}>
                        {run.overall_score.toFixed(0)}
                      </Badge>
                    ) : null}
                    <span className="ml-auto text-xs text-muted-foreground">
                      started {relativeTime(run.started_at)}
                    </span>
                  </div>
                  {run.cekura_run_id ? (
                    <CardDescription>cekura run: {run.cekura_run_id}</CardDescription>
                  ) : null}
                </CardHeader>
                <CardContent>
                  {runResults.length === 0 ? (
                    <div className="text-xs text-muted-foreground">No criterion results yet.</div>
                  ) : (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {runResults.map((r) => (
                        <div
                          key={r.id}
                          className="flex items-center justify-between rounded-md border border-[hsl(var(--border))] p-3 text-xs"
                        >
                          <span className="font-medium">
                            {CRITERION_LABEL[r.criterion] ?? r.criterion}
                          </span>
                          <Badge variant={r.passed ? "success" : "destructive"}>
                            {r.score != null ? r.score.toFixed(0) : r.passed ? "pass" : "fail"}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </>
  );
}
