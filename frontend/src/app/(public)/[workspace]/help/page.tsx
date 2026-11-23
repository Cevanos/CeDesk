import type { Metadata } from "next";
import { Suspense } from "react";

import { HelpHome } from "@/components/public/help-center";

export const metadata: Metadata = { title: "Help center" };

export default function Page() {
  return (
    <Suspense>
      <HelpHome />
    </Suspense>
  );
}
