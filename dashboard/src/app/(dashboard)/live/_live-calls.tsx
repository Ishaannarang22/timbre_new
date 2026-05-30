"use client";

import { useEffect, useMemo, useState } from "react";
import type { RealtimePostgresChangesPayload } from "@supabase/supabase-js";
import Link from "next/link";
import { Activity, AlertTriangle, CircleDot } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { supabaseBrowser } from "@/lib/supabase/browser";
import type { Call, Escalation, Patient } from "@/lib/types";
import { callStatusTone, relativeTime, severityTone } from "@/lib/format";
import { daysSince } from "@/lib/utils";

type PatientLite = Pick<
  Patient,
  "id" | "name" | "preferred_name" | "language" | "birth_date" | "birth_type" | "primary_provider" | "doula_assigned"
>;

export function LiveCalls({
  initialCalls,
  initialEscalations,
  patients,
}: {
  initialCalls: Call[];
  initialEscalations: Escalation[];
  patients: PatientLite[];
}) {
  const [calls, setCalls] = useState<Call[]>(initialCalls);
  const [escalations, setEscalations] = useState<Escalation[]>(initialEscalations);
  const patientById = useMemo(() => {
    const m = new Map<string, PatientLite>();
    for (const p of patients) m.set(p.id, p);
    return m;
  }, [patients]);

  useEffect(() => {
    const sb = supabaseBrowser();
    const channel = sb
      .channel("live-room")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "call" },
        (payload: RealtimePostgresChangesPayload<Call>) => {
          const next = payload.new as Call | null;
          const prev = payload.old as Call | null;
          const row = next ?? prev;
          if (!row) return;
          setCalls((curr) => {
            const without = curr.filter((c) => c.id !== row.id);
            if (payload.eventType === "DELETE") return without;
            if (next && next.status !== "in_progress" && next.status !== "queued") return without;
            const merged = next ? [next, ...without] : without;
            return merged.sort((a, b) =>
              (b.started_at ?? b.scheduled_at ?? "").localeCompare(a.started_at ?? a.scheduled_at ?? ""),
            );
          });
        },
      )
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "escalation" },
        (payload: RealtimePostgresChangesPayload<Escalation>) => {
          const row = payload.new as Escalation;
          setEscalations((prev) => [row, ...prev].slice(0, 50));
        },
      )
      .subscribe();
    return () => {
      sb.removeChannel(channel);
    };
  }, []);

  return (
    <div className="grid gap-6 p-6 lg:grid-cols-3">
      <section className="lg:col-span-2">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
          <Activity className="size-4 text-success" />
          Active calls
          <Badge variant="muted">{calls.length}</Badge>
        </div>
        {calls.length === 0 ? (
          <Empty title="No active calls" description="When a call goes in-progress it'll appear here live." />
        ) : (
          <div className="grid gap-3">
            {calls.map((c) => {
              const p = patientById.get(c.patient_id);
              const dpp = daysSince(p?.birth_date);
              return (
                <Link key={c.id} href={p ? `/patient/${p.id}` : "#"} className="block group">
                  <Card className="transition-colors group-hover:bg-accent/40">
                    <CardContent className="flex items-center justify-between gap-4 p-4">
                      <div className="flex flex-col">
                        <div className="flex items-center gap-2">
                          {c.status === "in_progress" ? (
                            <CircleDot className="size-4 text-success animate-pulse" />
                          ) : null}
                          <span className="text-sm font-semibold">
                            {p?.preferred_name ?? p?.name ?? c.patient_id.slice(0, 8)}
                          </span>
                          <Badge variant="muted" className="uppercase">
                            {c.language}
                          </Badge>
                          <Badge variant={callStatusTone[c.status]}>{c.status.replace("_", " ")}</Badge>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                          {p?.birth_type ? <span>{p.birth_type.replace("_", " ")}</span> : null}
                          {dpp != null ? <span>day {dpp} postpartum</span> : null}
                          {c.current_node ? (
                            <span>
                              at <code className="font-mono text-[11px]">{c.current_node}</code>
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {relativeTime(c.started_at ?? c.scheduled_at ?? c.created_at)}
                      </span>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        )}
      </section>
      <section>
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
          <AlertTriangle className="size-4 text-destructive" />
          New escalations
          <Badge variant="muted">{escalations.length}</Badge>
        </div>
        {escalations.length === 0 ? (
          <Empty title="No escalations" description="Red-alert items will pop in here in real time." />
        ) : (
          <div className="grid gap-2">
            {escalations.map((e) => {
              const p = patientById.get(e.patient_id);
              return (
                <Card key={e.id} className="border-l-4 border-l-[hsl(var(--destructive))]/80">
                  <CardContent className="space-y-2 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={severityTone[e.severity]} className="uppercase text-[10px]">
                          {e.severity}
                        </Badge>
                        <Badge variant="muted" className="uppercase text-[10px]">
                          {e.category}
                        </Badge>
                      </div>
                      <span className="text-[11px] text-muted-foreground">
                        {relativeTime(e.created_at)}
                      </span>
                    </div>
                    <div className="text-sm font-medium">
                      {p?.preferred_name ?? p?.name ?? e.patient_id.slice(0, 8)}
                    </div>
                    <div className="text-xs text-muted-foreground line-clamp-3">{e.trigger_text}</div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
