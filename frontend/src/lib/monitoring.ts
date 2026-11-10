/**
 * One seam for error reporting and product telemetry. Nothing else in the app
 * imports a vendor SDK, so adopting Sentry (or anything else) is a single call:
 *
 *   // src/instrumentation-client.ts
 *   import * as Sentry from "@sentry/nextjs";
 *   setErrorReporter((e, ctx) => Sentry.captureException(e, { extra: ctx }));
 *
 * Until then errors go to the console so nothing is swallowed silently.
 */
export type ErrorContext = Record<string, unknown>;
export type ErrorReporter = (error: unknown, context?: ErrorContext) => void;
export type EventSink = (name: string, data?: Record<string, unknown>) => void;

const consoleReporter: ErrorReporter = (error, context) => {
  console.error("[cdesk]", error, context ?? "");
};

let reporter: ErrorReporter = consoleReporter;
let sink: EventSink = () => {};

export function setErrorReporter(fn: ErrorReporter | null) {
  reporter = fn ?? consoleReporter;
}

export function setEventSink(fn: EventSink | null) {
  sink = fn ?? (() => {});
}

/** Never throws: a failing reporter must not take the app down with it. */
export function reportError(error: unknown, context?: ErrorContext) {
  try {
    reporter(error, context);
  } catch {
    /* reporting is best effort */
  }
}

export function trackEvent(name: string, data?: Record<string, unknown>) {
  try {
    sink(name, data);
  } catch {
    /* telemetry is best effort */
  }
}
