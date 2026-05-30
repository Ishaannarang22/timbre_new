import Link from "next/link";
import { notFound } from "next/navigation";
import {
  AlertTriangle,
  Baby,
  CalendarDays,
  ClipboardList,
  HeartPulse,
  MessageSquareQuote,
  Pill,
  Receipt,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Empty } from "@/components/ui/empty";
import { PageHeader } from "@/components/page-header";
import { supabaseAdmin } from "@/lib/supabase/server";
import { formatCents, formatPhone, daysSince } from "@/lib/utils";
import {
  billingStatusLabel,
  billingStatusTone,
  feedbackCategoryLabel,
  feedbackTone,
  pickupStatusLabel,
  relativeTime,
  severityTone,
} from "@/lib/format";
import type {
  Appointment,
  Billing,
  Csat,
  Escalation,
  Feedback,
  Newborn,
  Patient,
  PhqScore,
  Prescription,
  RecoveryAnswer,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function PatientProfile({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const db = supabaseAdmin();
  const [patientRes, newbornRes, billingRes, appointmentsRes, prescriptionsRes, recoveryRes, phqRes, csatRes, feedbackRes, escalationsRes] =
    await Promise.all([
      db.from("patient").select("*").eq("id", id).single(),
      db.from("newborn").select("*").eq("patient_id", id),
      db.from("billing").select("*").eq("patient_id", id).order("created_at", { ascending: false }),
      db.from("appointment").select("*").eq("patient_id", id).order("scheduled_at", { ascending: true }),
      db.from("prescription").select("*").eq("patient_id", id).order("prescribed_date", { ascending: false }),
      db.from("recovery_answer").select("*").eq("patient_id", id).order("recorded_at", { ascending: false }).limit(5),
      db.from("phq_score").select("*").eq("patient_id", id).order("recorded_at", { ascending: false }).limit(5),
      db.from("csat").select("*").eq("patient_id", id).order("recorded_at", { ascending: false }).limit(5),
      db.from("feedback").select("*").eq("patient_id", id).order("created_at", { ascending: false }).limit(10),
      db.from("escalation").select("*").eq("patient_id", id).order("created_at", { ascending: false }).limit(10),
    ]);

  if (patientRes.error || !patientRes.data) notFound();
  const patient = patientRes.data as Patient;
  const newborns = (newbornRes.data ?? []) as Newborn[];
  const billing = (billingRes.data ?? []) as Billing[];
  const appointments = (appointmentsRes.data ?? []) as Appointment[];
  const prescriptions = (prescriptionsRes.data ?? []) as Prescription[];
  const recovery = (recoveryRes.data ?? []) as RecoveryAnswer[];
  const phq = (phqRes.data ?? []) as PhqScore[];
  const csat = (csatRes.data ?? []) as Csat[];
  const feedback = (feedbackRes.data ?? []) as Feedback[];
  const escalations = (escalationsRes.data ?? []) as Escalation[];

  const dpp = daysSince(patient.birth_date);

  return (
    <>
      <PageHeader
        title={patient.preferred_name ?? patient.name}
        description={
          [
            patient.name,
            patient.birth_type ? patient.birth_type.replace("_", " ") : null,
            dpp != null ? `day ${dpp} postpartum` : null,
            patient.primary_provider,
            patient.doula_assigned,
          ]
            .filter(Boolean)
            .join("  ·  ")
        }
        action={
          <Link
            href="/"
            className="text-sm text-muted-foreground underline-offset-4 hover:underline hover:text-foreground"
          >
            ← back to queue
          </Link>
        }
      />
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <ProfileCard patient={patient} newborns={newborns} />
        <BillingCard billing={billing} />
        <AppointmentsCard appointments={appointments} />
        <PrescriptionsCard prescriptions={prescriptions} className="lg:col-span-2" />
        <ScreensCard phq={phq} csat={csat} recovery={recovery} />
        <FeedbackCard feedback={feedback} className="lg:col-span-2" />
        <EscalationsCard escalations={escalations} />
      </div>
    </>
  );
}

function ProfileCard({ patient, newborns }: { patient: Patient; newborns: Newborn[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardList className="size-4 text-primary" /> Profile
        </CardTitle>
        <CardDescription>{patient.hospital}  ·  {patient.insurance ?? "—"}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <Field label="Language" value={patient.language === "es" ? "Spanish" : "English"} />
        <Field label="Phone" value={formatPhone(patient.phone)} />
        <Field
          label="Birth"
          value={
            patient.birth_date
              ? `${patient.birth_date}  ·  ${patient.birth_type?.replace("_", " ") ?? "—"}`
              : "—"
          }
        />
        <Field label="Discharged" value={patient.discharge_date ?? "—"} />
        {patient.notes ? (
          <div className="rounded-md bg-accent/60 p-3 text-xs text-accent-foreground">
            {patient.notes}
          </div>
        ) : null}
        {newborns.length > 0 ? (
          <div className="border-t border-[hsl(var(--border))] pt-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
              <Baby className="size-3.5" /> Newborn
            </div>
            {newborns.map((n) => (
              <div key={n.id} className="space-y-1 text-xs">
                <div className="font-medium text-foreground">{n.name ?? "Baby"}</div>
                <div className="text-muted-foreground">
                  {n.dob}  ·  {n.sex ?? "—"}  ·  {n.birth_weight_g ? `${n.birth_weight_g}g` : "—"}  ·  {n.feeding_type}
                </div>
                {n.pediatrician ? <div className="text-muted-foreground">Peds: {n.pediatrician}</div> : null}
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function BillingCard({ billing }: { billing: Billing[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Receipt className="size-4 text-primary" /> Billing
        </CardTitle>
        <CardDescription>What the concierge agent surfaces on "where is my bill?"</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {billing.length === 0 ? (
          <Empty title="No billing records" />
        ) : (
          billing.map((b) => (
            <div key={b.id} className="rounded-md border border-[hsl(var(--border))] p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{formatCents(b.amount_cents)}</span>
                <Badge variant={billingStatusTone[b.status]}>{billingStatusLabel[b.status]}</Badge>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{b.service_description}</div>
              {b.processing_notes ? (
                <div className="mt-2 text-xs text-foreground/80 italic">"{b.processing_notes}"</div>
              ) : null}
              {b.due_date ? (
                <div className="mt-2 text-[11px] text-muted-foreground">due {b.due_date}</div>
              ) : null}
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function AppointmentsCard({ appointments }: { appointments: Appointment[] }) {
  const upcoming = appointments.filter((a) => a.status === "scheduled" && new Date(a.scheduled_at) > new Date());
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CalendarDays className="size-4 text-primary" /> Upcoming visits
        </CardTitle>
        <CardDescription>Appointments the concierge agent can reference.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {upcoming.length === 0 ? (
          <Empty title="No upcoming visits" />
        ) : (
          upcoming.slice(0, 5).map((a) => (
            <div key={a.id} className="rounded-md border border-[hsl(var(--border))] p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{a.provider_name}</span>
                <span className="text-xs text-muted-foreground">{relativeTime(a.scheduled_at)}</span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {a.provider_specialty}  ·  {a.appointment_type?.replace(/_/g, " ")}  ·  {a.location}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function PrescriptionsCard({ prescriptions, className }: { prescriptions: Prescription[]; className?: string }) {
  const active = prescriptions.filter((p) => p.status === "active");
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Pill className="size-4 text-primary" /> Active prescriptions
        </CardTitle>
        <CardDescription>The adherence node logs barriers against these.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {active.length === 0 ? (
          <Empty title="No active prescriptions" />
        ) : (
          active.map((p) => (
            <div key={p.id} className="rounded-md border border-[hsl(var(--border))] p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{p.medication}</span>
                <Badge variant={p.pickup_status === "picked_up" ? "success" : p.pickup_status === "not_picked_up" ? "warning" : "outline"}>
                  {pickupStatusLabel[p.pickup_status]}
                </Badge>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{p.instructions}</div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                {p.pharmacy ?? "—"}  ·  prescribed {p.prescribed_date} by {p.prescribed_by ?? "—"}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function ScreensCard({ phq, csat, recovery }: { phq: PhqScore[]; csat: Csat[]; recovery: RecoveryAnswer[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HeartPulse className="size-4 text-primary" /> Recent screens
        </CardTitle>
        <CardDescription>PHQ / EPDS, CSAT, recovery answers.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {phq[0] ? (
          <div className="rounded-md border border-[hsl(var(--border))] p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide">{phq[0].instrument}</span>
              <Badge variant={phq[0].elevated ? "destructive" : "muted"}>score {phq[0].score}</Badge>
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {relativeTime(phq[0].recorded_at)}
              {phq[0].suicidal_ideation ? "  ·  ⚠ suicidal ideation flagged" : null}
            </div>
          </div>
        ) : null}
        {csat[0] ? (
          <div className="rounded-md border border-[hsl(var(--border))] p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wide">CSAT</span>
              <Badge variant={csat[0].rating >= 4 ? "success" : csat[0].rating <= 2 ? "destructive" : "outline"}>
                {csat[0].rating}/5
              </Badge>
            </div>
            {csat[0].qualitative_summary ? (
              <div className="mt-1 text-xs italic text-muted-foreground">"{csat[0].qualitative_summary}"</div>
            ) : null}
          </div>
        ) : null}
        {recovery[0] ? (
          <div className="rounded-md border border-[hsl(var(--border))] p-3 space-y-1">
            <div className="text-xs font-semibold uppercase tracking-wide">Last recovery check</div>
            <div className="grid grid-cols-2 gap-1 text-xs text-muted-foreground">
              {recovery[0].bleeding ? <span>bleeding: {recovery[0].bleeding}</span> : null}
              {recovery[0].pain_score != null ? <span>pain: {recovery[0].pain_score}/10</span> : null}
              {recovery[0].mobility_status ? <span>mobility: {recovery[0].mobility_status}</span> : null}
              {recovery[0].incision_status ? <span>incision: {recovery[0].incision_status}</span> : null}
            </div>
          </div>
        ) : null}
        {!phq[0] && !csat[0] && !recovery[0] ? <Empty title="No screens yet" /> : null}
      </CardContent>
    </Card>
  );
}

function FeedbackCard({ feedback, className }: { feedback: Feedback[]; className?: string }) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquareQuote className="size-4 text-primary" /> Patient feedback
        </CardTitle>
        <CardDescription>Open-ended comments captured during calls.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {feedback.length === 0 ? (
          <Empty title="No feedback yet" />
        ) : (
          feedback.map((f) => (
            <div key={f.id} className="rounded-md border border-[hsl(var(--border))] p-3">
              <div className="flex items-center justify-between gap-2 text-xs">
                <Badge variant="outline">{feedbackCategoryLabel[f.category]}</Badge>
                <div className="flex items-center gap-2">
                  <Badge variant={feedbackTone[f.sentiment]}>{f.sentiment}</Badge>
                  <span className="text-muted-foreground">{relativeTime(f.created_at)}</span>
                </div>
              </div>
              <div className="mt-2 text-sm text-foreground/90">"{f.note}"</div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function EscalationsCard({ escalations }: { escalations: Escalation[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="size-4 text-destructive" /> Escalations
        </CardTitle>
        <CardDescription>Recent red alerts for this patient.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {escalations.length === 0 ? (
          <Empty title="No escalations" />
        ) : (
          escalations.map((e) => (
            <div key={e.id} className="rounded-md border border-[hsl(var(--border))] p-3">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <Badge variant={severityTone[e.severity]} className="uppercase">
                    {e.severity}
                  </Badge>
                  <Badge variant="muted" className="uppercase">{e.category}</Badge>
                </div>
                <span className="text-muted-foreground">{relativeTime(e.created_at)}</span>
              </div>
              <div className="mt-1 text-xs text-foreground/90">{e.trigger_text}</div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  );
}
