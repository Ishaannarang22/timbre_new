import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { relativeTime, severityTone } from "@/lib/format";

export const dynamic = "force-dynamic";

type Row = {
  id: string;
  patient_id: string;
  severity: "urgent" | "warning" | "info";
  category: "maternal" | "pediatric" | "crisis" | "concierge";
  trigger_phrase: string | null;
  trigger_text: string | null;
  status: "new" | "acknowledged" | "resolved" | "dismissed";
  created_at: string;
  patient: { id: string; name: string; preferred_name: string | null } | null;
};

export default async function Escalations() {
  const db = supabaseAdmin();
  const { data } = await db
    .from("escalation")
    .select(
      `id, patient_id, severity, category, trigger_phrase, trigger_text, status, created_at,
       patient:patient_id ( id, name, preferred_name )`,
    )
    .order("created_at", { ascending: false })
    .limit(100);
  const rows: Row[] = ((data ?? []) as unknown as Row[]) ?? [];
  const groups = {
    new: rows.filter((r) => r.status === "new"),
    acknowledged: rows.filter((r) => r.status === "acknowledged"),
    resolved: rows.filter((r) => r.status === "resolved" || r.status === "dismissed"),
  };
  return (
    <>
      <PageHeader
        title="Escalations"
        description="Every red alert raised by the agent's escalation global functions."
        action={<Badge variant="destructive">{groups.new.length} new</Badge>}
      />
      <div className="space-y-8 p-6">
        <Group title="New" tone="destructive" rows={groups.new} />
        <Group title="Acknowledged" tone="warning" rows={groups.acknowledged} />
        <Group title="Resolved" tone="muted" rows={groups.resolved} />
      </div>
    </>
  );
}

function Group({
  title,
  tone,
  rows,
}: {
  title: string;
  tone: "destructive" | "warning" | "muted";
  rows: Row[];
}) {
  return (
    <section>
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <AlertTriangle className="size-4" />
        {title}
        <Badge variant={tone}>{rows.length}</Badge>
      </div>
      {rows.length === 0 ? (
        <Empty title={`No ${title.toLowerCase()} escalations`} />
      ) : (
        <div className="grid gap-2">
          {rows.map((e) => (
            <Link key={e.id} href={`/patient/${e.patient_id}`} className="block group">
              <Card className="border-l-4 border-l-[hsl(var(--destructive))]/80 transition-colors group-hover:bg-accent/40">
                <CardContent className="space-y-2 p-4">
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <Badge variant={severityTone[e.severity]} className="uppercase text-[10px]">
                        {e.severity}
                      </Badge>
                      <Badge variant="muted" className="uppercase text-[10px]">
                        {e.category}
                      </Badge>
                      {e.trigger_phrase ? (
                        <span className="text-muted-foreground">{e.trigger_phrase}</span>
                      ) : null}
                    </div>
                    <span className="text-muted-foreground">{relativeTime(e.created_at)}</span>
                  </div>
                  <div className="text-sm font-medium">
                    {e.patient?.preferred_name ?? e.patient?.name ?? e.patient_id.slice(0, 8)}
                  </div>
                  <div className="text-xs text-foreground/90">{e.trigger_text}</div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}
