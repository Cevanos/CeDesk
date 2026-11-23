import { SettingsSkeleton } from "@/components/feedback/skeletons";

export default function Loading() {
  return (
    <div className="mx-auto w-full max-w-6xl px-3 py-5 sm:px-6 sm:py-8">
      <SettingsSkeleton />
    </div>
  );
}
