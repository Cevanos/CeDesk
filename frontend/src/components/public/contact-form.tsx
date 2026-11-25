"use client";

import { CheckCircle2, MailX, Send, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { publicErrorMessage, publicFieldError } from "@/components/public/errors";
import {
  TypeFields,
  fieldsPayload,
  typeFieldErrors,
  typeFieldsSchema,
} from "@/components/public/type-fields";
import { AiBadge, AiMark } from "@/components/ui/ai";
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui/primitives";
import { usePublicForm, useSubmitPublicForm, useWorkspaceSlug } from "@/hooks/use-portal";
import { bind, bindSelect, fieldError, useFormik, yup, yupValidate } from "@/lib/forms";

interface Values {
  name: string;
  email: string;
  message: string;
  type_id: string;
  fields: Record<string, string>;
}

const baseShape = {
  name: yup.string().trim().required("Enter your name.").max(120, "Use at most 120 characters."),
  email: yup
    .string()
    .trim()
    .required("Enter a valid email address.")
    .email("Enter a valid email address."),
  message: yup
    .string()
    .trim()
    .required("Write your message.")
    .max(10000, "Use at most 10,000 characters."),
  type_id: yup.string(),
};

/** `/<workspace>/contact`: a plain "Contact us" form that needs no sign-in. */
export function ContactForm() {
  const ws = useWorkspaceSlug();
  const form = usePublicForm(ws);
  const submit = useSubmitPublicForm(ws);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [formError, setFormError] = useState("");
  const types = form.data?.types;
  const fieldsOf = (typeId: string) => (types ?? []).find((t) => t.id === typeId)?.fields ?? [];

  const formik = useFormik<Values>({
    initialValues: { name: "", email: "", message: "", type_id: "", fields: {} },
    // The chosen topic decides which extra fields exist, so the schema follows the values.
    validate: yupValidate<Values>((v) =>
      yup.object({ ...baseShape, fields: typeFieldsSchema(fieldsOf(v.type_id)) }),
    ),
    onSubmit: (v, helpers) => {
      setFormError("");
      submit.mutate(
        {
          name: v.name.trim(),
          email: v.email.trim(),
          message: v.message.trim(),
          ...(v.type_id
            ? { type_id: v.type_id, fields: fieldsPayload(fieldsOf(v.type_id), v.fields) }
            : {}),
          website: "",
        },
        {
          onSuccess: () => setSentTo(v.email.trim()),
          onError: (err) => {
            const errs: Record<string, string> = {};
            for (const k of ["name", "email", "message"] as const) {
              const m = publicFieldError(err, k);
              if (m) errs[k] = m;
            }
            helpers.setErrors(errs);
            if (!Object.keys(errs).length) setFormError(publicErrorMessage(err));
          },
        },
      );
    },
  });

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

  if (sentTo !== null) {
    return (
      <Card className="mx-auto max-w-xl animate-rise rounded-2xl p-8 text-center shadow-pop">
        <div className="mx-auto grid size-14 place-items-center rounded-2xl bg-success-soft text-success">
          <CheckCircle2 className="size-7" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">Message sent</h1>
        <p role="status" className="mt-2 text-sm text-muted-foreground">
          Thanks for reaching out. We&apos;ll reply to {sentTo} soon.
        </p>
      </Card>
    );
  }

  const typeFields = fieldsOf(formik.values.type_id);

  return (
    <div className="mx-auto max-w-2xl animate-rise">
      <div className="flex items-center gap-2.5">
        <AiMark className="size-9 rounded-xl" />
        <AiBadge>Smart routing</AiBadge>
      </div>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight">Contact us</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Tell us what you need and our team will get back to you by email. Your message is read,
        prioritised and routed to the right person automatically.
      </p>
      <Card className="mt-6 rounded-2xl p-5 shadow-pop sm:p-7">
        <form noValidate className="space-y-4" onSubmit={formik.handleSubmit}>
          {/* Spam trap. Hidden from sight and from assistive tech; people never fill it. */}
          <div aria-hidden className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
            <label htmlFor="cf-website">Website</label>
            <input
              id="cf-website"
              name="website"
              tabIndex={-1}
              autoComplete="off"
              defaultValue=""
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Your name" htmlFor="cf-name" error={fieldError(formik, "name")}>
              <Input id="cf-name" autoComplete="name" maxLength={120} {...bind(formik, "name")} />
            </Field>
            <Field label="Email address" htmlFor="cf-email" error={fieldError(formik, "email")}>
              <Input
                id="cf-email"
                type="email"
                inputMode="email"
                autoComplete="email"
                {...bind(formik, "email")}
              />
            </Field>
          </div>
          {form.data.types.length > 0 && (
            <Field label="Topic" htmlFor="cf-type">
              <Select
                id="cf-type"
                {...bindSelect(formik, "type_id")}
                placeholder="Choose one (optional)"
                options={form.data.types.map((t) => ({ value: t.id, label: t.name }))}
                onChange={(e) => {
                  void formik.setValues({
                    ...formik.values,
                    type_id: e.target.value,
                    // Every field key present, so a submit marks them all touched.
                    fields: Object.fromEntries(fieldsOf(e.target.value).map((f) => [f.key, ""])),
                  });
                }}
              />
            </Field>
          )}
          <TypeFields
            idPrefix="cf"
            fields={typeFields}
            values={formik.values.fields}
            errors={typeFieldErrors(formik)}
            onChange={(k, val) => void formik.setFieldValue(`fields.${k}`, val)}
          />
          <Field label="Message" htmlFor="cf-message" error={fieldError(formik, "message")}>
            <Textarea id="cf-message" rows={6} maxLength={10000} {...bind(formik, "message")} />
          </Field>
          {formError && (
            <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">
              {formError}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button
              type="submit"
              size="lg"
              variant="ai"
              className="w-full sm:w-auto"
              loading={submit.isPending}
            >
              <Send className="size-4" /> Send message
            </Button>
            <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5" aria-hidden /> Private and secure
            </p>
          </div>
        </form>
      </Card>
    </div>
  );
}
