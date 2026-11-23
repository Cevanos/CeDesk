"use client";

import { ErrorState } from "@/components/feedback/error-state";

/** Renders inside the app shell, so navigation stays usable after a crash. */
export default function AppError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorState {...props} scope="app" showHome />;
}
