import { describe, expect, it } from "vitest";

import { safeHttpUrl } from "@/lib/safe-url";

describe("safeHttpUrl", () => {
  it.each(["https://example.com/a?b=1", "http://example.com", "  https://example.com  "])(
    "keeps %s",
    (url) => {
      expect(safeHttpUrl(url)).toBe(url.trim());
    },
  );

  it.each([
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    " \tjavascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.example.com",
    "/relative/path",
    "https://",
    "",
    "   ",
  ])("rejects %j", (url) => {
    expect(safeHttpUrl(url)).toBeNull();
  });

  it.each([undefined, null, 42, {}, ["https://example.com"]])("rejects non-string %j", (v) => {
    expect(safeHttpUrl(v)).toBeNull();
  });
});
