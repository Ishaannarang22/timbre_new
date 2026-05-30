"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  ClipboardList,
  Gauge,
  Heart,
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
    <aside className="hidden lg:flex w-64 shrink-0 flex-col border-r border-[hsl(var(--border))] bg-card">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="rounded-md bg-primary/10 p-1.5 text-primary">
          <Heart className="size-5" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold">timbre</span>
          <span className="text-xs text-muted-foreground">postpartum care console</span>
        </div>
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
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="px-5 py-4 text-xs text-muted-foreground">
        Demo data only — no real PHI.
      </div>
    </aside>
  );
}
