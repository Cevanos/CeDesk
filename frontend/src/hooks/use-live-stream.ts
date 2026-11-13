"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

/** What the stream tells us. Events only say *what* changed; we refetch through the normal API. */
export const AGENT_EVENTS = [
  "ticket.created",
  "ticket.updated",
  "ticket.resolved",
  "assignment.changed",
  "message.created",
  "chat.accepted",
  "chat.taken_over",
  "chat.closed",
  "chat.reopened",
  "notification",
  "reset",
  "end",
] as const;

export const PORTAL_EVENTS = [
  "ticket.updated",
  "ticket.resolved",
  "message.created",
  "reset",
  "end",
] as const;

export type StreamEvent = { name: string; data: Record<string, unknown> };

interface Options {
  /** Where to get a one-time ticket (POST). */
  ticketPath: string;
  /** The stream itself (GET ?ticket=...). */
  streamPath: string;
  events: readonly string[];
  enabled?: boolean;
  onEvent: (e: StreamEvent) => void;
}

const MIN_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;

function parse(raw: string): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(raw);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/**
 * One Server-Sent Events connection with a fresh one-time ticket for every (re)connect.
 * The browser's own retry would reuse a spent ticket, so on any error we close the stream
 * and start again after a growing pause. A reconnect can have missed events, so it asks
 * the page to refresh everything (a synthetic `reset`).
 */
export function useLiveStream({
  ticketPath,
  streamPath,
  events,
  enabled = true,
  onEvent,
}: Options) {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  const eventsKey = events.join(",");

  useEffect(() => {
    if (!enabled || typeof EventSource === "undefined") return;
    let stopped = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = MIN_DELAY_MS;
    let reconnected = false;
    const cleanups: (() => void)[] = [];

    const schedule = () => {
      if (stopped) return;
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, MAX_DELAY_MS);
    };

    async function connect() {
      if (stopped) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        // Don't hold a stream open in a background tab; come back when it is shown.
        const resume = () => {
          if (document.visibilityState === "visible") {
            document.removeEventListener("visibilitychange", resume);
            void connect();
          }
        };
        document.addEventListener("visibilitychange", resume);
        cleanups.push(() => document.removeEventListener("visibilitychange", resume));
        return;
      }
      let ticket: string;
      try {
        ({ ticket } = await api.post<{ ticket: string; expires_in: number }>(ticketPath));
      } catch {
        schedule();
        return;
      }
      if (stopped) return;

      const es = new EventSource(`/api/${streamPath}?ticket=${encodeURIComponent(ticket)}`);
      source = es;
      es.onopen = () => {
        delay = MIN_DELAY_MS;
        if (reconnected) handler.current({ name: "reset", data: {} });
        reconnected = true;
      };
      for (const name of eventsKey.split(",")) {
        es.addEventListener(name, (ev) => {
          const data = parse((ev as MessageEvent<string>).data);
          if (name === "end") {
            // Signed out (or the server is done with us): don't come back.
            stopped = true;
            es.close();
          }
          handler.current({ name, data });
        });
      }
      es.onerror = () => {
        es.close();
        source = null;
        reconnected = true;
        schedule();
      };
    }

    void connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
      cleanups.forEach((c) => c());
    };
  }, [ticketPath, streamPath, eventsKey, enabled]);
}

/** Turn an agent stream event into the refetches it calls for. */
export function applyAgentEvent(qc: QueryClient, e: StreamEvent) {
  switch (e.name) {
    case "notification":
      void qc.invalidateQueries({ queryKey: queryKeys.notifications.all });
      break;
    case "reset":
      void qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
      void qc.invalidateQueries({ queryKey: queryKeys.notifications.all });
      break;
    case "end":
      break;
    default:
      // Ticket, message, assignment and chat events: the list row and the open ticket.
      void qc.invalidateQueries({ queryKey: queryKeys.tickets.all });
  }
}

/** Live updates for signed-in agents. Mounted once in the app shell. */
export function useAgentLiveUpdates(enabled: boolean) {
  const qc = useQueryClient();
  useLiveStream({
    ticketPath: "stream/ticket",
    streamPath: "stream",
    events: AGENT_EVENTS,
    enabled,
    onEvent: (e) => applyAgentEvent(qc, e),
  });
}

/** Live updates for a customer looking at one portal page. */
export function usePortalLiveUpdates(enabled: boolean) {
  const qc = useQueryClient();
  useLiveStream({
    ticketPath: "portal/stream/ticket",
    streamPath: "portal/stream",
    events: PORTAL_EVENTS,
    enabled,
    onEvent: () => void qc.invalidateQueries({ queryKey: queryKeys.portal.all }),
  });
}
