"use client";

import { Languages, Sparkles, Undo2, Wand2, X } from "lucide-react";
import { useState } from "react";

import { AiBadge, AiMark } from "@/components/ui/ai";
import { LinkifiedText } from "@/components/ui/linkified-text";
import { MenuItem, Popover } from "@/components/ui/overlay";
import { Button } from "@/components/ui/primitives";
import {
  AI_MAX_CHARS,
  LANGUAGES,
  TONES,
  aiErrorMessage,
  useAiEnabled,
  useHideAiWhenMissing,
  useImprove,
  useSuggestReply,
  useSummarise,
  useTranslate,
} from "@/hooks/use-assist";

/** Ticket page card: an AI summary of the conversation. Shown only, never saved. */
export function AiSummaryCard({ ticketId }: { ticketId: string }) {
  const enabled = useAiEnabled();
  const hide = useHideAiWhenMissing();
  const summarise = useSummarise(ticketId);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!enabled) return null;

  const run = () => {
    setError(null);
    summarise.mutate(undefined, {
      onSuccess: (s) => setText(s),
      onError: (e) => {
        hide(e);
        setError(aiErrorMessage(e));
      },
    });
  };

  return (
    <section
      className="ai-frame relative overflow-hidden rounded-xl p-3.5 shadow-card"
      aria-label="AI summary"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -top-10 -right-10 size-28 rounded-full bg-ai-gradient opacity-15 blur-2xl"
      />
      {summarise.isPending && (
        <div aria-hidden className="absolute inset-x-0 top-0 h-0.5 ai-shimmer" />
      )}
      <div className="relative flex flex-wrap items-center gap-2">
        <AiMark />
        <h2 className="text-sm font-semibold">AI summary</h2>
        <AiBadge className="hidden sm:inline-flex">Cevanos</AiBadge>
        <div className="ml-auto flex items-center gap-1">
          {text !== null && (
            <button
              type="button"
              aria-label="Hide summary"
              onClick={() => setText(null)}
              className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2"
            >
              <X className="size-3.5" />
            </button>
          )}
          <Button size="sm" variant="ai" loading={summarise.isPending} onClick={run}>
            {text === null ? "Summarise" : "Refresh"}
          </Button>
        </div>
      </div>
      {summarise.isPending && (
        <p className="mt-2 text-xs text-muted-foreground" role="status">
          Reading the conversation… this can take up to 20 seconds.
        </p>
      )}
      {text !== null && (
        <p className="relative mt-3 text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
          {text}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-danger">
          {error}
        </p>
      )}
      {text !== null && (
        <p className="mt-2 text-[11px] text-subtle-foreground">
          Written by AI and not saved on the ticket. Check it before you rely on it.
        </p>
      )}
    </section>
  );
}

/**
 * AI buttons in the composer. Suggest reply / Improve / Translate put their text into the box
 * for the agent to edit and send (nothing is sent or saved here); Undo brings the old text back.
 */
