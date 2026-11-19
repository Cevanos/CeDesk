"use client";

import { useCallback, useState } from "react";

export type FieldErrors = Record<string, string>;

/**
 * Per-field error messages for a form. `clear(key)` drops one field's error (call it when
 * the value changes); the form-level `_form` message goes with it, since it no longer applies.
 */
export function useFieldErrors(initial: FieldErrors = {}) {
  const [errors, setErrors] = useState<FieldErrors>(initial);

  const clear = useCallback(
    (key: string) =>
      setErrors((prev) => (prev[key] || prev._form ? { ...prev, [key]: "", _form: "" } : prev)),
    [],
  );
  const reset = useCallback(() => setErrors({}), []);

  return { errors, setErrors, clear, reset };
}
