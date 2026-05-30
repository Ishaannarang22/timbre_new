import Link from "next/link";
import { MessageSquareQuote } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { feedbackCategoryLabel, feedbackTone, relativeTime } from "@/lib/format";
import type { FeedbackCategory } from "@/lib/types";

export const dynamic = "force-dynamic";

type Row = {
  id: string;
  patient_id: string;
  category: FeedbackCategory;
  note: string;
  sentiment: "positive" | "neutral" | "negative";
  created_at: string;
  patient: { id: string; name: string; preferred_name: string | null } | null;
};

const CATEGORIES: FeedbackCategory[] = [
  "clinical",
  "billing",
  "scheduling",
  "facilities",
  "staff",
  "communication",
  "other",
];

export default async function FeedbackPage() {
  const db = supabaseAdmin();
  const { data } = await db
    .from("feedback")
    .select(`id, patient_id, category, note, sentiment, created_at, patient:patient_id (id, name, preferred_name)`)
    .order("created_at", { ascending: false })
    .limit(200);
  const rows: Row[] = ((data ?? []) as unknown as Row[]) ?? [];
  return (
    <>
      <PageHeader
        title="Patient Voices"
        description="Open-ended feedback the agent captured across calls, grouped by what patients are talking about."
        action={<Badge variant="outline">{rows.length} entries</Badge>}
      />
      <div className="space-y-8 p-6">
        {CATEGORIES.map((cat) => {
          const group = rows.filter((r) => r.category === cat);
          if (group.length === 0) return null;
          return (
            <section key={cat}>
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <MessageSquareQuote className="size-4 text-primary" />
                {feedbackCategoryLabel[cat]}
                <Badge variant="muted">{group.length}</Badge>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {group.map((f) => (
                  <Link key={f.id} href={`/patient/${f.patient_id}`} className="block group">
                    <Card className="h-full transition-colors group-hover:bg-accent/40">
                      <CardContent className="p-4 space-y-2">
                        <div className="text-sm text-foreground leading-snug">"{f.note}"</div>
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>
                            — {f.patient?.preferred_name ?? f.patient?.name ?? "Anonymous"}
                          </span>
                          <div className="flex items-center gap-2">
                            <Badge variant={feedbackTone[f.sentiment]}>{f.sentiment}</Badge>
                            <span>{relativeTime(f.created_at)}</span>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  </Link>
                ))}
              </div>
            </section>
          );
        })}
        {rows.length === 0 ? <Empty title="No feedback captured yet" /> : null}
      </div>
    </>
  );
}
