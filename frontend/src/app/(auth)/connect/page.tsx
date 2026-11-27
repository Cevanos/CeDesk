import type { Metadata } from "next";
import { Suspense } from "react";

import { ConnectConsent } from "@/components/auth/connect-consent";

export const metadata: Metadata = { title: "Connect an app" };

export default function ConnectPage() {
  return (
    <Suspense>
      <ConnectConsent />
    </Suspense>
  );
}
