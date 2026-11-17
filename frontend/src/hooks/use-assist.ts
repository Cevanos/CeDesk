"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useMe } from "@/hooks/use-auth";
import { ApiError, api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { Me } from "@/lib/api/types";

/** Longest text the AI endpoints accept. */
export const AI_MAX_CHARS = 8000;

export const TONES = [
  { value: "friendly", label: "Friendlier" },
  { value: "professional", label: "More professional" },
  { value: "concise", label: "Shorter" },
  { value: "empathetic", label: "More empathetic" },
  { value: "fix", label: "Fix spelling and grammar" },
] as const;
export type Tone = (typeof TONES)[number]["value"];

export const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "pt", label: "Portuguese" },
  { value: "it", label: "Italian" },
  { value: "nl", label: "Dutch" },
  { value: "hi", label: "Hindi" },
  { value: "ar", label: "Arabic" },
  { value: "zh", label: "Chinese" },
  { value: "ja", label: "Japanese" },
  { value: "ko", label: "Korean" },
] as const;

/** GET /me says `ai: true` only when Cevanos is connected. Hide every AI control otherwise. */
export function useAiEnabled(): boolean {
  const me = useMe();
  return me.data?.ai === true;
}

/** True for the 409 that means Cevanos is not connected (hide the AI buttons). */
export function isCevanosMissing(e: unknown): boolean {
  return (
    e instanceof ApiError &&
    e.status === 409 &&
    /cevanos_not_connected|not.connected/i.test(`${e.code} ${e.message}`)
  );
}

/** The sentence to show for a failed AI call. */
export function aiErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "The AI request failed. Try again.";
  if (isCevanosMissing(e)) return "Connect Cevanos in Settings to use AI.";
  // The key is connected but lacks the ai:assist scope: the API's message tells an Admin what to do.
  if (e.code === "cevanos_scope_missing") {
    return e.message || "The Cevanos key lacks the ai:assist scope. Ask an Admin to add it.";
  }
  if (e.status === 503) return "Cevanos didn't answer. Try again.";
  if (e.status === 422) return e.message || "The AI couldn't use that text.";
  return e.userMessage;
}

// Nothing the AI returns is saved by these calls: the caller only shows the text. `silent`
// keeps the global error toast off because each button shows its own message.
const meta = { silent: true };

export function useSummarise(ticketId: string) {
  return useMutation({
    meta,
    mutationFn: () =>
      api.post<{ summary: string }>(`tickets/${ticketId}/summary`).then((r) => r.summary),
  });
}

export function useSuggestReply(ticketId: string) {
  return useMutation({
    meta,
    mutationFn: () => api.post<{ text: string }>(`tickets/${ticketId}/draft`).then((r) => r.text),
  });
}

export function useImprove() {
  return useMutation({
    meta,
    mutationFn: (v: { text: string; tone: Tone }) =>
      api.post<{ text: string }>("assist/improve", v).then((r) => r.text),
  });
}

export function useTranslate() {
  return useMutation({
    meta,
    mutationFn: (v: { text: string; to: string }) =>
      api.post<{ text: string; from?: string }>("assist/translate", v),
  });
}

/** After a 409 cevanos_not_connected: flip `ai` off in the cached /me so every AI control hides. */
export function useHideAiWhenMissing() {
  const qc = useQueryClient();
  return (e: unknown) => {
    if (isCevanosMissing(e)) {
      qc.setQueryData<Me>(queryKeys.me, (m) => (m ? { ...m, ai: false } : m));
    }
  };
}
