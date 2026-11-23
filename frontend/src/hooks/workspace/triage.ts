"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { TriageSettings } from "@/lib/api/types";

const words = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

/**
 * `topics` is read tolerantly: a map `{refund: [..]}`, a map of `{keywords: [..]}` objects,
 * or a list of `{name|topic|key, keywords|words}`. Anything else becomes "no topics".
 */
export function normalizeTriage(raw: unknown): TriageSettings {
  const o = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const t = o.topics;
  const topics: Record<string, string[]> = {};
  let shape: "map" | "list" = "map";
  if (Array.isArray(t)) {
    shape = "list";
    for (const it of t) {
      if (!it || typeof it !== "object") continue;
      const r = it as Record<string, unknown>;
      const name = [r.name, r.topic, r.key, r.id].find((x) => typeof x === "string" && x);
      if (typeof name === "string") topics[name] = words(r.keywords ?? r.words ?? r.terms);
    }
  } else if (t && typeof t === "object") {
    for (const [name, v] of Object.entries(t)) {
      topics[name] = Array.isArray(v)
        ? words(v)
        : words(
            (v as Record<string, unknown> | null)?.keywords ??
              (v as Record<string, unknown> | null)?.words,
          );
    }
  }
  return {
    topics,
    redact_cards: (o.redact_cards ?? o.redact_card_numbers) === true,
    is_default: typeof o.is_default === "boolean" ? o.is_default : undefined,
    shape,
  };
}

/** The body for a save, in the same shape the API sent. */
export function triageBody(
  v: Pick<TriageSettings, "topics" | "redact_cards" | "shape">,
): Record<string, unknown> {
  const topics =
    v.shape === "list"
      ? Object.entries(v.topics).map(([name, keywords]) => ({ name, keywords }))
      : v.topics;
  return { topics, redact_cards: v.redact_cards };
}

/** The body that puts the built-in keyword lists back (`topics: null`). */
export function resetBody(v: { redact_cards: boolean }): Record<string, unknown> {
  return { topics: null, redact_cards: v.redact_cards };
}

export function useTriageSettings() {
  return useQuery({
    queryKey: queryKeys.triage.all,
    queryFn: async () => normalizeTriage(await api.get<unknown>("triage-settings")),
    staleTime: 60_000,
  });
}

/**
 * Save the topics and the switch, or reset to the built-in lists. The API resets with
 * `topics: null`; it still needs the card-redaction switch, so a reset keeps the current one.
 */
export function useSaveTriageSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      body:
        | Pick<TriageSettings, "topics" | "redact_cards" | "shape">
        | { reset: true; redact_cards: boolean },
    ) => api.put<unknown>("triage-settings", "reset" in body ? resetBody(body) : triageBody(body)),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.triage.all }),
  });
}
