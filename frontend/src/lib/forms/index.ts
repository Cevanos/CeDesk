import type { FormikErrors, FormikProps } from "formik";
import type { Schema } from "yup";

/**
 * Formik helpers shared by every form.
 *
 * Validation is Yup, run by Formik. Errors only show once a field has been touched or the
 * form was submitted (Formik marks every field touched on submit), so a pristine form never
 * shouts at the user.
 */
export { Formik, Form, useFormik, useFormikContext, FieldArray } from "formik";
export type { FormikProps, FormikHelpers, FormikErrors } from "formik";
export * as yup from "yup";

type AnyForm = Pick<FormikProps<never>, never> & {
  values: unknown;
  errors: unknown;
  touched: unknown;
  handleChange: (e: React.ChangeEvent<unknown>) => void;
  handleBlur: (e: React.FocusEvent<unknown>) => void;
};

function get(obj: unknown, path: string): unknown {
  return path
    .split(/[.[\]]+/)
    .filter(Boolean)
    .reduce<unknown>((o, k) => (o == null ? undefined : (o as Record<string, unknown>)[k]), obj);
}

/** The error to show under a field: only after it was touched / the form submitted. */
export function fieldError(form: AnyForm, name: string): string | undefined {
  const e = get(form.errors, name);
  return get(form.touched, name) && typeof e === "string" ? e : undefined;
}

/**
 * Props to spread on an input: name, value, change/blur handlers and `aria-invalid`.
 * `<Input id="email" {...bind(form, "email")} />`
 */
export function bind(form: AnyForm, name: string) {
  const v = get(form.values, name);
  return {
    name,
    value: (v ?? "") as string,
    onChange: form.handleChange as React.ChangeEventHandler<HTMLInputElement | HTMLTextAreaElement>,
    onBlur: form.handleBlur as React.FocusEventHandler<HTMLInputElement | HTMLTextAreaElement>,
    "aria-invalid": fieldError(form, name) ? (true as const) : undefined,
  };
}

/** Props for the custom `Select` (it reports `{ target: { name, value } }`). */
export function bindSelect(form: AnyForm, name: string) {
  const v = get(form.values, name);
  return {
    name,
    value: (v ?? "") as string,
    onChange: form.handleChange as unknown as (e: {
      target: { name?: string; value: string };
    }) => void,
    onBlur: () => (form as unknown as FormikProps<never>).setFieldTouched(name as never, true),
    "aria-invalid": fieldError(form, name) ? (true as const) : undefined,
  };
}

/**
 * A Formik `validate` that runs a Yup schema chosen from the current values. Use it when
 * the shape depends on the form itself (a ticket type decides which extra fields exist).
 */
export function yupValidate<V extends object>(
  schemaFor: (values: V) => Schema,
): (values: V) => Promise<FormikErrors<V>> {
  return async (values) => {
    const { validateYupSchema, yupToFormErrors } = await import("formik");
    try {
      await validateYupSchema(values, schemaFor(values));
      return {};
    } catch (e) {
      return yupToFormErrors<V>(e);
    }
  };
}
