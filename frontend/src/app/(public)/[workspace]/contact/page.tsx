import type { Metadata } from "next";

import { ContactForm } from "@/components/public/contact-form";

export const metadata: Metadata = { title: "Contact us" };

export default function Page() {
  return <ContactForm />;
}
