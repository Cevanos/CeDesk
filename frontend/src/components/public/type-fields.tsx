"use client";

import { Field, Input, Select } from "@/components/ui/primitives";
import type { TypeField } from "@/lib/api/types";
import { yup } from "@/lib/forms";

/** What the customer typed for a ticket type's own fields, by field key. */
export type FieldValues = Record<string, string>;

/**
 * The question list a ticket type asks (Refund: order id, amount, reason...). It renders
 * one control per field and reports the typed text; `fieldsPayload` turns it into the body.
 */
export function TypeFields({
  idPrefix,
  fields,
  values,
  errors,
  onChange,
}: {
  idPrefix: string;
  fields: TypeField[];
  values: FieldValues;
  errors?: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <>
      {fields.map((f) => {
        const id = `${idPrefix}-f-${f.key}`;
        const label = f.required ? f.label : `${f.label} (optional)`;
        return (
          <Field key={f.key} label={label} htmlFor={id} error={errors?.[`fields.${f.key}`]}>
            {f.kind === "select" ? (
              <Select
                id={id}
                value={values[f.key] ?? ""}
                placeholder="Choose one"
                options={f.options.map((o) => ({ value: o, label: o }))}
                onChange={(e) => onChange(f.key, e.target.value)}
              />
            ) : (
              <Input
                id={id}
                type={f.kind === "number" ? "number" : "text"}
                inputMode={f.kind === "number" ? "decimal" : undefined}
                maxLength={500}
                value={values[f.key] ?? ""}
                aria-invalid={!!errors?.[`fields.${f.key}`]}
                onChange={(e) => onChange(f.key, e.target.value)}
              />
            )}
          </Field>
        );
      })}
    </>
  );
}

/** Which required fields are still empty, as `{ "fields.<key>": message }`. */
export function missingFields(fields: TypeField[], values: FieldValues): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of fields) {
    if (f.required && !(values[f.key] ?? "").trim()) out[`fields.${f.key}`] = `Enter ${f.label}.`;
  }
  return out;
}

/** The `fields` object for the request: numbers as numbers, empty answers left out. */
export function fieldsPayload(
  fields: TypeField[],
  values: FieldValues,
): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const f of fields) {
    const raw = (values[f.key] ?? "").trim();
    if (!raw) continue;
    const n = Number(raw);
    out[f.key] = f.kind === "number" && Number.isFinite(n) ? n : raw;
  }
  return out;
}

/**
 * Yup shape for a ticket type's own fields, keyed by field key (values are strings, as
 * typed). Required fields must be non-blank; number fields must parse as a number.
 */
export function typeFieldsSchema(fields: TypeField[]) {
  const shape: Record<string, yup.StringSchema> = {};
  for (const f of fields) {
    let s = yup.string().trim();
    if (f.kind === "number") {
      s = s.test("number", `${f.label} must be a number.`, (v) => !v || Number.isFinite(Number(v)));
    }
    shape[f.key] = f.required ? s.required(`Enter ${f.label}.`) : s;
  }
  return yup.object(shape);
}

/**
 * Formik's nested `fields` errors, as the flat `{ "fields.<key>": message }` map that
 * `TypeFields` reads. Only fields that were touched (or the whole form once submitted).
 */
export function typeFieldErrors(form: {
  errors: { fields?: unknown };
  touched: { fields?: unknown };
}): Record<string, string> {
  const errs = (form.errors.fields ?? {}) as Record<string, unknown>;
  const touched = (form.touched.fields ?? {}) as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(errs)) {
    if (typeof v === "string" && touched[k]) out[`fields.${k}`] = v;
  }
  return out;
}
