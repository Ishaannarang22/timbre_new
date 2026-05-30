import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { daysSince } from "@/lib/utils";
import type { Patient } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PatientsList() {
  const db = supabaseAdmin();
  const { data } = await db
    .from("patient")
    .select("*")
    .order("discharge_date", { ascending: false })
    .limit(200);
  const patients = (data ?? []) as Patient[];
  return (
    <>
      <PageHeader
        title="Patients"
        description="All postpartum patients in this demo roster."
        action={<Badge variant="outline">{patients.length} active</Badge>}
      />
      <div className="grid gap-3 p-6 md:grid-cols-2 xl:grid-cols-3">
        {patients.length === 0 ? (
          <Empty title="No patients" />
        ) : (
          patients.map((p) => {
            const dpp = daysSince(p.birth_date);
            return (
              <Link key={p.id} href={`/patient/${p.id}`} className="block group">
                <Card className="h-full transition-colors group-hover:bg-accent/40">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold">{p.preferred_name ?? p.name}</span>
                      <Badge variant="muted" className="uppercase text-[10px]">
                        {p.language}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {p.birth_type?.replace("_", " ")}
                      {dpp != null ? `  ·  day ${dpp} postpartum` : null}
                    </div>
                    <div className="mt-2 text-[11px] text-muted-foreground line-clamp-2">
                      {p.notes}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })
        )}
      </div>
    </>
  );
}
