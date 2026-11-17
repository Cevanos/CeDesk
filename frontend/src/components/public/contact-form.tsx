"use client";

import { CheckCircle2, MailX, Send } from "lucide-react";
import { useState } from "react";

import { publicErrorMessage, publicFieldError } from "@/components/public/errors";
import {
  type FieldValues,
  TypeFields,
  fieldsPayload,
  missingFields,
} from "@/components/public/type-fields";
import { Button, Card, EmptyState, Field, Input, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { usePublicForm, useSubmitPublicForm, useWorkspaceSlug } from "@/hooks/use-portal";

/** `/<workspace>/contact`: a plain "Contact us" form that needs no sign-in. */
export function ContactForm() {
  const ws = useWorkspaceSlug();
  const form = usePublicForm(ws);
  const submit = useSubmitPublicForm(ws);
  const [v, setV] = useState({ name: "", email: "", message: "", type_id: "" });
  const [values, setValues] = useState<FieldValues>({});
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) =>
    setV((x) => ({ ...x, [k]: e.target.value }));

  const typeFields = (form.data?.types ?? []).find((t) => t.id === v.type_id)?.fields ?? [];

  if (form.isPending) return <Skeleton className="mx-auto h-96 max-w-2xl rounded-2xl" />;
  if (form.isError || form.data.enabled === false) {
    return (
      <Card className="mx-auto max-w-xl rounded-2xl">
        <EmptyState icon={<MailX className="size-6" />} title="The contact form is not available">
          {form.isError
            ? publicErrorMessage(form.error)
            : "Please use the email address on our website instead."}
        </EmptyState>
      </Card>
    );
  }

  if (done) {
    return (
      <Card className="mx-auto max-w-xl animate-rise rounded-2xl p-8 text-center shadow-pop">
        <div className="mx-auto grid size-14 place-items-center rounded-2xl bg-success-soft text-success">
          <CheckCircle2 className="size-7" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">Message sent</h1>
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          Thanks for reaching out. We&apos;ll reply to {v.email.trim()} soon.
        </p>
      </Card>
    );
  }

  return (
    <div className="mx-auto max-w-2xl animate-rise">
      <h1 className="text-3xl font-semibold tracking-tight">Contact us</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Tell us what you need and our team will get back to you by email.
      </p>
      <Card className="mt-6 rounded-2xl p-5 shadow-pop sm:p-7">
        <form
          noValidate
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            const next: Record<string, string> = {};
            if (!v.name.trim()) next.name = "Enter your name.";
            if (!/^\S+@\S+\.\S+$/.test(v.email.trim())) next.email = "Enter a valid email address.";
            if (!v.message.trim()) next.message = "Write your message.";
            Object.assign(next, missingFields(typeFields, values));
            setErrs(next);
            if (Object.keys(next).length) return;
            submit.mutate(
              {
                name: v.name.trim(),
                email: v.email.trim(),
                message: v.message.trim(),
                ...(v.type_id ? { type_id: v.type_id, fields: fieldsPayload(typeFields, values) } : {}),
                website: "",
              },
              {
                onSuccess: () => setDone(true),
                onError: (err) =>
                  setErrs({
                    name: publicFieldError(err, "name") ?? "",
                    email: publicFieldError(err, "email") ?? "",
                    message: publicFieldError(err, "message") ?? "",
                    _form: publicErrorMessage(err),
                  }),
              },
            );
          }}
        >
          {/* Spam trap. Hidden from sight and from assistive tech; people never fill it. */}
          <div aria-hidden className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
            <label htmlFor="cf-website">Website</label>
            <input id="cf-website" name="website" tabIndex={-1} autoComplete="off" defaultValue="" />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Your name" htmlFor="cf-name" error={errs.name}>
              <Input
                id="cf-name"
                autoComplete="name"
                maxLength={120}
                value={v.name}
                aria-invalid={!!errs.name}
                onChange={set("name")}
              />
            </Field>
            <Field label="Email address" htmlFor="cf-email" error={errs.email}>
              <Input
                id="cf-email"
                type="email"
                inputMode="email"
                autoComplete="email"
                value={v.email}
                aria-invalid={!!errs.email}
                onChange={set("email")}
              />
            </Field>
          </div>
          {form.data.types.length > 0 && (
            <Field label="Topic" htmlFor="cf-type">
              <Select
                id="cf-type"
                value={v.type_id}
                placeholder="Choose one (optional)"
                options={form.data.types.map((t) => ({ value: t.id, label: t.name }))}
                onChange={(e) => {
                  set("type_id")(e);
                  setValues({});
                }}
              />
            </Field>
          )}
          <TypeFields
            idPrefix="cf"
            fields={typeFields}
            values={values}
            errors={errs}
            onChange={(k, val) => setValues((x) => ({ ...x, [k]: val }))}
          />
          <Field label="Message" htmlFor="cf-message" error={errs.message}>
            <Textarea
              id="cf-message"
              rows={6}
              maxLength={10000}
              value={v.message}
              aria-invalid={!!errs.message}
              onChange={set("message")}
            />
          </Field>
          {errs._form && !errs.name && !errs.email && !errs.message && (
            <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
              {errs._form}
            </p>
          )}
          <Button type="submit" size="lg" className="w-full sm:w-auto" loading={submit.isPending}>
            <Send className="size-4" /> Send message
          </Button>
        </form>
      </Card>
    </div>
  );
}
