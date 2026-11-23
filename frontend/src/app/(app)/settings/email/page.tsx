import type { Metadata } from "next";

import { EmailView } from "@/components/workspace/email-view";

export const metadata: Metadata = { title: "Email" };

export default function Page() {
  return <EmailView />;
}
