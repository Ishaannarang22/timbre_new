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
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-[hsl(var(--border))] p-10 text-center",
        className,
      )}
    >
      <div className="text-sm font-medium text-foreground">{title}</div>
      {description ? (
        <div className="mt-1 text-sm text-muted-foreground">{description}</div>
      ) : null}
    </div>
  );
}
