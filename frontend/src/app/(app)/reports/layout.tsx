import { ReportsFrame } from "@/components/workspace/reports-frame";

export default function Layout({ children }: { children: React.ReactNode }) {
  return <ReportsFrame>{children}</ReportsFrame>;
}
