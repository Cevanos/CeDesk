import { describe, expect, it } from "vitest";

import {
  fieldsPayload,
  missingFields,
  typeFieldErrors,
  typeFieldsSchema,
} from "@/components/public/type-fields";
import type { TypeField } from "@/lib/api/types";

const refund: TypeField[] = [
  { key: "order_id", label: "Order ID", kind: "text", options: [], required: true },
  { key: "amount", label: "Refund amount", kind: "number", options: [], required: false },
  { key: "reason", label: "Reason", kind: "select", options: ["Damaged", "Late"], required: false },
];

describe("ticket type fields", () => {
  it("sends numbers as numbers and leaves empty answers out", () => {
    expect(fieldsPayload(refund, { order_id: " A-1 ", amount: "499.5", reason: "" })).toEqual({
      order_id: "A-1",
      amount: 499.5,
    });
  });

  it("keeps a number field as text when it is not a number", () => {
    expect(fieldsPayload(refund, { amount: "abc" })).toEqual({ amount: "abc" });
  });

  it("asks only for the required fields that are empty", () => {
    expect(missingFields(refund, { order_id: "  " })).toEqual({
      "fields.order_id": "Enter Order ID.",
    });
    expect(missingFields(refund, { order_id: "A-1" })).toEqual({});
  });

  it("validates required and numeric fields with Yup", async () => {
    const schema = typeFieldsSchema(refund);
    await expect(schema.validate({ order_id: "A-1", amount: "12" })).resolves.toBeTruthy();
    await expect(schema.validate({ order_id: " " })).rejects.toThrow("Enter Order ID.");
    await expect(schema.validate({ order_id: "A", amount: "abc" })).rejects.toThrow(
      "Refund amount must be a number.",
    );
  });

  it("flattens touched Formik field errors", () => {
    expect(
      typeFieldErrors({
        errors: { fields: { order_id: "Enter Order ID.", amount: "bad" } },
        touched: { fields: { order_id: true } },
      }),
    ).toEqual({ "fields.order_id": "Enter Order ID." });
  });
});
