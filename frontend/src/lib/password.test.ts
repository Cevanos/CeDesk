import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api/client";
import { isWrongCurrentPassword, newPassword, passwordError } from "@/lib/password";

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

  it("takes a field-less message only when it is about the length", () => {
    expect(passwordError(err(422, "Use at least 14 characters.", []), "password")).toBe(
      "Use at least 14 characters.",
    );
    expect(passwordError(err(422, "role_id is only for role custom.", []), "password")).toBe(null);
  });

  it("never puts a wrong current password under the new password", () => {
    const wrong = err(422, "Your current password is not right.", []);
    expect(passwordError(wrong, "new_password")).toBe(null);
    expect(passwordError(wrong, "new_password", { anyMessage: true })).toBe(null);
  });

  it("anyMessage also takes 'choose a different one' for the new password", () => {
    const same = err(422, "Choose a password different from your current one.", []);
    expect(passwordError(same, "new_password")).toBe(null);
    expect(passwordError(same, "new_password", { anyMessage: true })).toBe(same.message);
  });
});

describe("isWrongCurrentPassword", () => {
  const err = (status: number, message: string) =>
    new ApiError({ status, code: "Invalid", message, details: [] });

  it("is true for the API's wrong-current-password 422", () => {
    expect(isWrongCurrentPassword(err(422, "Your current password is not right."))).toBe(true);
  });

  it("is false for other messages, other statuses and other errors", () => {
    expect(isWrongCurrentPassword(err(422, "Use at least 10 characters."))).toBe(false);
    expect(
      isWrongCurrentPassword(err(422, "Choose a password different from your current one.")),
    ).toBe(false);
    expect(isWrongCurrentPassword(err(403, "Your current password is not right."))).toBe(false);
    expect(isWrongCurrentPassword(new Error("boom"))).toBe(false);
  });
});
