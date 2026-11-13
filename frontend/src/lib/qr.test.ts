import { describe, expect, it } from "vitest";

import { formatWord, qrMatrix } from "@/lib/qr";

describe("qrMatrix", () => {
  it("makes a version 1 code (21 modules) for short text", () => {
    const m = qrMatrix("hello");
    expect(m).toHaveLength(21);
    expect(m![0]).toHaveLength(21);
  });

  it("grows with the text and draws the three finder patterns and the timing row", () => {
    const m = qrMatrix("otpauth://totp/CDesk:a@b.c?secret=JBSWY3DPEHPK3PXP&issuer=CDesk")!;
    const n = m.length;
    expect((n - 17) % 4).toBe(0);
    const at = (x: number, y: number) => m[y]?.[x];
    for (const [x = 0, y = 0] of [
      [0, 0],
      [n - 7, 0],
      [0, n - 7],
    ]) {
      expect(at(x, y)).toBe(true);
      expect(at(x + 3, y + 3)).toBe(true);
      expect(at(x + 1, y + 1)).toBe(false);
    }
    for (let i = 8; i < n - 8; i++) expect(at(i, 6)).toBe(i % 2 === 0);
  });

  it("returns null when the text cannot fit", () => {
    expect(qrMatrix("x".repeat(400))).toBeNull();
  });

  it("is deterministic", () => {
    expect(qrMatrix("abc")).toEqual(qrMatrix("abc"));
  });

  it("matches the published format word for error correction M, mask 0", () => {
    expect(formatWord(0).toString(2).padStart(15, "0")).toBe("101010000010010");
  });
});
