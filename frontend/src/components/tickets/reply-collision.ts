import type { Message } from "@/lib/api/types";

/** The newest message that matters for "did someone answer while I was typing?". */
export interface LatestMessage {
  id: string;
  kind: Message["kind"];
  author_member_id: string | null;
  author_name?: string | null;
}

/** Newest non-system message, or null for an empty thread. */
export function latestMessage(messages: Message[] | undefined): LatestMessage | null {
  let best: Message | null = null;
  for (const m of messages ?? []) {
    if (m.kind === "system") continue;
    if (!best || m.created_at > best.created_at) best = m;
  }
  return best
    ? {
        id: best.id,
        kind: best.kind,
        author_member_id: best.author_member_id,
        author_name: best.author_name,
      }
    : null;
}

/**
 * True when something new, written by someone other than me, landed after I started drafting.
 * `baseline` is the newest message id when I began typing ("" for an empty thread), or null
 * when I'm not drafting.
 */
export function collided(
  baseline: string | null,
  latest: LatestMessage | null | undefined,
  myId: string | undefined,
): latest is LatestMessage {
  if (baseline === null || !latest || latest.id === baseline) return false;
  return !myId || latest.author_member_id !== myId;
}

/** "Sam replied", "The customer replied", "A private note was added". */
export function describeCollision(m: LatestMessage): string {
  if (m.kind === "customer") return "The customer replied";
  if (m.kind === "note") return `${m.author_name ?? "A teammate"} added a private note`;
  if (m.kind === "ai") return "The AI assistant replied";
  return `${m.author_name ?? "A teammate"} replied`;
}
