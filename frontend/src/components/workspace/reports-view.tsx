"use client";

import { LoadError } from "@/components/workspace/list-shared";
import { NoReportsAccess, useReportFilters } from "@/components/workspace/reports-frame";
import { CsvButton } from "@/components/workspace/reports-more";
import { ReportSkeleton, ReportData, ArticlesTable } from "@/components/workspace/reports-sections";
import { useTeamReport } from "@/hooks/use-workspace";
import { ApiError } from "@/lib/api/client";

/** The "Team" tab. The page header, period, team and tab bar come from the reports frame. */
export function ReportsView() {
  const { params, badRange } = useReportFilters();
  const range = { from: params.from, to: params.to };
  const report = useTeamReport(params);
  if (badRange) return null;

  return (
    <>
      {report.isPending ? (
        <ReportSkeleton />
      ) : report.isError ? (
        report.error instanceof ApiError && report.error.status === 403 ? (
          <NoReportsAccess />
        ) : (
          <LoadError error={report.error} />
        )
      ) : (
        <>
          <div className="mb-4 flex justify-end">
            <CsvButton path="reports/team" params={params} withTeam name="team-report" />
          </div>
          <ReportData data={report.data} stale={report.isPlaceholderData} />
        </>
      )}
      {report.isPending || report.isError ? null : <ArticlesTable range={range} />}
    </>
  );
}
