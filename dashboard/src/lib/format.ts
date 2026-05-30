import type { BillingStatus, CallStatus, EscalationSeverity, FeedbackCategory, FeedbackSentiment, PickupStatus } from "./types";

export const billingStatusLabel: Record<BillingStatus, string> = {
  paid: "Paid",
  processing: "Processing",
  due: "Due",
  overdue: "Overdue",
  in_dispute: "In dispute",
};

export const billingStatusTone: Record<BillingStatus, "muted" | "success" | "outline" | "destructive" | "warning"> = {
  paid: "success",
  processing: "outline",
  due: "warning",
  overdue: "destructive",
  in_dispute: "outline",
};

export const pickupStatusLabel: Record<PickupStatus, string> = {
  ready: "Ready for pickup",
  processing: "Pharmacy processing",
  picked_up: "Picked up",
  not_picked_up: "Not picked up",
  on_backorder: "On backorder",
};

export const callStatusTone: Record<CallStatus, "muted" | "outline" | "success" | "destructive" | "warning"> = {
  queued: "outline",
  in_progress: "success",
  completed: "muted",
  escalated: "destructive",
  abandoned: "warning",
  failed: "destructive",
};

export const severityTone: Record<EscalationSeverity, "destructive" | "warning" | "outline"> = {
  urgent: "destructive",
  warning: "warning",
  info: "outline",
};

export const feedbackTone: Record<FeedbackSentiment, "success" | "muted" | "destructive"> = {
  positive: "success",
  neutral: "muted",
  negative: "destructive",
};

export const feedbackCategoryLabel: Record<FeedbackCategory, string> = {
  clinical: "Clinical care",
  billing: "Billing",
  scheduling: "Scheduling",
  facilities: "Facilities",
  staff: "Staff",
  communication: "Communication",
  other: "Other",
};

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = (now - then) / 1000; // seconds
  const abs = Math.abs(diff);
  if (abs < 60) return diff >= 0 ? "just now" : "soon";
  const past = diff >= 0;
  if (abs < 3600) {
    const m = Math.round(abs / 60);
    return past ? `${m}m ago` : `in ${m}m`;
  }
  if (abs < 86400) {
    const h = Math.round(abs / 3600);
    return past ? `${h}h ago` : `in ${h}h`;
  }
  const d = Math.round(abs / 86400);
  return past ? `${d}d ago` : `in ${d}d`;
}
