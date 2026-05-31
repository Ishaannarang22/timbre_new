"use client";

import * as React from "react";
import { Phone, PhoneCall, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type DemoPatient = {
  id: string;
  name: string;
  language: "en" | "es";
  blurb: string;
};

// Mirrors the server allowlist in /api/demo/call. Blurbs are flavor for the cards.
const PATIENTS: DemoPatient[] = [
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "Aisha",
    language: "en",
    blurb: "Recent delivery · English check-in",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Jess",
    language: "en",
    blurb: "Second baby · smooth recovery",
  },
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "María",
    language: "es",
    blurb: "Revisión en español",
  },
];

type Status =
  | { kind: "idle" }
  | { kind: "calling" }
  | { kind: "done"; name: string; from: string }
  | { kind: "error"; message: string };

export function DemoCall() {
  const [selected, setSelected] = React.useState<DemoPatient>(PATIENTS[0]);
  const [phone, setPhone] = React.useState("");
  const [status, setStatus] = React.useState<Status>({ kind: "idle" });

  const calling = status.kind === "calling";

  async function placeCall() {
    if (!phone.trim() || calling) return;
    setStatus({ kind: "calling" });
    try {
      const res = await fetch("/api/demo/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patient_id: selected.id, phone }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStatus({ kind: "error", message: data.error || "Couldn't place the call." });
        return;
      }
      setStatus({ kind: "done", name: data.name ?? selected.name, from: data.from ?? "" });
    } catch {
      setStatus({ kind: "error", message: "Network error — please try again." });
    }
  }

  return (
    <section className="relative overflow-hidden rounded-lg border border-[hsl(var(--border))] bg-gradient-to-br from-card to-muted/60">
      {/* soft terracotta glow */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl"
      />
      <div className="relative grid gap-8 p-6 md:grid-cols-[1.1fr_1fr] md:p-8">
        {/* Left: pitch */}
        <div className="flex flex-col justify-center">
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-primary/12 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-primary">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/70" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
            Live demo
          </span>
          <h2 className="mt-3 font-serif text-[32px] leading-[1.1] tracking-[-0.02em] text-foreground">
            Step into a patient&rsquo;s shoes —<br className="hidden sm:block" /> take her call.
          </h2>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-muted-foreground">
            Pick one of our real patients and enter your number. timbre calls you and runs
            her postpartum check-in as if you were her — pulling her actual chart. On the
            call, you can confirm it&rsquo;s using her real records: her baby, her meds, her
            appointments.
          </p>
        </div>

        {/* Right: the actual demo control */}
        <div className="flex flex-col gap-4 rounded-md border border-[hsl(var(--border))] bg-card p-5">
          {/* patient picker */}
          <div>
            <label className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
              Which patient do you want to be?
            </label>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {PATIENTS.map((p) => {
                const active = p.id === selected.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setSelected(p)}
                    className={cn(
                      "flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors",
                      active
                        ? "border-primary bg-primary/8 ring-1 ring-primary"
                        : "border-[hsl(var(--border))] bg-card hover:bg-muted",
                    )}
                  >
                    <span className="text-sm font-semibold text-foreground">{p.name}</span>
                    <span className="mt-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">
                      {p.language === "es" ? "Español" : "English"}
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">{selected.blurb}</p>
          </div>

          {/* phone input */}
          <div>
            <label
              htmlFor="demo-phone"
              className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
            >
              Your phone number
            </label>
            <div className="mt-2 flex items-center gap-2 rounded-md border border-[hsl(var(--border))] bg-background px-3 focus-within:ring-2 focus-within:ring-ring">
              <Phone className="h-4 w-4 shrink-0 text-muted-foreground" />
              <input
                id="demo-phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="+1 555 123 4567"
                value={phone}
                onChange={(e) => {
                  setPhone(e.target.value);
                  if (status.kind !== "idle") setStatus({ kind: "idle" });
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") placeCall();
                }}
                className="h-11 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/70"
              />
            </div>
          </div>

          <Button
            onClick={placeCall}
            disabled={!phone.trim() || calling}
            size="lg"
            className="w-full"
          >
            {calling ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground/40 border-t-primary-foreground" />
                Calling…
              </>
            ) : (
              <>
                <PhoneCall className="h-4 w-4" /> Call me as {selected.name}
              </>
            )}
          </Button>

          {/* status line */}
          {status.kind === "done" ? (
            <div className="flex items-start gap-2 rounded-md bg-success-soft px-3 py-2 text-sm text-success-soft-foreground">
              <Check className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                Calling you now as <strong>{status.name}</strong>
                {status.from ? (
                  <>
                    {" "}
                    — your phone will ring from <strong>{status.from}</strong>
                  </>
                ) : null}
                . Pick up: Maya checks in on you using {status.name}&rsquo;s real chart, so
                you can confirm it knows her records.
              </span>
            </div>
          ) : status.kind === "error" ? (
            <p className="text-sm text-destructive">{status.message}</p>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              We place a real call to your number and treat you as the patient you pick.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
