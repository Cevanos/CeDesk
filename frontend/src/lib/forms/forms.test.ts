import { describe, expect, it } from "vitest";

import { bind, fieldError } from "@/lib/forms";

const form = {
  values: { a: "x", n: { b: "y" } },
  errors: { a: "bad", n: { b: "worse" } },
  touched: { a: true, n: { b: false } },
  handleChange: () => {},
  handleBlur: () => {},
};

describe("forms helpers", () => {
  it("shows an error only once touched", () => {
    expect(fieldError(form, "a")).toBe("bad");
    expect(fieldError(form, "n.b")).toBeUndefined();
  });
  it("binds value and aria-invalid", () => {
    expect(bind(form, "a")).toMatchObject({ name: "a", value: "x", "aria-invalid": true });
    expect(bind(form, "n.b")["aria-invalid"]).toBeUndefined();
  });
});
