import { describe, expect, it } from "vitest";

import { filtersFromParams } from "@/lib/api/ticket-filters";

const params = (s: string) => new URLSearchParams(s);

describe("filtersFromParams", () => {
  it("has sensible defaults", () => {
    expect(filtersFromParams(params(""))).toEqual({
      status: "open",
      scope: "everyone",
      priority: "",
      team: "",
      q: "",
      imported: "",
      sort: "priority",
    });
  });

  it("reads valid values", () => {
    expect(
      filtersFromParams(
        params("status=resolved&scope=mine&priority=urgent&sort=newest&team=t1&q=refund"),
      ),
    ).toEqual({
      status: "resolved",
      scope: "mine",
      priority: "urgent",
      team: "t1",
      q: "refund",
      imported: "",
      sort: "newest",
    });
  });

  it("ignores junk so it never reaches the API", () => {
    const f = filtersFromParams(
      params("status=deleted&scope=root&priority=%3Cscript%3E&sort=random"),
    );
    expect(f).toMatchObject({ status: "open", scope: "everyone", priority: "", sort: "priority" });
  });
});
