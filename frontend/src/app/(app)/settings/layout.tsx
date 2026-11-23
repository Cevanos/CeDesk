import { SettingsShell } from "@/components/workspace/settings-shell";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsShell>{children}</SettingsShell>;
}
