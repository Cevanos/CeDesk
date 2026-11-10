"use client";

import { ErrorState } from "@/components/feedback/error-state";

export default function RootError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorState {...props} scope="root" />;
}
