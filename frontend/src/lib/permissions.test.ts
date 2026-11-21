import { describe, expect, it } from "vitest";

import { can, canManageCampaigns } from "@/lib/permissions";

describe("can()", () => {
  it("gives light agents notes only", () => {
    const p = can("light_agent");
    expect(p.note).toBe(true);
    expect(p.reply).toBe(false);
    expect(p.createTicket).toBe(false);
    expect(p.manageSettings).toBe(false);
  });

  it("lets agents work tickets but not assign others or manage settings", () => {
    const p = can("agent");
    expect(p).toMatchObject({ reply: true, resolve: true, createTicket: true, assignSelf: true });
    expect(p.assignOthers).toBe(false);
    expect(p.manageSettings).toBe(false);
  });

  it("lets supervisors assign others", () => {
    expect(can("supervisor").assignOthers).toBe(true);
    expect(can("supervisor").manageSettings).toBe(false);
  });

  it("reserves settings for admins", () => {
    expect(can("admin").manageSettings).toBe(true);
  });

  it("grants nothing while the role is unknown", () => {
    const p = can(undefined);
    expect(Object.values(p).every((v) => v === false)).toBe(true);
  });
});

describe("canManageCampaigns", () => {
  it("follows the manage_campaigns action only", () => {
    const me = (actions: string[]) => ({ access: { actions } }) as never;
    expect(canManageCampaigns(me(["manage_campaigns"]))).toBe(true);
    expect(canManageCampaigns(me(["reply"]))).toBe(false);
    expect(canManageCampaigns(undefined)).toBe(false);
    // An Admin role alone is not enough: the action decides.
    expect(canManageCampaigns({ role: "admin" } as never)).toBe(false);
  });
});
