import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "@/components/ui/copy-button";

const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

afterEach(() => {
  if (original) Object.defineProperty(navigator, "clipboard", original);
  else Reflect.deleteProperty(navigator, "clipboard");
  vi.restoreAllMocks();
});

describe("copyText", () => {
  it("uses the clipboard API on secure pages", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    expect(await copyText("https://example.test/hook")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("https://example.test/hook");
  });

  it("still copies on a plain-http page, where navigator.clipboard is missing", async () => {
    setClipboard(undefined);
    const exec = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
    expect(await copyText("https://example.test/hook")).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull(); // the helper cleans up
  });

  it("says so when nothing can copy", async () => {
    setClipboard(undefined);
    Object.defineProperty(document, "execCommand", {
      value: vi.fn().mockReturnValue(false),
      configurable: true,
    });
    expect(await copyText("x")).toBe(false);
  });
});
