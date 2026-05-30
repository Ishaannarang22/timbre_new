"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  ClipboardList,
  Gauge,
  MessageSquareQuote,
} from "lucide-react";
import { cn } from "@/lib/utils";

const items = [
  { href: "/", label: "Today's queue", icon: CalendarClock },
  { href: "/live", label: "Live calls", icon: Activity },
  { href: "/escalations", label: "Escalations", icon: AlertTriangle },
  { href: "/feedback", label: "Patient Voices", icon: MessageSquareQuote },
  { href: "/patients", label: "Patients", icon: ClipboardList },
  { href: "/evals", label: "Cekura evals", icon: Gauge },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="hidden lg:flex w-64 shrink-0 flex-col border-r border-[hsl(var(--border))] bg-[hsl(var(--background))]">
      <div className="flex items-baseline gap-2 px-6 pt-7 pb-6">
        <span className="font-serif text-[26px] leading-none text-foreground tracking-tight">
          timbre
        </span>
        <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          care console
        </span>
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 px-3">
        {items.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/"
              ? pathname === "/"
              : pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-sm px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-muted text-foreground font-medium"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <Icon className="size-4 shrink-0" strokeWidth={1.75} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-6 py-5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        Demo data only — no real PHI.
      </div>
    </aside>
  );
}
