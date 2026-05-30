import * as React from "react";
import { cn } from "@/lib/utils";

export function Empty({
  title,
  description,
  className,
}: {
  title: string;
  description?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-md border border-dashed border-[hsl(var(--border))] bg-card/50 px-10 py-12 text-center",
        className,
      )}
    >
      <div className="font-serif text-[18px] tracking-tight text-foreground">{title}</div>
      {description ? (
        <div className="mt-1.5 max-w-md text-sm text-muted-foreground">{description}</div>
      ) : null}
    </div>
  );
}
