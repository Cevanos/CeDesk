"use client";

import { Languages, Sparkles, Undo2, Wand2, X } from "lucide-react";
import { useState } from "react";

import { MenuItem, Popover } from "@/components/ui/overlay";
import { Button, Card } from "@/components/ui/primitives";
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
    <Card className="p-3.5" aria-label="AI summary">
      <div className="flex items-center gap-2">
        <Sparkles className="size-4 text-primary" aria-hidden />
        <h2 className="text-sm font-semibold">AI summary</h2>
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
          <Button size="sm" variant="outline" loading={summarise.isPending} onClick={run}>
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
        <p className="mt-2 text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">{text}</p>
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
    </Card>
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
  const apply = (text: string, old: string) => {
    setBefore(old);
    setBody(text);
  };

  return (
    <div
      className="flex flex-wrap items-center gap-1 px-1 pb-1.5"
      role="group"
      aria-label="AI help"
    >
      {canSuggest && (
        <Button
          size="sm"
          variant="ghost"
          loading={draft.isPending}
          disabled={busy}
          onClick={() => {
            setError(null);
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
                setError(null);
                const old = body;
                improve.mutate(
                  { text: old, tone: t.value },
                  { onSuccess: (text) => apply(text, old), onError: fail },
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
                setError(null);
                const old = body;
                translate.mutate(
                  { text: old, to: l.value },
                  { onSuccess: (r) => apply(r.text, old), onError: fail },
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
  if (!enabled || !text.trim() || text.length > AI_MAX_CHARS) return null;

  return (
    <div className="mt-1.5">
      {result && (
        <div className="rounded-lg border border-border bg-surface-2/70 px-3 py-2">
          <p className="text-sm leading-relaxed wrap-break-word whitespace-pre-wrap">
            {result.text}
          </p>
          <p className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
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
                  translate.mutate(
                    { text, to: l.value },
                    {
                      onSuccess: (r) => setResult(r),
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
    </div>
  );
}
