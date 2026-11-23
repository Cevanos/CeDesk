"use client";

import { useReportWebVitals } from "next/web-vitals";

import { trackEvent } from "@/lib/monitoring";

export function WebVitals() {
  useReportWebVitals((m) => {
    trackEvent("web-vital", { name: m.name, value: m.value, rating: m.rating, id: m.id });
  });
  return null;
}
