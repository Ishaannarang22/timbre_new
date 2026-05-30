import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { callStatusTone, relativeTime } from "@/lib/format";
import { daysSince } from "@/lib/utils";

export const dynamic = "force-dynamic";

type QueueRow = {
  id: string;
  status: "queued" | "in_progress" | "completed" | "escalated" | "abandoned" | "failed";
  language: "en" | "es";
  scheduled_at: string | null;
  current_node: string | null;
  patient: {
    id: string;
    name: string;
    preferred_name: string | null;
    language: string;
    birth_date: string | null;
    birth_type: string | null;
    primary_provider: string | null;
    doula_assigned: string | null;
  } | null;
};

export default async function QueuePage() {
  const db = supabaseAdmin();
  const { data, error } = await db
    .from("call")
    .select(
      `id, status, language, scheduled_at, current_node,
       patient:patient_id ( id, name, preferred_name, language, birth_date, birth_type, primary_provider, doula_assigned )`,
    )
    .in("status", ["queued", "in_progress"])
    .order("scheduled_at", { ascending: true });

  const rows: QueueRow[] = ((data ?? []) as unknown as QueueRow[]) ?? [];

  return (
    <>
      <PageHeader
        title="Today's call queue"
        description="Scheduled postpartum check-ins for today. The voice agent works through this list in order."
        action={
          <Badge variant="outline" className="px-3 py-1">
            {rows.length} {rows.length === 1 ? "call" : "calls"} queued
          </Badge>
        }
      />
      <div className="p-6">
        {error ? (
          <Empty title="Couldn't load queue" description={error.message} />
        ) : rows.length === 0 ? (
          <Empty
            title="No calls queued"
            description="When patients are scheduled, they'll appear here in order."
          />
        ) : (
          <div className="grid gap-3">
            {rows.map((row) => {
              const dpp = daysSince(row.patient?.birth_date);
              return (
                <Link key={row.id} href={`/patient/${row.patient?.id}`} className="block group">
                  <Card className="transition-colors group-hover:bg-accent/40">
                    <CardContent className="flex items-center justify-between gap-4 p-4">
                      <div className="flex flex-col">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold">
                            {row.patient?.preferred_name ?? row.patient?.name ?? "Unknown patient"}
                          </span>
                          <Badge variant="muted" className="uppercase">
                            {row.language === "es" ? "ES" : "EN"}
                          </Badge>
                          <Badge variant={callStatusTone[row.status]}>{row.status.replace("_", " ")}</Badge>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                          {row.patient?.birth_type ? (
                            <span>{row.patient.birth_type.replace("_", " ")}</span>
                          ) : null}
                          {dpp != null ? <span>day {dpp} postpartum</span> : null}
                          {row.patient?.primary_provider ? (
                            <span>{row.patient.primary_provider}</span>
                          ) : null}
                          {row.patient?.doula_assigned ? (
                            <span>doula: {row.patient.doula_assigned.replace(/ \(.*\)$/, "")}</span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1 text-right">
                        <span className="text-xs font-medium text-foreground">
                          {row.scheduled_at ? relativeTime(row.scheduled_at) : "—"}
                        </span>
                        {row.current_node ? (
                          <span className="text-xs text-muted-foreground">
                            at <code className="font-mono">{row.current_node}</code>
                          </span>
                        ) : null}
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
