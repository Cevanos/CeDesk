import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { UiGallery } from "@/components/dev/ui-gallery";

export const metadata: Metadata = { title: "UI gallery" };

/** Component gallery. Exists in development only; production serves a 404. */
export default function UiGalleryPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <UiGallery />;
}
