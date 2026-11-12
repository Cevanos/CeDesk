import type { Metadata } from "next";

import { PortalHome } from "@/components/public/portal-home";

export const metadata: Metadata = { title: "My tickets" };

export default function Page() {
  return <PortalHome />;
}
