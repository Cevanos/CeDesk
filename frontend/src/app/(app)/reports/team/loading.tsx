import { Skeleton } from "@/components/ui/primitives";

export default function Loading() {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-3 py-5 sm:px-6 sm:py-8">
      <Skeleton className="h-8 w-40" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-56 rounded-xl" />
    </div>
  );
}
