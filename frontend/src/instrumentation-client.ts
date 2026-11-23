import { reportError } from "@/lib/monitoring";

/** Runs in the browser before hydration: catch what React boundaries cannot. */
window.addEventListener("error", (e) => {
  reportError(e.error ?? e.message, { source: "window.onerror" });
});
window.addEventListener("unhandledrejection", (e) => {
  reportError(e.reason, { source: "unhandledrejection" });
});
