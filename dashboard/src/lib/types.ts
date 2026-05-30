// Domain types mirroring supabase/schema.sql. Hand-maintained for the demo;
// `npx supabase gen types typescript` would replace this once the project is live.

export type LanguageCode = "en" | "es";
export type BirthType = "vaginal" | "c_section" | "vbac";
export type FeedingType = "breast" | "formula" | "combo";
export type BillingStatus = "paid" | "processing" | "due" | "overdue" | "in_dispute";
export type AppointmentStatus = "scheduled" | "completed" | "cancelled" | "no_show";
export type PrescriptionStatus = "active" | "discontinued" | "expired";
export type PickupStatus = "ready" | "processing" | "picked_up" | "not_picked_up" | "on_backorder";
export type CallDirection = "inbound" | "outbound";
export type CallStatus = "queued" | "in_progress" | "completed" | "escalated" | "abandoned" | "failed";
export type BleedingLevel = "none" | "spotting" | "light" | "moderate" | "heavy" | "concerning";
export type PhqInstrument = "phq2" | "phq9" | "epds";
export type AdherenceBarrier =
  | "cost" | "transport" | "side_effects" | "forgot" | "no_pharmacy" | "concerns" | "other" | "none";
export type EscalationSeverity = "urgent" | "warning" | "info";
export type EscalationCategory = "maternal" | "pediatric" | "crisis" | "concierge";
export type EscalationStatus = "new" | "acknowledged" | "resolved" | "dismissed";
export type FeedbackCategory =
  | "clinical" | "billing" | "scheduling" | "facilities" | "staff" | "communication" | "other";
export type FeedbackSentiment = "positive" | "neutral" | "negative";

export interface Patient {
  id: string;
  name: string;
  preferred_name: string | null;
  dob: string;
  language: LanguageCode;
  phone: string;
  email: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  insurance: string | null;
  primary_provider: string | null;
  doula_assigned: string | null;
  birth_date: string | null;
  birth_type: BirthType | null;
  discharge_date: string | null;
  hospital: string;
  notes: string | null;
  created_at: string;
}

export interface Newborn {
  id: string;
  patient_id: string;
  name: string | null;
  dob: string;
  sex: string | null;
  birth_weight_g: number | null;
  gestational_age_weeks: number | null;
  feeding_type: FeedingType;
  pediatrician: string | null;
}

export interface Billing {
  id: string;
  patient_id: string;
  service_description: string;
  amount_cents: number;
  status: BillingStatus;
  service_date: string | null;
  due_date: string | null;
  paid_date: string | null;
  processing_notes: string | null;
  insurance_claim_id: string | null;
  created_at: string;
}

export interface Appointment {
  id: string;
  patient_id: string;
  provider_name: string;
  provider_specialty: string | null;
  scheduled_at: string;
  duration_min: number;
  appointment_type: string | null;
  status: AppointmentStatus;
  location: string | null;
  notes: string | null;
}

export interface Prescription {
  id: string;
  patient_id: string;
  medication: string;
  dosage: string | null;
  instructions: string | null;
  prescribed_date: string;
  prescribed_by: string | null;
  status: PrescriptionStatus;
  pharmacy: string | null;
  pickup_status: PickupStatus;
  notes: string | null;
}

export interface Call {
  id: string;
  patient_id: string;
  call_sid: string | null;
  direction: CallDirection;
  status: CallStatus;
  language: LanguageCode;
  scheduled_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  current_node: string | null;
  transcript_redacted: string | null;
  summary: string | null;
  flow_name: string;
  created_at: string;
}

export interface Escalation {
  id: string;
  call_id: string | null;
  patient_id: string;
  severity: EscalationSeverity;
  category: EscalationCategory;
  trigger_phrase: string | null;
  trigger_text: string | null;
  transcript_excerpt: string | null;
  status: EscalationStatus;
  assigned_to: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  resolution_notes: string | null;
  created_at: string;
}

export interface Feedback {
  id: string;
  call_id: string | null;
  patient_id: string;
  category: FeedbackCategory;
  note: string;
  sentiment: FeedbackSentiment;
  quote_friendly: boolean;
  created_at: string;
}

export interface RecoveryAnswer {
  id: string;
  call_id: string;
  patient_id: string;
  bleeding: BleedingLevel | null;
  pain_score: number | null;
  incision_status: string | null;
  mobility_status: string | null;
  urination_status: string | null;
  emotional_state: string | null;
  notes: string | null;
  recorded_at: string;
}

export interface PhqScore {
  id: string;
  call_id: string;
  patient_id: string;
  instrument: PhqInstrument;
  score: number;
  responses: Record<string, unknown> | null;
  elevated: boolean;
  suicidal_ideation: boolean;
  recorded_at: string;
}

export interface AdherenceEntry {
  id: string;
  call_id: string;
  patient_id: string;
  prescription_id: string | null;
  medication: string | null;
  picked_up: boolean | null;
  taking_as_prescribed: boolean | null;
  barrier: AdherenceBarrier;
  barrier_notes: string | null;
  recorded_at: string;
}

export interface Csat {
  id: string;
  call_id: string;
  patient_id: string;
  rating: number;
  qualitative_summary: string | null;
  recorded_at: string;
}
