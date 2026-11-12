import { afterEach, describe, expect, it, vi } from "vitest";

import { newId } from "@/lib/new-id";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => vi.unstubAllGlobals());

describe("newId", () => {
  it("makes a v4 UUID", () => expect(newId()).toMatch(V4));

  it("still works where crypto.randomUUID is missing (plain http)", () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const a = newId();
    expect(a).toMatch(V4);
    expect(newId()).not.toBe(a);
  });
});
