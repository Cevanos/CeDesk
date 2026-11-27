import type { Metadata } from "next";

import { ReplayPage } from "@/components/workspace/replay-page";

export const metadata: Metadata = { title: "Replay" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ReplayPage id={id} />;
}
