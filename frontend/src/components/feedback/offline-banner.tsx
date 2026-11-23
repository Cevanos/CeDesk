"use client";

import { WifiOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { useOnline } from "@/hooks/use-online";

export function OfflineBanner() {
  const t = useTranslations("offline");
  const online = useOnline();
  const wasOffline = useRef(false);

  useEffect(() => {
    if (!online) {
      wasOffline.current = true;
    } else if (wasOffline.current) {
      wasOffline.current = false;
      toast.success(t("restored"));
    }
  }, [online, t]);

  if (online) return null;
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 bg-warning-soft px-4 py-2 text-center text-[13px] font-medium text-warning"
    >
      <WifiOff className="size-4 flex-none" aria-hidden />
      {t("banner")}
    </div>
  );
}