export function ComposerAi({
  ticketId,
  body,
  setBody,
  canSuggest,
}: {
  ticketId: string;
  body: string;
  setBody: (text: string) => void;
  /** Suggest reply needs the reply right and the Reply tab. */
  canSuggest: boolean;
}) {
  const enabled = useAiEnabled();
  const hide = useHideAiWhenMissing();
  const draft = useSuggestReply(ticketId);
  const improve = useImprove();
  const translate = useTranslate();
  const [before, setBefore] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (!enabled) return null;

  const busy = draft.isPending || improve.isPending || translate.isPending;
  const empty = body.trim().length === 0;
  const tooLong = body.length > AI_MAX_CHARS;
  const textTitle = empty
    ? "Write something first"
    : tooLong
      ? `Text must be ${AI_MAX_CHARS.toLocaleString()} characters or fewer`
      : undefined;

  const fail = (e: unknown) => {
    hide(e);
    setError(aiErrorMessage(e));
  };
  const start = () => {
    setError(null);
    setNote(null);
  };
  const apply = (text: string, old: string) => {
    setBefore(old);
    setBody(text);
  };

  return (
    <div
      className="ai-frame mx-1 mb-1.5 flex flex-wrap items-center gap-1 rounded-xl px-1.5 py-1"
      role="group"
      aria-label="AI help"
    >
      <AiMark className="mr-0.5 size-5 rounded-md" />
      {canSuggest && (
        <Button
          size="sm"
          variant="ai"
          loading={draft.isPending}
          disabled={busy}
          onClick={() => {
            start();
            draft.mutate(undefined, { onSuccess: (t) => apply(t, body), onError: fail });
          }}
        >
          {!draft.isPending && <Sparkles className="size-3.5" aria-hidden />} Suggest reply
        </Button>
      )}
      <Popover
        align="left"
        className="top-auto bottom-[calc(100%+8px)]"
        trigger={({ props }) => (
          <Button
            size="sm"
            variant="ghost"
            loading={improve.isPending}
            disabled={busy || empty || tooLong}
            title={textTitle}
            {...props}
          >
            {!improve.isPending && <Wand2 className="size-3.5" aria-hidden />} Improve
          </Button>
        )}
      >
        {(close) =>
          TONES.map((t) => (
            <MenuItem
              key={t.value}
              onClick={() => {
                close();
                start();
                const old = body;
                improve.mutate(
                  { text: old, tone: t.value },
                  {
                    onSuccess: (r) => (r.note ? setNote(r.note) : apply(r.text, old)),
                    onError: fail,
                  },
                );
              }}
            >
              {t.label}
            </MenuItem>
          ))
        }
      </Popover>
      <Popover
        align="left"
        className="top-auto bottom-[calc(100%+8px)] max-h-64 overflow-y-auto"
        trigger={({ props }) => (
          <Button
            size="sm"
            variant="ghost"
            loading={translate.isPending}
            disabled={busy || empty || tooLong}
            title={textTitle}
            {...props}
          >
            {!translate.isPending && <Languages className="size-3.5" aria-hidden />} Translate
          </Button>
        )}
      >
        {(close) =>
          LANGUAGES.map((l) => (
            <MenuItem
              key={l.value}
              onClick={() => {
                close();
                start();
                const old = body;
                translate.mutate(
                  { text: old, to: l.value },
                  {
                    onSuccess: (r) => (r.note ? setNote(r.note) : apply(r.text, old)),
                    onError: fail,
                  },
                );
              }}
            >
              To {l.label}
            </MenuItem>
          ))
        }
      </Popover>
      {before !== null && !busy && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setBody(before);
            setBefore(null);
          }}
        >
          <Undo2 className="size-3.5" aria-hidden /> Undo
        </Button>
      )}
      {busy && (
        <span role="status" className="text-xs text-muted-foreground">
          Working… up to 20 seconds
        </span>
      )}
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
      {note && !error && (
        <span role="status" className="text-xs text-muted-foreground">
          {note}
        </span>
      )}
    </div>
  );
}

/** Under a message: translate it for the agent to read. Shown only, never saved. */
export function TranslateMessage({ text }: { text: string }) {
  const enabled = useAiEnabled();
  const hide = useHideAiWhenMissing();
  const translate = useTranslate();
  const [result, setResult] = useState<{ text: string; from?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  if (!enabled || !text.trim() || text.length > AI_MAX_CHARS) return null;

  return (
    <div className="mt-1.5">
      {result && (
        <div className="ai-frame rounded-lg px-3 py-2">
          <p className="text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
            <LinkifiedText text={result.text} />
          </p>
          <p className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
            <Sparkles className="size-3 text-ai-via" aria-hidden />
            Translated{result.from ? ` from ${result.from}` : ""} by AI
            <button type="button" className="underline" onClick={() => setResult(null)}>
              Hide translation
            </button>
          </p>
        </div>
      )}
      {!result && (
        <Popover
          align="left"
          className="max-h-64 overflow-y-auto"
          trigger={({ props }) => (
            <button
              type="button"
              disabled={translate.isPending}
              className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground disabled:opacity-60"
              {...props}
            >
              <Languages className="size-3" aria-hidden />
              {translate.isPending ? "Translating…" : "Translate"}
            </button>
          )}
        >
          {(close) =>
            LANGUAGES.map((l) => (
              <MenuItem
                key={l.value}
                onClick={() => {
                  close();
                  setError(null);
                  setNote(null);
                  translate.mutate(
                    { text, to: l.value },
                    {
                      onSuccess: (r) => (r.note ? setNote(r.note) : setResult(r)),
                      onError: (e) => {
                        hide(e);
                        setError(aiErrorMessage(e));
                      },
                    },
                  );
                }}
              >
                To {l.label}
              </MenuItem>
            ))
          }
        </Popover>
      )}
      {error && (
        <p role="alert" className="mt-1 text-[11px] text-danger">
          {error}
        </p>
      )}
      {note && !error && (
        <p role="status" className="mt-1 text-[11px] text-muted-foreground">
          {note}
        </p>
      )}
    </div>
  );
}
