import type { Metadata } from "next";

import { ContactFieldsView } from "@/components/workspace/contact-fields-view";

export const metadata: Metadata = { title: "Contact fields" };

export default function Page() {
  return <ContactFieldsView />;
}
