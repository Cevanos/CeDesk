import { afterEach, describe, expect, it, vi } from "vitest";

import { hardNavigate } from "@/lib/navigation";

describe("hardNavigate", () => {
  const assign = vi.fn();
  const original = window.location;

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: original });
    assign.mockClear();
  });

  function at(origin: string) {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { origin, assign },
    });
  }

  it.each([
    ["/login", "/login"],
    ["/login?next=%2Ftickets", "/login?next=%2Ftickets"],
    ["/tickets/5#reply", "/tickets/5#reply"],
  ])("goes to %s on this site", (path, expected) => {
    at("https://desk.example.com");
    hardNavigate(path);
    expect(assign).toHaveBeenCalledWith(expected);
  });

  it.each(["https://evil.example/x", "//evil.example/x", "/\t/evil.example"])(
    "sends %s to / instead of another site",
    (path) => {
      at("https://desk.example.com");
      hardNavigate(path);
      expect(assign).toHaveBeenCalledWith("/");
    },
  );
});
