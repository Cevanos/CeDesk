import { beforeEach, describe, expect, it } from "vitest";

import { visitorId } from "@/hooks/use-portal";

describe("visitorId", () => {
  beforeEach(() => localStorage.clear());

  it("makes an id the server accepts (8 to 100 characters) and keeps it", () => {
    const first = visitorId();
    expect(first.length).toBeGreaterThanOrEqual(8);
    expect(first.length).toBeLessThanOrEqual(100);
    expect(visitorId()).toBe(first);
  });

  it("replaces a stored value that is too short", () => {
    localStorage.setItem("cdesk.visitor", "abc");
    expect(visitorId().length).toBeGreaterThanOrEqual(8);
  });
});
