"use client";

import { useEffect } from "react";

/**
 * Warns before the tab is closed, reloaded or navigated away with unsaved work.
 * Browsers show their own generic prompt; custom text is ignored by design.
 */
export function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}
