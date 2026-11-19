import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";
import { newPassword, passwordError } from "@/lib/password";

describe("newPassword", () => {
  it("allows 8 characters, the lowest minimum a workspace can set", () => {
    expect(newPassword().safeParse("abcdefgh").success).toBe(true);
  });

  it("refuses 7 characters and over 128", () => {
    expect(newPassword().safeParse("abcdefg").success).toBe(false);
    expect(newPassword().safeParse("a".repeat(129)).success).toBe(false);
  });
});

describe("passwordError", () => {
  const err = (status: number, message: string, details: { field: string; message: string }[]) =>
    new ApiError({ status, code: "Invalid", message, details });

  it("shows the workspace minimum, which comes back with no field", () => {
    expect(passwordError(err(422, "Use at least 14 characters.", []), "password")).toBe(
      "Use at least 14 characters.",
    );
  });

  it("prefers an error named for the field", () => {
    const e = err(422, "Invalid", [{ field: "new_password", message: "Too long" }]);
    expect(passwordError(e, "new_password")).toBe("Too long");
  });

  it("ignores other fields, other statuses and other errors", () => {
    expect(passwordError(err(422, "Invalid", [{ field: "email", message: "x" }]), "password")).toBe(
      null,
    );
    expect(passwordError(err(409, "Taken", []), "password")).toBe(null);
    expect(passwordError(new Error("boom"), "password")).toBe(null);
  });
});
