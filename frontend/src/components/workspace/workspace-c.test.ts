import { describe, expect, it } from "vitest";

import { checksSummary } from "@/components/workspace/email-checks";
import { helpfulPercent } from "@/components/workspace/help-center-view";
import { cleanDomain, validateDomain } from "@/components/workspace/portal-settings-view";
import { topicLabel } from "@/components/workspace/topics-view";
import type { EmailAddress } from "@/lib/api/types";

describe("domains", () => {
  it("strips a leading @", () => expect(cleanDomain("@@shop.in")).toBe("shop.in"));
  it.each(["example.com", "mail.shop.co.in"])("accepts %s", (d) =>
    expect(validateDomain(d)).toBeNull(),
  );
  it.each(["nope", "a b.com", "-x.com", "x..com"])("rejects %s", (d) =>
    expect(validateDomain(d)).toMatch(/isn't a domain/),
  );
});

describe("helpfulPercent", () => {
  it("is null with no votes", () => expect(helpfulPercent(0, 0)).toBeNull());
  it("rounds the share of yes", () => expect(helpfulPercent(2, 1)).toBe(67));
});

describe("topicLabel", () => {
  it("makes a readable name", () => expect(topicLabel("order_status")).toBe("Order status"));
});

describe("checksSummary", () => {
  const base: EmailAddress = {
    id: "1",
    address: "a@b.in",
    name: "A",
    signature: "",
    is_default: true,
  };
  it("flags a failing DNS record first", () =>
    expect(checksSummary({ ...base, dns: { spf: { status: "fail" } } }).tone).toBe("danger"));
  it("shows waiting", () =>
    expect(checksSummary({ ...base, forwarding: { status: "waiting" } }).tone).toBe("info"));
  it("is green only when everything passes", () => {
    const ok = { status: "pass" };
    expect(
      checksSummary({
        ...base,
        forwarding: { status: "verified" },
        dns: { spf: ok, dkim: ok, dmarc: ok },
      }).tone,
    ).toBe("success");
  });
});
