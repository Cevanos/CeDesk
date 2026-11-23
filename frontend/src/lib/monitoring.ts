/**
 * Single place for error reporting and telemetry. Nothing else imports a vendor
 * SDK, so adopting Sentry is one call:
 *
 *   // src/instrumentation-client.ts
 *   import * as Sentry from "@sentry/nextjs";
 *   setErrorReporter((e, ctx) => Sentry.captureException(e, { extra: ctx }));
 *
 * Until then errors just go to the console.
 */
export type ErrorContext = Record<string, unknown>;
export type ErrorReporter = (error: unknown, context?: ErrorContext) => void;
export type EventSink = (name: string, data?: Record<string, unknown>) => void;

const consoleReporter: ErrorReporter = (error, context) => {
  console.error("[cedesk]", error, context ?? "");
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
