import type { Metadata } from "next";

import { HelpArticle } from "@/components/public/help-center";

export const metadata: Metadata = { title: "Help article" };

export default function Page() {
  return <HelpArticle />;
}
