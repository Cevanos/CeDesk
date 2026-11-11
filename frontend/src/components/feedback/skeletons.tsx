import { Card, Skeleton } from "@/components/ui/primitives";

/** Layout-matched placeholders for route `loading.tsx` files. */
export function ListSkeleton({ rows = 7 }: { rows?: number }) {
  return (
    <div
      className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8"
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Loading…</span>
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-2 h-4 w-72 max-w-full" />
      <div className="mt-5 grid grid-cols-3 gap-2 sm:gap-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 rounded-xl sm:h-28" />
        ))}
      </div>
      <Card className="mt-4 overflow-hidden">
        <ul className="divide-y divide-border">
          {Array.from({ length: rows }).map((_, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-4">
              <Skeleton className="size-9 flex-none rounded-full" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-3 w-1/3" />
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function DetailSkeleton() {
  return (
    <div
      className="mx-auto grid w-full max-w-6xl gap-5 px-3 py-5 sm:px-6 sm:py-8 lg:grid-cols-[1fr_20rem]"
      aria-busy="true"
    >
      <span className="sr-only">Loading…</span>
      <div className="space-y-4">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-3/4" />
        <Card className="space-y-4 p-5">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          ))}
        </Card>
      </div>
      <Card className="hidden h-96 lg:block" />
    </div>
  );
}

export function SettingsSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true">
      <span className="sr-only">Loading…</span>
      <Skeleton className="h-8 w-48" />
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-16 rounded-xl" />
      ))}
    </div>
  );
}
