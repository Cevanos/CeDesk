"use client";

import { useReportFilters } from "@/components/workspace/reports-frame";
import { AiPanel, MetricsPanel } from "@/components/workspace/reports-more";

/** "More metrics" tab. */
export function MetricsTab() {
  const { params, badRange } = useReportFilters();
  return badRange ? null : <MetricsPanel params={params} />;
}

/** "AI" tab. The AI report has no team filter. */
export function AiTab() {
  const { params, badRange } = useReportFilters();
  return badRange ? null : <AiPanel params={params} />;
}
