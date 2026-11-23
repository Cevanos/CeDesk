import { describe, expect, it } from "vitest";

import { parseIntIn, parseIpList } from "./security-helpers";

describe("parseIpList", () => {
  it("splits on lines and commas and removes duplicates", () => {
    expect(parseIpList("1.2.3.4\n10.0.0.0/8, 1.2.3.4 \n\n")).toEqual(["1.2.3.4", "10.0.0.0/8"]);
  });
  it("returns an empty list for blank text", () => {
    expect(parseIpList("  \n ")).toEqual([]);
  });
});

describe("parseIntIn", () => {
  it("accepts whole numbers in range", () => {
    expect(parseIntIn(" 12 ", 8, 128)).toBe(12);
  });
  it("rejects out of range and non numbers", () => {
    expect(parseIntIn("7", 8, 128)).toBeNull();
    expect(parseIntIn("1.5", 1, 9)).toBeNull();
    expect(parseIntIn("", 1, 9)).toBeNull();
  });
});
