"use client";

import { CheckCircle2, Link2Off, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { publicErrorMessage } from "@/components/public/errors";
import { RatingPicker, scoreLabel } from "@/components/public/rating";
import { Button, Card, EmptyState, Field, Skeleton, Textarea } from "@/components/ui/primitives";
import { usePublicCsat, useSubmitPublicCsat } from "@/hooks/use-portal";
import { bind, fieldError, useFormik, yup } from "@/lib/forms";

/** `?score=N` from the email button; ignored unless it is a whole number from 1 to 5. */
function parseScore(v: string | null): number | null {
  const n = Number(v);
  return Number.isInteger(n) && n >= 1 && n <= 5 ? n : null;
}

/**
 * The rating page the emails link to: `/rate?score=N#token=...`. The token is in the hash
 * so it never reaches server logs; it is read once and removed from the address bar.
 */
export function CsatPage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [presetScore, setPresetScore] = useState<number | null>(null);
  const found = useRef<{ token: string; score: number | null } | null>(null);

  useEffect(() => {
    // StrictMode runs this twice in development: the second run must not see an empty address.
    if (found.current === null) {
      const raw = /(?:^|[#&])token=([^&]+)/.exec(window.location.hash)?.[1] ?? "";
      let t = raw;
      try {
        t = decodeURIComponent(raw);
      } catch {
        /* keep raw */
      }
      const score = parseScore(new URLSearchParams(window.location.search).get("score"));
      found.current = { token: t, score };
      if (t) window.history.replaceState(null, "", window.location.pathname);
    }
    setToken(found.current.token || null);
    setPresetScore(found.current.score);
  }, []);

  if (token === undefined) return <Frame><Skeleton className="h-72 rounded-2xl" /></Frame>;
  if (token === null) {
    return (
      <Frame>
        <Card className="rounded-2xl">
          <EmptyState icon={<Link2Off className="size-6" />} title="This link is incomplete">
            Please open the rating link from your email again.
          </EmptyState>
        </Card>
      </Frame>
    );
  }
  return (
    <Frame>
      <CsatForm token={token} presetScore={presetScore} />
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto w-full max-w-xl animate-rise">{children}</div>;
}

function CsatForm({ token, presetScore }: { token: string; presetScore: number | null }) {
  const info = usePublicCsat(token);
  const submit = useSubmitPublicCsat(token);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formik = useFormik<{ score: number | null; comment: string }>({
    initialValues: { score: presetScore, comment: "" },
    validationSchema: yup.object({
      score: yup
        .number()
        .typeError("Choose a rating from 1 to 5.")
        .required("Choose a rating from 1 to 5.")
        .min(1, "Choose a rating from 1 to 5.")
        .max(5, "Choose a rating from 1 to 5."),
      comment: yup.string().max(2000, "Use at most 2,000 characters."),
    }),
    onSubmit: (v) => {
      setError(null);
      submit.mutate(
        { score: v.score as number, comment: v.comment.trim() || undefined },
        { onSuccess: () => setDone(true), onError: (err) => setError(publicErrorMessage(err)) },
      );
    },
  });
  const score = formik.values.score;

  if (info.isPending) return <Skeleton className="h-72 rounded-2xl" />;
  if (info.isError) {
    return (
      <Card className="rounded-2xl">
        <EmptyState icon={<Link2Off className="size-6" />} title="We can't open this rating link">
          {publicErrorMessage(info.error)}
        </EmptyState>
      </Card>
    );
  }

  const d = info.data;
  const ticket = [d.ticket_label, d.subject].filter(Boolean).join(" · ");

  const alreadyRated = d.already_rated === true || d.score != null;

  if (done || alreadyRated) {
    const shown = done ? score : (d.score ?? score);
    return (
      <Card className="rounded-2xl p-8 text-center shadow-pop">
        <div className="mx-auto grid size-14 place-items-center rounded-2xl bg-success-soft text-success">
          <CheckCircle2 className="size-7" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">
          {done ? "Thank you for your feedback" : "You've already rated this ticket"}
        </h1>
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          {shown ? `Your rating: ${shown} of 5, ${scoreLabel(shown)}. ` : ""}
          {done ? "It helps our team get better." : "You can close this page."}
        </p>
      </Card>
    );
  }

  return (
    <Card className="rounded-2xl p-6 shadow-pop sm:p-8">
      <div className="bg-ai-gradient grid size-12 place-items-center rounded-2xl text-white shadow-[0_8px_20px_-8px_var(--ai-from)]">
        <Sparkles className="size-6" />
      </div>
      <h1 className="mt-5 text-2xl font-semibold tracking-tight">How did we do?</h1>
      {ticket && <p className="mt-1.5 truncate text-sm text-muted-foreground">{ticket}</p>}

      <form noValidate className="mt-6 space-y-5" onSubmit={formik.handleSubmit}>
        <RatingPicker
          value={score}
          onChange={(n) => {
            void formik.setFieldValue("score", n);
            setError(null);
          }}
          disabled={submit.isPending}
        />
        {fieldError(formik, "score") && (
          <p role="alert" className="text-xs text-danger">
            {fieldError(formik, "score")}
          </p>
        )}
        <Field
          label="Anything you'd like to add? (optional)"
          htmlFor="csat-comment"
          error={fieldError(formik, "comment")}
        >
          <Textarea id="csat-comment" rows={4} maxLength={2000} {...bind(formik, "comment")} />
        </Field>
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" size="lg" variant="ai" className="w-full" loading={submit.isPending}>
          Send my rating
        </Button>
      </form>
    </Card>
  );
}
