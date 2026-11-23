import { Star } from "lucide-react";

import type { Csat } from "@/lib/api/types";
import { cn, formatDateTime } from "@/lib/utils";

/** Five stars, filled up to `score` (1-5). Read-only. */
export function Stars({
  score,
  className,
  size = "size-4",
}: {
  score: number;
  className?: string;
  size?: string;
}) {
  const filled = Math.max(0, Math.min(5, Math.round(score)));
  return (
    <span
      role="img"
      aria-label={`${filled} out of 5 stars`}
      className={cn("inline-flex items-center gap-0.5", className)}
    >
      {[1, 2, 3, 4, 5].map((n) => (
        <Star
          key={n}
          aria-hidden
          className={cn(
            size,
            n <= filled ? "fill-amber-400 text-amber-400" : "fill-transparent text-border-strong",
          )}
        />
      ))}
    </span>
  );
}

/** "4.6 ★ · 12 ratings", or nothing when there are no ratings. */
export function CsatAverage({
  average,
  count,
  className,
}: {
  average?: number | null;
  count?: number | null;
  className?: string;
}) {
  if (!count || average == null) return null;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <Star className="size-3.5 fill-amber-400 text-amber-400" aria-hidden />
      <span className="font-semibold">{average.toFixed(1)}</span>
      <span className="text-muted-foreground">
        · {count} {count === 1 ? "rating" : "ratings"}
      </span>
    </span>
  );
}

/** The customer's rating of a ticket: stars, their comment and when they rated. */
export function CsatCard({ csat }: { csat: Csat }) {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2.5">
        <Stars score={csat.score} size="size-5" />
        <span className="text-lg font-semibold tabular-nums">{csat.score}/5</span>
      </div>
      {csat.comment ? (
        <blockquote className="rounded-lg border-l-4 border-amber-300 bg-surface-2 px-3 py-2 text-sm text-foreground-2">
          “{csat.comment}”
        </blockquote>
      ) : (
        <p className="text-sm text-muted-foreground">No comment left.</p>
      )}
      {csat.rated_at && (
        <p className="text-xs text-subtle-foreground">Rated {formatDateTime(csat.rated_at)}</p>
      )}
    </div>
  );
}
