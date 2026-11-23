import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { normalizeMe, validate } from "@/lib/api/schemas";
import * as monitoring from "@/lib/monitoring";

afterEach(() => vi.restoreAllMocks());

describe("normalizeMe", () => {
  const base = { name: "Ada", email: "a@x.io", role: "agent" };

  it("flattens object-shaped workspace and status", () => {
    const me = normalizeMe({
      ...base,
      id: "m1",
      workspace: { name: "Acme", slug: "acme" },
      status: { status: "available" },
    });
    expect(me).toMatchObject({
      id: "m1",
      workspace_name: "Acme",
      workspace: "acme",
      status: "available",
    });
  });

  it("unwraps the real /me shape { member, workspace }", () => {
    const me = normalizeMe({
      member: { id: "m9", name: "Ritesh", email: "r@x.io", role: "admin", is_active: true },
      workspace: { id: "w1", name: "Acme Store", slug: "acme-store" },
    });
    expect(me).toMatchObject({
      id: "m9",
      role: "admin",
      workspace_name: "Acme Store",
      workspace: "acme-store",
    });
  });

  it("accepts string workspace and member_id", () => {
    const me = normalizeMe({ ...base, member_id: "m2", workspace: "acme", status: "offline" });
    expect(me).toMatchObject({ id: "m2", workspace: "acme", status: "offline" });
  });

  it("tolerates a null status", () => {
    expect(normalizeMe({ ...base, id: "m3", status: null }).status).toBeNull();
  });
});

describe("validate", () => {
  const schema = z.object({ id: z.string() });

  it("returns parsed data when it matches", () => {
    expect(validate(schema, { id: "1" }, "x")).toEqual({ id: "1" });
  });

  it("reports drift but still returns the raw value", () => {
    const spy = vi.spyOn(monitoring, "reportError").mockImplementation(() => {});
    const raw = { id: 5 };
    expect(validate(schema, raw, "GET /x")).toBe(raw);
    expect(spy).toHaveBeenCalledOnce();
  });
});
