import type { Metadata } from "next";

import { WorkflowEditor } from "@/components/workspace/workflow-editor";

export const metadata: Metadata = { title: "Workflow" };

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ versions?: string }>;
}) {
  const { id } = await params;
  const { versions } = await searchParams;
  return <WorkflowEditor id={id} openVersions={versions === "1"} />;
}
