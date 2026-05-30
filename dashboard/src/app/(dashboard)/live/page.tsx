import { LiveCalls } from "./_live-calls";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import type { Call, Escalation, Patient } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function LivePage() {
  const db = supabaseAdmin();
  const [callsRes, escalationsRes, patientsRes] = await Promise.all([
    db.from("call").select("*").in("status", ["in_progress", "queued"]).order("started_at", { ascending: false }).limit(20),
    db.from("escalation").select("*").in("status", ["new", "acknowledged"]).order("created_at", { ascending: false }).limit(20),
    db.from("patient").select("id, name, preferred_name, language, birth_date, birth_type, primary_provider, doula_assigned").limit(200),
  ]);
  const calls = (callsRes.data ?? []) as Call[];
  const escalations = (escalationsRes.data ?? []) as Escalation[];
  const patients = (patientsRes.data ?? []) as Pick<Patient, "id" | "name" | "preferred_name" | "language" | "birth_date" | "birth_type" | "primary_provider" | "doula_assigned">[];
  return (
    <>
      <PageHeader
        title="Live calls"
        description="Active conversations and new escalations. Updates in real time from Supabase Realtime."
      />
      <LiveCalls initialCalls={calls} initialEscalations={escalations} patients={patients} />
    </>
  );
}
